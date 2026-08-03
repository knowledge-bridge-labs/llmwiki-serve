from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import psutil
import pytest
import yaml
from typer.testing import CliRunner

from llmwiki_serve.cli import app as cli_app
from llmwiki_serve.instances import (
    HEALTH_PROBE_TIMEOUT_SECONDS,
    HealthProbeFailure,
    HealthProbeResult,
    InstanceRecord,
    ListenerEndpoint,
    LocalInstanceDiscoveryResult,
    ProcessEntry,
    ProcessEntryDiscoveryResult,
    discover_serve_processes,
    instance_registry_dir,
    list_local_instances,
    listener_endpoint_keys,
    parse_serve_process_candidate,
    process_entry_from_command_line,
    procfs_process_entries,
    ps_process_entries,
    psutil_process_entries,
    read_instance_records,
    register_instance,
    windows_process_entries,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample-wiki"


def test_serve_registers_instance_until_shutdown(monkeypatch, tmp_path: Path) -> None:
    import uvicorn

    state_dir = tmp_path / "state"
    captured: dict[str, Any] = {}

    def fake_run(app: Any, *, host: str, port: int) -> None:
        captured["host"] = host
        captured["port"] = port
        captured["instances"] = [
            item.model_dump()
            for item in list_local_instances(
                state_dir=state_dir,
                probe=False,
                processes=False,
            )
        ]

    monkeypatch.setattr(uvicorn, "run", fake_run)
    result = CliRunner().invoke(
        cli_app,
        ["serve", str(FIXTURE), "--port", "9876"],
        env={"LLMWIKI_SERVE_STATE_DIR": str(state_dir)},
    )

    assert result.exit_code == 0, result.output
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9876
    assert len(captured["instances"]) == 1
    instance = captured["instances"][0]
    assert instance["status"] == "running"
    assert instance["service_verified"] is False
    assert instance["host"] == "127.0.0.1"
    assert instance["port"] == 9876
    assert instance["root"] == str(FIXTURE.resolve())
    assert "process_create_time" not in instance
    assert instance["source_id"] == "sample-packaging-llmwiki"
    assert instance["bundle_id"].startswith("sample-packaging-llmwiki:sha256:")
    assert read_instance_records(state_dir=state_dir) == []


def test_cli_ls_json_reports_health_stale_duplicates_and_overlap(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    root = tmp_path / "vault-a"
    wiki = root / "wiki"
    company = wiki / "company"
    company.mkdir(parents=True)
    records = [
        make_record(pid=101, port=10765, root=wiki, source_id="vault-a", bundle_id="same-bundle"),
        make_record(pid=102, port=11003, root=wiki, source_id="vault-a", bundle_id="same-bundle"),
        make_record(pid=103, port=11001, root=root, source_id="vault-a-root", bundle_id="root"),
        make_record(
            pid=104,
            port=11004,
            root=company,
            source_id="vault-a-company",
            bundle_id="company",
        ),
        make_record(pid=999, port=11099, root=tmp_path / "stale", source_id="stale"),
    ]
    for record in records:
        register_instance(record, state_dir=state_dir)

    monkeypatch.setattr(
        "llmwiki_serve.instances.process_is_running",
        lambda pid: pid != 999,
    )
    monkeypatch.setattr(
        "llmwiki_serve.instances.probe_llmwiki_health",
        lambda record, timeout_seconds: HealthProbeResult(
            source={
                "source_id": record.source_id,
                "bundle_id": record.bundle_id,
                "adapter": record.adapter,
                "implementation": record.implementation,
                "page_count": record.page_count,
                "approved_page_count": record.approved_page_count,
            },
            version="0.2.4",
        ),
    )

    result = CliRunner().invoke(
        cli_app,
        ["ls", "--json", "--state-dir", str(state_dir), "--no-processes"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    instances = {item["port"]: item for item in payload["instances"]}
    assert instances[10765]["status"] == "healthy"
    assert instances[10765]["healthy"] is True
    assert instances[10765]["service_verified"] is True
    assert "duplicate-bundle" in instances[10765]["notes"]
    assert "same-root" in instances[10765]["notes"]
    assert "subfolder-root" in instances[10765]["notes"]
    assert "parent-root" in instances[11001]["notes"]
    assert "subfolder-root" in instances[11004]["notes"]
    assert instances[11099]["status"] == "stale"
    assert instances[11099]["stale"] is True
    assert instances[11099]["service_verified"] is False

    table = CliRunner().invoke(
        cli_app,
        ["ls", "--no-probe", "--state-dir", str(state_dir), "--no-processes"],
    )

    assert table.exit_code == 0, table.output
    assert "PID" in table.output
    assert "127.0.0.1:10765" in table.output
    assert str(wiki.resolve()) not in table.output
    assert ".../vault-a/wiki" in table.output
    assert "duplicate-bundle" in table.output

    pruned = CliRunner().invoke(
        cli_app,
        ["ls", "--json", "--state-dir", str(state_dir), "--prune-stale", "--no-processes"],
    )

    assert pruned.exit_code == 0, pruned.output
    pruned_payload = json.loads(pruned.output)
    assert any(item["port"] == 11099 and item["pruned"] for item in pruned_payload["instances"])
    assert all(record.record.pid != 999 for record in read_instance_records(state_dir=state_dir))


def test_cli_status_aliases_ls(monkeypatch, tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    root = tmp_path / "wiki"
    root.mkdir()
    register_instance(make_record(pid=os.getpid(), port=8765, root=root), state_dir=state_dir)
    monkeypatch.setattr(
        "llmwiki_serve.instances.probe_llmwiki_health",
        lambda record, timeout_seconds: None,
    )

    result = CliRunner().invoke(
        cli_app,
        ["status", "--json", "--state-dir", str(state_dir), "--no-processes"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["instances"][0]["status"] == "unhealthy"
    assert payload["instances"][0]["service_verified"] is False
    assert payload["instances"][0]["root"] == str(root.resolve())
    assert instance_registry_dir(state_dir).exists()


def test_windows_command_line_parsing_extracts_serve_args() -> None:
    command_line = (
        r'"C:\Program Files\Python\python.exe" -m llmwiki_serve.cli serve '
        r'"C:\Example\Wiki Root" --host 0.0.0.0 --port 49217'
    )
    entry = process_entry_from_command_line(
        pid=4321,
        command_line=command_line,
        platform="windows",
    )

    assert entry is not None
    candidate = parse_serve_process_candidate(entry)

    assert candidate is not None
    assert candidate.pid == 4321
    assert candidate.host == "0.0.0.0"
    assert candidate.port == 49217
    assert candidate.root == r"C:\Example\Wiki Root"


def test_posix_command_line_parsing_extracts_serve_args() -> None:
    entry = process_entry_from_command_line(
        pid=4322,
        command_line="uv run llmwiki-serve serve '/tmp/wiki root' --port=49218 --host ::1",
        platform="posix",
    )

    assert entry is not None
    candidate = parse_serve_process_candidate(entry)

    assert candidate is not None
    assert candidate.pid == 4322
    assert candidate.host == "::1"
    assert candidate.port == 49218
    assert candidate.root == "/tmp/wiki root"


def test_command_line_parsing_recognizes_common_launcher_shapes() -> None:
    cases = [
        (
            r'"C:\Tools\Scripts\llmwiki-serve.exe" serve "C:\Wiki Root" --port 49219',
            "windows",
            49219,
        ),
        (
            r'uv run "C:\Tools\Scripts\llmwiki-serve.exe" serve "C:\Wiki Root" --port 49220',
            "windows",
            49220,
        ),
        (
            r'"C:\Python\python.exe" -m llmwiki_serve serve "C:\Wiki Root" --port 49221',
            "windows",
            49221,
        ),
        (
            r'"C:\Python\python.exe" "C:\repo\src\llmwiki_serve\cli.py" serve '
            r'"C:\Wiki Root" --port 49222',
            "windows",
            49222,
        ),
        (
            "uv run llmwiki-serve serve '/tmp/wiki root' --port 49223",
            "posix",
            49223,
        ),
    ]

    for command_line, platform, port in cases:
        entry = process_entry_from_command_line(
            pid=4300 + port,
            command_line=command_line,
            platform=platform,  # type: ignore[arg-type]
        )
        assert entry is not None
        candidate = parse_serve_process_candidate(entry)
        assert candidate is not None, command_line
        assert candidate.port == port


def test_process_discovery_resolves_relative_root_against_cwd(tmp_path: Path) -> None:
    cwd = tmp_path / "Project Root"
    cwd.mkdir()
    entry = ProcessEntry(
        pid=4990,
        argv=("llmwiki-serve", "serve", "wiki root", "--port", "49224"),
        cwd=str(cwd),
    )

    candidate = parse_serve_process_candidate(entry)

    assert candidate is not None
    assert candidate.root == str((cwd / "wiki root").resolve(strict=False))


def test_process_discovery_prefers_listener_pid_for_wrapped_endpoint(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    entries = [
        ProcessEntry(
            pid=5010,
            argv=("uv", "run", "llmwiki-serve", "serve", str(root), "--port", "49225"),
            cwd=str(tmp_path),
        ),
        ProcessEntry(
            pid=5020,
            argv=("llmwiki-serve", "serve", str(root), "--port", "49225"),
            cwd=str(tmp_path),
        ),
    ]

    result = discover_serve_processes(
        process_entries=entries,
        listener_pids_by_endpoint={("127.0.0.1", 49225): 5020},
    )

    assert result.warnings == []
    assert len(result.candidates) == 1
    assert result.candidates[0].pid == 5020
    assert result.candidates[0].listener_pid_verified is True
    assert result.candidates[0].root == str(root.resolve(strict=False))


def test_process_discovery_marks_listener_pid_unverified_when_unlinked(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    result = discover_serve_processes(
        process_entries=[
            ProcessEntry(
                pid=5110,
                argv=("uv", "run", "llmwiki-serve", "serve", str(root), "--port", "49230"),
                cwd=str(tmp_path),
            ),
            ProcessEntry(
                pid=5120,
                argv=("python", "-m", "worker"),
                ppid=9999,
            ),
        ],
        listener_pids_by_endpoint={("127.0.0.1", 49230): 5120},
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].pid == 5120
    assert result.candidates[0].listener_pid_verified is False


def test_process_discovery_verifies_listener_pid_descendant(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    result = discover_serve_processes(
        process_entries=[
            ProcessEntry(
                pid=5210,
                argv=("uv", "run", "llmwiki-serve", "serve", str(root), "--port", "49231"),
                cwd=str(tmp_path),
            ),
            ProcessEntry(
                pid=5220,
                argv=("python", "-m", "uvicorn", "llmwiki_serve.api:app"),
                ppid=5210,
            ),
        ],
        listener_pids_by_endpoint={("127.0.0.1", 49231): 5220},
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].pid == 5220
    assert result.candidates[0].listener_pid_verified is True
    assert result.candidates[0].root == ""


def test_listener_pid_correction_prefers_listener_process_root(tmp_path: Path) -> None:
    wrapper_root = tmp_path / "wrapper root"
    listener_root = tmp_path / "listener root"
    result = discover_serve_processes(
        process_entries=[
            ProcessEntry(
                pid=5230,
                argv=(
                    "uv",
                    "run",
                    "llmwiki-serve",
                    "serve",
                    str(wrapper_root),
                    "--port",
                    "49235",
                ),
                cwd=str(tmp_path),
            ),
            ProcessEntry(
                pid=5240,
                argv=("llmwiki-serve", "serve", str(listener_root), "--port", "49235"),
                cwd=str(tmp_path),
                ppid=5230,
            ),
        ],
        listener_pids_by_endpoint={("127.0.0.1", 49235): 5240},
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].pid == 5240
    assert result.candidates[0].root == str(listener_root.resolve(strict=False))


def test_listener_endpoint_keys_cover_wildcard_and_ipv6_loopback() -> None:
    assert ("127.0.0.1", 49226) in listener_endpoint_keys("0.0.0.0", 49226)
    assert ("127.0.0.1", 49227) in listener_endpoint_keys("::", 49227)
    assert ("::1", 49227) in listener_endpoint_keys("::", 49227)
    assert ("::1", 49228) in listener_endpoint_keys("::1", 49228)


def test_process_discovery_probes_ipv6_wildcard_on_ipv6_loopback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    with ipv6_health_server(
        llmwiki_health_payload(source_id="ipv6-source", version="0.2.6")
    ) as port:
        listener = ListenerEndpoint(pid=5250, host="::", port=port, probe_host="::1")
        monkeypatch.setattr(
            "llmwiki_serve.instances.current_platform_process_entries",
            lambda: ProcessEntryDiscoveryResult(
                entries=[
                    ProcessEntry(
                        pid=5250,
                        argv=(
                            "llmwiki-serve",
                            "serve",
                            str(root),
                            "--host",
                            "::",
                            "--port",
                            str(port),
                        ),
                    )
                ],
                warnings=[],
                listener_pids_by_endpoint={
                    ("127.0.0.1", port): 5250,
                    ("::1", port): 5250,
                },
                listeners_by_endpoint={
                    ("127.0.0.1", port): listener,
                    ("::1", port): listener,
                },
            ),
        )

        instances = list_local_instances(state_dir=tmp_path / "state")

    assert len(instances) == 1
    assert instances[0].healthy is True
    assert instances[0].service_verified is True
    assert instances[0].record.host == "::1"
    assert instances[0].record.source_id == "ipv6-source"


def test_process_discovery_marks_unverified_listener_pid_when_socket_provider_degrades(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    root = tmp_path / "wiki"
    with health_server(llmwiki_health_payload(source_id="orphan-source", version="0.2.5")) as port:
        monkeypatch.setattr(
            "llmwiki_serve.instances.current_platform_process_entries",
            lambda: ProcessEntryDiscoveryResult(
                entries=[
                    ProcessEntry(
                        pid=5030,
                        argv=("llmwiki-serve", "serve", str(root), "--port", str(port)),
                    )
                ],
                warnings=["process discovery degraded: psutil denied access to listener sockets"],
            ),
        )
        result = CliRunner().invoke(
            cli_app,
            ["ls", "--json", "--state-dir", str(state_dir)],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    instance = payload["instances"][0]
    assert instance["pid"] == 5030
    assert instance["status"] == "unhealthy"
    assert instance["service_verified"] is False
    assert "health-local-listener-unverified" in instance["notes"]
    assert "listener-pid-unverified" in instance["notes"]
    assert payload["warnings"] == [
        "process discovery degraded: psutil denied access to listener sockets"
    ]


def test_psutil_provider_collects_current_process_argv_and_cwd() -> None:
    result = psutil_process_entries()
    entries = {entry.pid: entry for entry in result.entries}

    current = entries[os.getpid()]
    assert current.argv
    assert current.cwd
    assert current.create_time is not None


def test_psutil_provider_reports_aggregated_permission_degradation(monkeypatch) -> None:
    class FakeAccessDenied(Exception):
        pass

    class FakeNoSuchProcess(Exception):
        pass

    class FakeZombieProcess(Exception):
        pass

    class DeniedCmdlineProcess:
        pid = 6100

        def cmdline(self) -> list[str]:
            raise FakeAccessDenied()

    class PartialDeniedProcess:
        pid = 6101

        def cmdline(self) -> list[str]:
            return ["llmwiki-serve", "serve", "wiki", "--port", "49232"]

        def cwd(self) -> str:
            raise FakeAccessDenied()

        def create_time(self) -> float:
            raise FakeAccessDenied()

        def ppid(self) -> int:
            raise FakeAccessDenied()

    class FakePsutil:
        NoSuchProcess = FakeNoSuchProcess
        AccessDenied = FakeAccessDenied
        ZombieProcess = FakeZombieProcess
        CONN_LISTEN = "LISTEN"

        @staticmethod
        def process_iter(attrs: list[str]) -> list[object]:
            assert attrs == ["pid"]
            return [DeniedCmdlineProcess(), PartialDeniedProcess()]

        @staticmethod
        def net_connections(kind: str) -> list[object]:
            assert kind == "tcp"
            raise FakeAccessDenied("denied")

    monkeypatch.setattr("llmwiki_serve.instances._PSUTIL", FakePsutil)

    result = psutil_process_entries()

    assert len(result.entries) == 1
    assert set(result.warnings) == {
        "process discovery degraded: psutil denied access to some process command lines",
        "process discovery degraded: psutil denied access to some process cwd values",
        "process discovery degraded: psutil denied access to some process create_time values",
        "process discovery degraded: psutil denied access to some process parent PIDs",
        "process discovery degraded: psutil denied access to listener sockets",
    }


def test_fallback_provider_warnings_do_not_expose_command_output_or_args(monkeypatch) -> None:
    secret = "sk-test-secret --token hidden"

    def failed_provider_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args,
            returncode=1,
            stdout=f"llmwiki-serve serve wiki --api-key {secret}",
            stderr=f"provider stderr included {secret}",
        )

    monkeypatch.setattr("llmwiki_serve.instances.subprocess.run", failed_provider_run)

    windows_result = windows_process_entries()
    posix_result = ps_process_entries()
    warning_text = "\n".join(windows_result.warnings + posix_result.warnings)

    assert windows_result.warnings == [
        "process discovery degraded: Windows process provider failed"
    ]
    assert posix_result.warnings == ["process discovery degraded: POSIX process provider failed"]
    assert secret not in warning_text
    assert "llmwiki-serve serve" not in warning_text
    assert "Get-CimInstance" not in warning_text
    assert "ps -axo" not in warning_text
    assert "stderr" not in warning_text


def test_procfs_provider_reports_capability_warnings_without_raw_details(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    missing_cmdline = proc_root / "100"
    missing_cwd = proc_root / "101"
    missing_cmdline.mkdir(parents=True)
    missing_cwd.mkdir()
    (missing_cwd / "cmdline").write_bytes(b"llmwiki-serve\0serve\0secret root\0--port\049232\0")

    result = procfs_process_entries(proc_root)

    assert result is not None
    assert len(result.entries) == 1
    assert set(result.warnings) == {
        "process discovery degraded: procfs could not read some process command lines",
        "process discovery degraded: procfs could not read some process cwd values",
    }
    warning_text = "\n".join(result.warnings)
    assert "100" not in warning_text
    assert "101" not in warning_text
    assert str(proc_root) not in warning_text
    assert "secret root" not in warning_text


def test_non_serve_command_line_is_not_a_candidate() -> None:
    entry = ProcessEntry(pid=4323, argv=("llmwiki-serve", "ls", "--json"))

    assert parse_serve_process_candidate(entry) is None


def test_cli_ls_json_reports_orphan_from_process_discovery(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    root = tmp_path / "wiki"
    root.mkdir()
    with health_server(llmwiki_health_payload(source_id="orphan-source", version="0.1.9")) as port:
        monkeypatch.setattr(
            "llmwiki_serve.instances.current_platform_process_entries",
            lambda: process_entries_with_listener(
                [
                    ProcessEntry(
                        pid=4242,
                        argv=(
                            "python",
                            "-m",
                            "llmwiki_serve.cli",
                            "serve",
                            str(root),
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(port),
                        ),
                    )
                ],
                pid=4242,
                host="127.0.0.1",
                port=port,
            ),
        )
        result = CliRunner().invoke(
            cli_app,
            ["ls", "--json", "--state-dir", str(state_dir)],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert len(payload["instances"]) == 1
    instance = payload["instances"][0]
    assert instance["pid"] == 4242
    assert instance["port"] == port
    assert instance["status"] == "healthy"
    assert instance["healthy"] is True
    assert instance["service_verified"] is True
    assert instance["registered"] is False
    assert instance["orphan"] is True
    assert instance["source_id"] == "orphan-source"
    assert instance["version"] == "0.1.9"
    assert instance["root"] == str(root)
    assert instance["discovery_source"] == "process"
    assert instance["root_source"] == "process-args"
    assert "orphan" in instance["notes"]
    assert "process-discovered" in instance["notes"]
    assert "listener-pid-unverified" not in instance["notes"]


def test_cli_probe_timeout_option_is_forwarded(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, float] = {}

    def fake_discover(**kwargs: Any) -> LocalInstanceDiscoveryResult:
        captured["timeout"] = kwargs["probe_timeout_seconds"]
        return LocalInstanceDiscoveryResult(instances=[], warnings=[])

    monkeypatch.setattr("llmwiki_serve.cli.discover_local_instances", fake_discover)

    result = CliRunner().invoke(
        cli_app,
        [
            "ls",
            "--json",
            "--state-dir",
            str(tmp_path / "state"),
            "--probe-timeout-seconds",
            "2.75",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["timeout"] == 2.75


def test_cli_probe_timeout_default_matches_contract(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, float] = {}

    def fake_discover(**kwargs: Any) -> LocalInstanceDiscoveryResult:
        captured["timeout"] = kwargs["probe_timeout_seconds"]
        return LocalInstanceDiscoveryResult(instances=[], warnings=[])

    monkeypatch.setattr("llmwiki_serve.cli.discover_local_instances", fake_discover)

    result = CliRunner().invoke(
        cli_app,
        ["status", "--json", "--state-dir", str(tmp_path / "state")],
    )

    assert result.exit_code == 0, result.output
    assert captured["timeout"] == HEALTH_PROBE_TIMEOUT_SECONDS


def test_explicit_probe_port_reports_manual_orphan(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    with health_server(llmwiki_health_payload(source_id="manual-source", version="0.1.9")) as port:
        result = CliRunner().invoke(
            cli_app,
            [
                "ls",
                "--json",
                "--state-dir",
                str(state_dir),
                "--no-processes",
                "--probe-port",
                str(port),
            ],
        )

    assert result.exit_code == 0, result.output
    instance = json.loads(result.output)["instances"][0]
    assert instance["pid"] == 0
    assert instance["port"] == port
    assert instance["registered"] is False
    assert instance["orphan"] is True
    assert instance["source_id"] == "manual-source"
    assert instance["discovery_source"] == "manual-probe"
    assert instance["root_source"] == "unknown"
    assert instance["service_verified"] is True
    assert "manual-probe" in instance["notes"]


def test_process_discovery_dedupes_registered_same_endpoint(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    root = tmp_path / "wiki"
    root.mkdir()
    with health_server(llmwiki_health_payload(source_id="health-source", version="0.2.4")) as port:
        register_instance(make_record(pid=os.getpid(), port=port, root=root), state_dir=state_dir)
        monkeypatch.setattr(
            "llmwiki_serve.instances.current_platform_process_entries",
            lambda: ProcessEntryDiscoveryResult(
                entries=[
                    ProcessEntry(
                        pid=os.getpid(),
                        argv=(
                            "llmwiki-serve",
                            "serve",
                            str(root),
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(port),
                        ),
                    )
                ],
                warnings=[],
            ),
        )
        result = CliRunner().invoke(
            cli_app,
            ["ls", "--json", "--state-dir", str(state_dir)],
        )

    assert result.exit_code == 0, result.output
    instances = json.loads(result.output)["instances"]
    assert len(instances) == 1
    assert instances[0]["port"] == port
    assert instances[0]["registered"] is True
    assert instances[0]["orphan"] is False
    assert instances[0]["service_verified"] is True
    assert instances[0]["source_id"] == "health-source"
    assert instances[0]["version"] == "0.2.4"


def test_registered_record_prefers_listener_pid_when_original_pid_is_reused(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    root = tmp_path / "wiki"
    root.mkdir()
    original_pid = 6010
    listener_pid = 6020
    register_instance(make_record(pid=original_pid, port=49229, root=root), state_dir=state_dir)
    monkeypatch.setattr("llmwiki_serve.instances.process_is_running", lambda pid: True)
    monkeypatch.setattr(
        "llmwiki_serve.instances.probe_llmwiki_health",
        lambda record, timeout_seconds: HealthProbeResult(
            source={
                "source_id": "health-source",
                "bundle_id": "health-bundle",
                "adapter": "llmwiki-markdown",
                "implementation": "llmwiki-markdown",
                "page_count": 5,
                "approved_page_count": 5,
            },
            version="0.2.5",
        ),
    )

    instances = list_local_instances(
        state_dir=state_dir,
        process_entries=[],
        listener_pids_by_endpoint={("127.0.0.1", 49229): listener_pid},
    )

    assert len(instances) == 1
    assert instances[0].record.pid == listener_pid
    assert instances[0].status == "healthy"
    assert "listener-pid-corrected" in instances[0].notes


def test_registered_record_with_create_time_mismatch_is_stale_pid_reused(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    root = tmp_path / "wiki"
    root.mkdir()
    register_instance(
        make_record(pid=6110, port=49233, root=root, process_create_time=100.0),
        state_dir=state_dir,
    )
    monkeypatch.setattr("llmwiki_serve.instances.process_is_running", lambda pid: True)
    monkeypatch.setattr("llmwiki_serve.instances.current_process_create_time", lambda pid: 200.0)

    instances = list_local_instances(state_dir=state_dir, processes=False)

    assert len(instances) == 1
    assert instances[0].status == "stale"
    assert instances[0].stale is True
    assert instances[0].service_verified is False
    assert "pid-reused" in instances[0].notes


def test_old_registry_record_without_create_time_remains_backward_compatible(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    registry_dir = instance_registry_dir(state_dir)
    registry_dir.mkdir(parents=True)
    payload = make_record(pid=6120, port=49234, root=tmp_path / "wiki").model_dump(
        include_process_create_time=True
    )
    payload.pop("process_create_time", None)
    payload["schema_version"] = 1
    path = registry_dir / "127.0.0.1-49234-6120.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    records = read_instance_records(state_dir=state_dir)

    assert len(records) == 1
    assert records[0].record.schema_version == 1
    assert records[0].record.process_create_time is None


def test_non_llmwiki_process_is_not_reported(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    with health_server(llmwiki_health_payload(source_id="ignored", version="0.2.4")) as port:
        monkeypatch.setattr(
            "llmwiki_serve.instances.current_platform_process_entries",
            lambda: ProcessEntryDiscoveryResult(
                entries=[
                    ProcessEntry(
                        pid=4545,
                        argv=("python", "-m", "http.server", str(port)),
                    )
                ],
                warnings=[],
            ),
        )
        result = CliRunner().invoke(
            cli_app,
            ["ls", "--json", "--state-dir", str(state_dir)],
        )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"instances": []}


def test_process_discovery_ignores_non_llmwiki_health(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    with health_server({"status": "ok", "service": "other"}) as port:
        monkeypatch.setattr(
            "llmwiki_serve.instances.current_platform_process_entries",
            lambda: process_entries_with_listener(
                [
                    ProcessEntry(
                        pid=4646,
                        argv=(
                            "llmwiki-serve",
                            "serve",
                            str(tmp_path / "wiki"),
                            "--port",
                            str(port),
                        ),
                    )
                ],
                pid=4646,
                host="127.0.0.1",
                port=port,
            ),
        )
        result = CliRunner().invoke(
            cli_app,
            ["ls", "--json", "--state-dir", str(state_dir)],
        )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"instances": []}


def test_process_discovery_does_not_expose_raw_command_line_arguments(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    with health_server(llmwiki_health_payload(source_id="safe-source", version="0.2.5")) as port:
        monkeypatch.setattr(
            "llmwiki_serve.instances.current_platform_process_entries",
            lambda: process_entries_with_listener(
                [
                    ProcessEntry(
                        pid=4650,
                        argv=(
                            "llmwiki-serve",
                            "serve",
                            str(tmp_path / "wiki"),
                            "--port",
                            str(port),
                            "--redis-url",
                            "redis://user:super-secret@example.invalid/0",
                        ),
                    )
                ],
                pid=4650,
                host="127.0.0.1",
                port=port,
            ),
        )
        result = CliRunner().invoke(
            cli_app,
            ["ls", "--json", "--state-dir", str(state_dir)],
        )

    assert result.exit_code == 0, result.output
    assert "super-secret" not in result.output
    assert "redis://user" not in result.output
    instance = json.loads(result.output)["instances"][0]
    assert instance["source_id"] == "safe-source"


def test_process_discovery_ignores_failed_health(
    monkeypatch,
    tmp_path: Path,
    free_tcp_port: int,
) -> None:
    monkeypatch.setattr(
        "llmwiki_serve.instances.current_platform_process_entries",
        lambda: ProcessEntryDiscoveryResult(
            entries=[
                ProcessEntry(
                    pid=4747,
                    argv=(
                        "llmwiki-serve",
                        "serve",
                        str(tmp_path / "wiki"),
                        "--port",
                        str(free_tcp_port),
                    ),
                )
            ],
            warnings=[],
        ),
    )

    instances = list_local_instances(state_dir=tmp_path / "state", probe_timeout_seconds=0.05)

    assert len(instances) == 1
    instance = instances[0]
    assert instance.record.pid == 4747
    assert instance.record.port == free_tcp_port
    assert instance.status == "unhealthy"
    assert instance.healthy is False
    assert instance.service_verified is False
    assert instance.registered is False
    assert instance.orphan is True
    assert "health-local-listener-unverified" in instance.notes
    assert "service-unverified" in instance.notes


def test_process_discovery_never_probes_nonlocal_argv_without_listener(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def forbidden_urlopen(request: Any, **kwargs: Any) -> Any:
        calls.append(str(getattr(request, "full_url", request)))
        raise AssertionError("process discovery must not probe arbitrary argv hosts")

    monkeypatch.setattr("llmwiki_serve.instances.urlopen", forbidden_urlopen)
    monkeypatch.setattr(
        "llmwiki_serve.instances.current_platform_process_entries",
        lambda: ProcessEntryDiscoveryResult(
            entries=[
                ProcessEntry(
                    pid=4748,
                    argv=(
                        "llmwiki-serve",
                        "serve",
                        str(tmp_path / "wiki"),
                        "--host",
                        "203.0.113.77",
                        "--port",
                        "49236",
                    ),
                )
            ],
            warnings=[],
        ),
    )

    instances = list_local_instances(state_dir=tmp_path / "state", probe_timeout_seconds=5.0)

    assert calls == []
    assert len(instances) == 1
    assert instances[0].status == "unhealthy"
    assert instances[0].service_verified is False
    assert "health-local-listener-unverified" in instances[0].notes
    assert "service-unverified" in instances[0].notes


def test_process_discovery_bounds_verified_listener_probe_latency(
    monkeypatch,
    tmp_path: Path,
) -> None:
    candidate_count = 16
    entries = [
        ProcessEntry(
            pid=4800 + index,
            argv=(
                "llmwiki-serve",
                "serve",
                str(tmp_path / f"wiki-{index}"),
                "--port",
                str(49250 + index),
            ),
        )
        for index in range(candidate_count)
    ]
    listener_pids = {("127.0.0.1", 49250 + index): 4800 + index for index in range(candidate_count)}

    monkeypatch.setattr(
        "llmwiki_serve.instances.current_platform_process_entries",
        lambda: ProcessEntryDiscoveryResult(
            entries=entries,
            warnings=[],
            listener_pids_by_endpoint=listener_pids,
            listeners_by_endpoint={
                endpoint: ListenerEndpoint(
                    pid=pid,
                    host=endpoint[0],
                    port=endpoint[1],
                    probe_host=endpoint[0],
                )
                for endpoint, pid in listener_pids.items()
            },
        ),
    )

    def slow_probe(record: InstanceRecord, *, timeout_seconds: float) -> HealthProbeFailure:
        time.sleep(0.2)
        return HealthProbeFailure("health-timeout")

    monkeypatch.setattr("llmwiki_serve.instances.probe_llmwiki_health_outcome", slow_probe)

    started = time.monotonic()
    instances = list_local_instances(state_dir=tmp_path / "state", probe_timeout_seconds=1.0)
    elapsed = time.monotonic() - started

    assert len(instances) == candidate_count
    assert all(instance.status == "unhealthy" for instance in instances)
    assert elapsed < 1.5


def test_process_discovery_reports_timeout_as_unverified_unhealthy(
    monkeypatch,
    tmp_path: Path,
) -> None:
    with slow_health_server(delay_seconds=0.3) as port:
        monkeypatch.setattr(
            "llmwiki_serve.instances.current_platform_process_entries",
            lambda: process_entries_with_listener(
                [
                    ProcessEntry(
                        pid=4750,
                        argv=(
                            "llmwiki-serve",
                            "serve",
                            str(tmp_path / "wiki"),
                            "--port",
                            str(port),
                        ),
                    )
                ],
                pid=4750,
                host="127.0.0.1",
                port=port,
            ),
        )

        instances = list_local_instances(
            state_dir=tmp_path / "state",
            probe_timeout_seconds=0.05,
        )

    assert len(instances) == 1
    assert instances[0].status == "unhealthy"
    assert instances[0].service_verified is False
    assert "health-timeout" in instances[0].notes
    assert "service-unverified" in instances[0].notes


def test_live_registered_pid_with_failed_health_is_not_promoted_by_process_table(
    monkeypatch,
    tmp_path: Path,
    free_tcp_port: int,
) -> None:
    state_dir = tmp_path / "state"
    root = tmp_path / "wiki"
    root.mkdir()
    register_instance(
        make_record(pid=os.getpid(), port=free_tcp_port, root=root), state_dir=state_dir
    )
    monkeypatch.setattr(
        "llmwiki_serve.instances.probe_llmwiki_health",
        lambda record, timeout_seconds: None,
    )
    monkeypatch.setattr(
        "llmwiki_serve.instances.current_platform_process_entries",
        lambda: ProcessEntryDiscoveryResult(
            entries=[
                ProcessEntry(
                    pid=os.getpid(),
                    argv=("python", "-m", "http.server", str(free_tcp_port)),
                )
            ],
            warnings=[],
        ),
    )

    result = CliRunner().invoke(
        cli_app,
        ["ls", "--json", "--state-dir", str(state_dir)],
    )

    assert result.exit_code == 0, result.output
    instances = json.loads(result.output)["instances"]
    assert len(instances) == 1
    assert instances[0]["status"] == "unhealthy"
    assert instances[0]["healthy"] is False
    assert instances[0]["registered"] is True
    assert instances[0]["orphan"] is False


def test_cli_ls_json_reports_process_discovery_degraded_warning(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "llmwiki_serve.instances.current_platform_process_entries",
        lambda: ProcessEntryDiscoveryResult(
            entries=[],
            warnings=["process discovery degraded: provider unavailable"],
        ),
    )

    result = CliRunner().invoke(
        cli_app,
        ["ls", "--json", "--state-dir", str(tmp_path / "state")],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "instances": [],
        "warnings": ["process discovery degraded: provider unavailable"],
    }


def test_real_server_process_discovery_empty_registry_arbitrary_port_listener_pid(
    tmp_path: Path,
) -> None:
    executable = llmwiki_serve_executable()
    if not executable.exists():
        pytest.skip(f"console script not available: {executable}")
    root = tmp_path / "Wiki Root"
    root.mkdir()
    (root / "index.md").write_text("# E2E Wiki\n\nArbitrary port discovery.\n", encoding="utf-8")
    server_state_dir = tmp_path / "server-state"
    empty_state_dir = tmp_path / "empty-state"
    port = unused_tcp_port()
    env = os.environ.copy()
    env["LLMWIKI_SERVE_STATE_DIR"] = str(server_state_dir)
    env["LLMWIKI_SERVE_IO_LOG"] = "off"
    process = subprocess.Popen(
        [
            str(executable),
            "serve",
            str(root),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--io-log",
            "off",
        ],
        cwd=str(tmp_path),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        wait_for_server_health(port, timeout_seconds=20.0)
        listener_pid = listener_pid_for_port(port)
        assert listener_pid is not None
        result = subprocess.run(
            [
                str(executable),
                "ls",
                "--json",
                "--state-dir",
                str(empty_state_dir),
                "--probe-timeout-seconds",
                "0.5",
            ],
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

        assert result.returncode == 0, result.stderr + result.stdout
        payload = json.loads(result.stdout)
        matches = [item for item in payload["instances"] if item["port"] == port]
        assert len(matches) == 1
        instance = matches[0]
        assert instance["pid"] == listener_pid
        assert instance["registered"] is False
        assert instance["orphan"] is True
        assert instance["service_verified"] is True
        assert instance["root"] == str(root.resolve(strict=False))
        assert instance["root_source"] == "process-args"
        assert "listener-pid-unverified" not in instance["notes"]
    finally:
        terminate_process_tree(process.pid)


def test_ci_release_smoke_command_does_not_depend_on_shell_globs() -> None:
    workflow_path = Path(__file__).parents[1] / ".github" / "workflows" / "ci.yml"
    if not workflow_path.exists():
        pytest.skip("repository checkout only; .github workflows are omitted from the sdist")
    workflow = workflow_path.read_text(encoding="utf-8")
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")

    assert "dist/*.whl" not in workflow
    assert "dist/*.tar.gz" not in workflow
    assert "scripts/release_smoke.py --dist-dir dist --allow-network-install" in workflow
    assert "scripts/release_smoke.py --wheel dist/*.whl --sdist dist/*.tar.gz" not in readme
    assert "scripts/release_smoke.py --dist-dir dist" in readme


def test_publish_workflow_limits_oidc_to_minimal_publish_job() -> None:
    workflow_path = Path(__file__).parents[1] / ".github" / "workflows" / "publish.yml"
    if not workflow_path.exists():
        pytest.skip("repository checkout only; .github workflows are omitted from the sdist")
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    jobs = workflow["jobs"]
    build_job = jobs["build"]
    publish_job = jobs["publish"]

    assert build_job["permissions"] == {"contents": "read"}
    assert "id-token" not in build_job["permissions"]
    assert publish_job["needs"] == "build"
    assert publish_job["environment"] == "pypi"
    assert publish_job["permissions"] == {"contents": "read", "id-token": "write"}

    build_steps = json.dumps(build_job["steps"], sort_keys=True)
    publish_steps = json.dumps(publish_job["steps"], sort_keys=True)
    assert "actions/checkout" in build_steps
    assert "uv sync --extra dev --extra vector --locked" in build_steps
    assert "uv build" in build_steps
    assert "twine check dist/*" in build_steps
    assert "actions/upload-artifact" in build_steps
    assert "dist/*.whl" in build_steps
    assert "dist/*.tar.gz" in build_steps
    assert "dist-sha256.json" in build_steps
    assert "scripts/release_dist_manifest.py write" in build_steps
    assert '"retention-days": 7' in build_steps

    publish_command = next(
        step["run"] for step in publish_job["steps"] if step.get("name") == "Publish to PyPI"
    )
    assert publish_command.split() == [
        "uv",
        "publish",
        "--trusted-publishing",
        "always",
        "release-artifact/dist/*.whl",
        "release-artifact/dist/*.tar.gz",
    ]
    assert "actions/download-artifact" in publish_steps
    assert "release-artifact/dist/*" not in publish_command.split()
    assert "actions/checkout" not in publish_steps
    assert "uv sync" not in publish_steps
    assert "uv run" not in publish_steps
    assert "pytest" not in publish_steps
    assert "ruff" not in publish_steps
    assert "mypy" not in publish_steps


def process_entries_with_listener(
    entries: list[ProcessEntry],
    *,
    pid: int,
    host: str,
    port: int,
    probe_host: str | None = None,
) -> ProcessEntryDiscoveryResult:
    listener = ListenerEndpoint(
        pid=pid,
        host=host,
        port=port,
        probe_host=probe_host or host,
    )
    return ProcessEntryDiscoveryResult(
        entries=entries,
        warnings=[],
        listener_pids_by_endpoint={(host, port): pid},
        listeners_by_endpoint={(host, port): listener},
    )


def make_record(
    *,
    pid: int,
    port: int,
    root: Path,
    source_id: str = "source",
    bundle_id: str = "bundle",
    process_create_time: float | None = None,
) -> InstanceRecord:
    return InstanceRecord(
        pid=pid,
        host="127.0.0.1",
        port=port,
        root=str(root.resolve(strict=False)),
        url=f"http://127.0.0.1:{port}",
        source_id=source_id,
        bundle_id=bundle_id,
        adapter="llmwiki-markdown",
        implementation="llmwiki-markdown",
        page_count=67,
        approved_page_count=67,
        started_at="2026-07-28T00:00:00Z",
        process_create_time=process_create_time,
    )


def llmwiki_health_payload(*, source_id: str, version: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "llmwiki-serve",
        "version": version,
        "source": {
            "source_id": source_id,
            "bundle_id": f"{source_id}:sha256:abc",
            "adapter": "llmwiki-markdown",
            "implementation": "llmwiki-markdown",
            "page_count": 5,
            "approved_page_count": 4,
        },
    }


@contextmanager
def health_server(payload: dict[str, Any]) -> Iterator[int]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def ipv6_health_server(payload: dict[str, Any]) -> Iterator[int]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
        address_family = socket.AF_INET6

    try:
        server = IPv6ThreadingHTTPServer(("::1", 0), Handler)
    except OSError as exc:
        pytest.skip(f"IPv6 loopback is unavailable: {exc}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@contextmanager
def slow_health_server(*, delay_seconds: float) -> Iterator[int]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            time.sleep(delay_seconds)
            body = json.dumps(llmwiki_health_payload(source_id="slow", version="0.2.5")).encode(
                "utf-8"
            )
            try:
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def llmwiki_serve_executable() -> Path:
    name = "llmwiki-serve.exe" if os.name == "nt" else "llmwiki-serve"
    return Path(sys.executable).with_name(name)


def unused_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_server_health(port: int, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
                if response.getcode() == 200:
                    return
        except (OSError, TimeoutError, URLError) as exc:
            last_error = exc
        time.sleep(0.1)
    raise AssertionError(f"server on port {port} did not become healthy: {last_error}")


def listener_pid_for_port(port: int) -> int | None:
    for connection in psutil.net_connections(kind="tcp"):
        if connection.status != psutil.CONN_LISTEN or connection.pid is None:
            continue
        local_address = connection.laddr
        if getattr(local_address, "port", None) == port:
            return int(connection.pid)
    return None


def terminate_process_tree(pid: int) -> None:
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    processes = parent.children(recursive=True) + [parent]
    for process in processes:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            continue
    _, alive = psutil.wait_procs(processes, timeout=5)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            continue
    psutil.wait_procs(alive, timeout=5)
