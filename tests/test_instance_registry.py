from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

from llmwiki_serve.cli import app as cli_app
from llmwiki_serve.instances import (
    HealthProbeResult,
    InstanceRecord,
    ProcessEntry,
    ProcessEntryDiscoveryResult,
    instance_registry_dir,
    list_local_instances,
    parse_serve_process_candidate,
    process_entry_from_command_line,
    read_instance_records,
    register_instance,
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
    assert instance["host"] == "127.0.0.1"
    assert instance["port"] == 9876
    assert instance["root"] == str(FIXTURE.resolve())
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
    assert "duplicate-bundle" in instances[10765]["notes"]
    assert "same-root" in instances[10765]["notes"]
    assert "subfolder-root" in instances[10765]["notes"]
    assert "parent-root" in instances[11001]["notes"]
    assert "subfolder-root" in instances[11004]["notes"]
    assert instances[11099]["status"] == "stale"
    assert instances[11099]["stale"] is True

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
            lambda: ProcessEntryDiscoveryResult(
                entries=[
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
                warnings=[],
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
    assert instance["registered"] is False
    assert instance["orphan"] is True
    assert instance["source_id"] == "orphan-source"
    assert instance["version"] == "0.1.9"
    assert instance["root"] == str(root)
    assert instance["discovery_source"] == "process"
    assert instance["root_source"] == "process-args"
    assert "orphan" in instance["notes"]
    assert "process-discovered" in instance["notes"]


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
    assert instances[0]["source_id"] == "health-source"
    assert instances[0]["version"] == "0.2.4"


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
            lambda: ProcessEntryDiscoveryResult(
                entries=[
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
                warnings=[],
            ),
        )
        result = CliRunner().invoke(
            cli_app,
            ["ls", "--json", "--state-dir", str(state_dir)],
        )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"instances": []}


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

    assert instances == []


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


def make_record(
    *,
    pid: int,
    port: int,
    root: Path,
    source_id: str = "source",
    bundle_id: str = "bundle",
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
