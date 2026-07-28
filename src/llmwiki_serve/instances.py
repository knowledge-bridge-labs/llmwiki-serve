from __future__ import annotations

import ctypes
import json
import os
import re
import shlex
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import WikiManifest

REGISTRY_SCHEMA_VERSION = 1
STATE_DIR_ENV = "LLMWIKI_SERVE_STATE_DIR"
INSTANCE_RECORD_DIR = "instances"
HEALTH_PROBE_TIMEOUT_SECONDS = 0.5
HTTP_PROBE_READ_LIMIT = 256 * 1024
PROCESS_DISCOVERY_COMMAND_TIMEOUT_SECONDS = 3.0
DEFAULT_SERVE_HOST = "127.0.0.1"
DEFAULT_SERVE_PORT = 8765
MANUAL_PROBE_HOST = "127.0.0.1"
UNKNOWN_PID = 0

InstanceStatus = Literal["healthy", "unhealthy", "running", "stale"]
DiscoverySource = Literal["registry", "process", "manual-probe"]
RootSource = Literal["registry", "process-args", "unknown"]


@dataclass(frozen=True)
class InstanceRecord:
    pid: int
    host: str
    port: int
    root: str
    url: str
    source_id: str
    bundle_id: str
    adapter: str
    implementation: str
    page_count: int
    approved_page_count: int
    started_at: str
    schema_version: int = REGISTRY_SCHEMA_VERSION

    @classmethod
    def from_manifest(
        cls,
        *,
        pid: int,
        host: str,
        port: int,
        manifest: WikiManifest,
        started_at: datetime | None = None,
    ) -> InstanceRecord:
        return cls(
            pid=pid,
            host=host,
            port=port,
            root=manifest.root,
            url=instance_url(host, port),
            source_id=manifest.source_id,
            bundle_id=manifest.bundle_id,
            adapter=manifest.adapter,
            implementation=manifest.implementation,
            page_count=manifest.page_count,
            approved_page_count=manifest.approved_page_count,
            started_at=(started_at or datetime.now(UTC)).isoformat().replace("+00:00", "Z"),
        )

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)

    def with_health(self, source: dict[str, Any]) -> InstanceRecord:
        return InstanceRecord(
            pid=self.pid,
            host=self.host,
            port=self.port,
            root=self.root,
            url=self.url,
            source_id=str(source.get("source_id") or self.source_id),
            bundle_id=str(source.get("bundle_id") or self.bundle_id),
            adapter=str(source.get("adapter") or self.adapter),
            implementation=str(source.get("implementation") or self.implementation),
            page_count=int_value(source.get("page_count"), default=self.page_count),
            approved_page_count=int_value(
                source.get("approved_page_count"),
                default=self.approved_page_count,
            ),
            started_at=self.started_at,
            schema_version=self.schema_version,
        )

    @classmethod
    def from_health(
        cls,
        *,
        pid: int = UNKNOWN_PID,
        host: str,
        port: int,
        root: str = "",
        source: dict[str, Any],
    ) -> InstanceRecord:
        return cls(
            pid=pid,
            host=host,
            port=port,
            root=root,
            url=instance_url(host, port),
            source_id=str(source.get("source_id") or ""),
            bundle_id=str(source.get("bundle_id") or ""),
            adapter=str(source.get("adapter") or ""),
            implementation=str(source.get("implementation") or ""),
            page_count=int_value(source.get("page_count"), default=0),
            approved_page_count=int_value(source.get("approved_page_count"), default=0),
            started_at="",
        )


@dataclass(frozen=True)
class StoredInstanceRecord:
    path: Path
    record: InstanceRecord


@dataclass(frozen=True)
class HealthProbeResult:
    source: dict[str, Any]
    version: str = ""


@dataclass(frozen=True)
class ProcessEntry:
    pid: int
    argv: tuple[str, ...]
    command_line: str = ""


@dataclass(frozen=True)
class ServeProcessCandidate:
    pid: int
    host: str
    port: int
    root: str


@dataclass(frozen=True)
class ProcessDiscoveryResult:
    candidates: list[ServeProcessCandidate]
    warnings: list[str]


@dataclass
class InstanceInfo:
    record: InstanceRecord
    status: InstanceStatus
    healthy: bool
    stale: bool
    notes: list[str]
    pruned: bool = False
    registered: bool = True
    orphan: bool = False
    version: str = ""
    discovery_source: DiscoverySource = "registry"
    root_source: RootSource = "registry"

    def model_dump(self) -> dict[str, Any]:
        payload = self.record.model_dump()
        payload.update(
            {
                "status": self.status,
                "healthy": self.healthy,
                "stale": self.stale,
                "notes": sorted(self.notes),
                "pruned": self.pruned,
                "registered": self.registered,
                "orphan": self.orphan,
                "version": self.version or "unknown",
                "discovery_source": self.discovery_source,
                "root_source": self.root_source,
            }
        )
        return payload


@dataclass(frozen=True)
class LocalInstanceDiscoveryResult:
    instances: list[InstanceInfo]
    warnings: list[str]

    def model_dump(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "instances": [item.model_dump() for item in self.instances],
        }
        if self.warnings:
            payload["warnings"] = sorted(set(self.warnings))
        return payload


def default_state_dir() -> Path:
    env_value = os.getenv(STATE_DIR_ENV)
    if env_value:
        return Path(env_value).expanduser()
    if os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        if local_app_data:
            return Path(local_app_data) / "llmwiki-serve"
    xdg_state_home = os.getenv("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home).expanduser() / "llmwiki-serve"
    return Path.home() / ".local" / "state" / "llmwiki-serve"


def instance_registry_dir(state_dir: Path | None = None) -> Path:
    return (state_dir or default_state_dir()) / INSTANCE_RECORD_DIR


def register_instance(record: InstanceRecord, *, state_dir: Path | None = None) -> Path:
    directory = instance_registry_dir(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    cleanup_replaced_records(record, directory)
    path = directory / instance_record_filename(record)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(record.model_dump(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def unregister_instance(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return


def list_local_instances(
    *,
    state_dir: Path | None = None,
    probe: bool = True,
    prune_stale: bool = False,
    probe_timeout_seconds: float = HEALTH_PROBE_TIMEOUT_SECONDS,
    processes: bool = True,
    manual_probe_ports: Sequence[int] | None = None,
    process_entries: Sequence[ProcessEntry] | None = None,
) -> list[InstanceInfo]:
    return discover_local_instances(
        state_dir=state_dir,
        probe=probe,
        prune_stale=prune_stale,
        probe_timeout_seconds=probe_timeout_seconds,
        processes=processes,
        manual_probe_ports=manual_probe_ports,
        process_entries=process_entries,
    ).instances


def discover_local_instances(
    *,
    state_dir: Path | None = None,
    probe: bool = True,
    prune_stale: bool = False,
    probe_timeout_seconds: float = HEALTH_PROBE_TIMEOUT_SECONDS,
    processes: bool = True,
    manual_probe_ports: Sequence[int] | None = None,
    process_entries: Sequence[ProcessEntry] | None = None,
) -> LocalInstanceDiscoveryResult:
    stored_records = read_instance_records(state_dir=state_dir)
    infos: list[InstanceInfo] = []
    for stored in stored_records:
        record = stored.record
        running = process_is_running(record.pid)
        status: InstanceStatus
        healthy = False
        stale = not running
        version = ""
        if stale:
            status = "stale"
        elif not probe:
            status = "running"
        else:
            health = probe_llmwiki_health(record, timeout_seconds=probe_timeout_seconds)
            if health is None:
                status = "unhealthy"
            else:
                status = "healthy"
                healthy = True
                record = record.with_health(health.source)
                version = health.version
        pruned = False
        if stale and prune_stale:
            unregister_instance(stored.path)
            pruned = True
        infos.append(
            InstanceInfo(
                record=record,
                status=status,
                healthy=healthy,
                stale=stale,
                notes=[],
                pruned=pruned,
                registered=True,
                orphan=False,
                version=version,
                discovery_source="registry",
                root_source="registry",
            )
        )
    warnings: list[str] = []
    seen_endpoints = active_endpoint_keys(infos)
    if processes:
        process_result = discover_serve_processes(process_entries=process_entries)
        warnings.extend(process_result.warnings)
        process_infos = probe_process_candidates(
            process_result.candidates,
            seen_endpoints=seen_endpoints,
            timeout_seconds=probe_timeout_seconds,
        )
        infos.extend(process_infos)
        seen_endpoints.update(
            endpoint_key(info.record.host, info.record.port) for info in process_infos
        )
    if manual_probe_ports:
        infos.extend(
            probe_manual_ports(
                manual_probe_ports,
                seen_endpoints=seen_endpoints,
                timeout_seconds=probe_timeout_seconds,
            )
        )
    annotate_instance_notes(infos)
    instances = sorted(
        infos,
        key=lambda item: (item.record.host, item.record.port, item.record.pid),
    )
    return LocalInstanceDiscoveryResult(instances=instances, warnings=warnings)


def active_endpoint_keys(infos: Sequence[InstanceInfo]) -> set[tuple[str, int]]:
    return {endpoint_key(info.record.host, info.record.port) for info in infos if not info.stale}


def probe_process_candidates(
    candidates: Sequence[ServeProcessCandidate],
    *,
    seen_endpoints: set[tuple[str, int]],
    timeout_seconds: float = HEALTH_PROBE_TIMEOUT_SECONDS,
) -> list[InstanceInfo]:
    infos: list[InstanceInfo] = []
    for candidate in candidates:
        candidate_endpoint = endpoint_key(candidate.host, candidate.port)
        if candidate_endpoint in seen_endpoints:
            continue
        probe_record = InstanceRecord(
            pid=candidate.pid,
            host=candidate.host,
            port=candidate.port,
            root=candidate.root,
            url=instance_url(candidate.host, candidate.port),
            source_id="",
            bundle_id="",
            adapter="",
            implementation="",
            page_count=0,
            approved_page_count=0,
            started_at="",
        )
        health = probe_llmwiki_health(probe_record, timeout_seconds=timeout_seconds)
        if health is None:
            continue
        record = InstanceRecord.from_health(
            pid=candidate.pid,
            host=candidate.host,
            port=candidate.port,
            root=candidate.root,
            source=health.source,
        )
        notes = ["orphan", "process-discovered"]
        if not health.version:
            notes.append("version-unknown")
        infos.append(
            InstanceInfo(
                record=record,
                status="healthy",
                healthy=True,
                stale=False,
                notes=notes,
                registered=False,
                orphan=True,
                version=health.version,
                discovery_source="process",
                root_source="process-args" if candidate.root else "unknown",
            )
        )
        seen_endpoints.add(candidate_endpoint)
    return infos


def probe_manual_ports(
    ports: Sequence[int],
    *,
    seen_endpoints: set[tuple[str, int]],
    timeout_seconds: float = HEALTH_PROBE_TIMEOUT_SECONDS,
) -> list[InstanceInfo]:
    infos: list[InstanceInfo] = []
    for port in ports:
        if port <= 0 or port > 65_535:
            continue
        candidate_endpoint = endpoint_key(MANUAL_PROBE_HOST, port)
        if candidate_endpoint in seen_endpoints:
            continue
        probe_record = InstanceRecord(
            pid=UNKNOWN_PID,
            host=MANUAL_PROBE_HOST,
            port=port,
            root="",
            url=instance_url(MANUAL_PROBE_HOST, port),
            source_id="",
            bundle_id="",
            adapter="",
            implementation="",
            page_count=0,
            approved_page_count=0,
            started_at="",
        )
        health = probe_llmwiki_health(probe_record, timeout_seconds=timeout_seconds)
        if health is None:
            continue
        record = InstanceRecord.from_health(
            host=MANUAL_PROBE_HOST,
            port=port,
            source=health.source,
        )
        notes = ["orphan", "manual-probe"]
        if not health.version:
            notes.append("version-unknown")
        infos.append(
            InstanceInfo(
                record=record,
                status="healthy",
                healthy=True,
                stale=False,
                notes=notes,
                registered=False,
                orphan=True,
                version=health.version,
                discovery_source="manual-probe",
                root_source="unknown",
            )
        )
        seen_endpoints.add(candidate_endpoint)
    return infos


def discover_serve_processes(
    *,
    process_entries: Sequence[ProcessEntry] | None = None,
) -> ProcessDiscoveryResult:
    if process_entries is None:
        process_result = current_platform_process_entries()
        entries = process_result.entries
        warnings = process_result.warnings
    else:
        entries = list(process_entries)
        warnings = []
    candidates: list[ServeProcessCandidate] = []
    seen: set[tuple[int, str, int]] = set()
    for entry in entries:
        candidate = parse_serve_process_candidate(entry)
        if candidate is None:
            continue
        key = (candidate.pid, normalized_endpoint_host(candidate.host), candidate.port)
        if key in seen:
            continue
        candidates.append(candidate)
        seen.add(key)
    return ProcessDiscoveryResult(candidates=candidates, warnings=warnings)


@dataclass(frozen=True)
class ProcessEntryDiscoveryResult:
    entries: list[ProcessEntry]
    warnings: list[str]


def current_platform_process_entries() -> ProcessEntryDiscoveryResult:
    if os.name == "nt":
        return windows_process_entries()
    return posix_process_entries()


def windows_process_entries() -> ProcessEntryDiscoveryResult:
    last_error = ""
    for executable in ("powershell", "pwsh"):
        try:
            result = subprocess.run(
                [
                    executable,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    (
                        "$ErrorActionPreference='Stop'; "
                        "Get-CimInstance Win32_Process | "
                        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=PROCESS_DISCOVERY_COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            last_error = str(exc)
            continue
        if result.returncode != 0:
            last_error = (result.stderr or result.stdout).strip()
            continue
        entries = windows_process_entries_from_json(result.stdout)
        return ProcessEntryDiscoveryResult(entries=entries, warnings=[])
    detail = f": {last_error}" if last_error else ""
    return ProcessEntryDiscoveryResult(
        entries=[],
        warnings=[f"process discovery degraded: Windows process provider unavailable{detail}"],
    )


def windows_process_entries_from_json(value: str) -> list[ProcessEntry]:
    text = value.strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    items = [payload] if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    entries: list[ProcessEntry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            pid = int(item["ProcessId"])
        except (KeyError, TypeError, ValueError):
            continue
        command_line = item.get("CommandLine")
        if not isinstance(command_line, str) or not command_line.strip():
            continue
        entry = process_entry_from_command_line(
            pid=pid,
            command_line=command_line,
            platform="windows",
        )
        if entry is not None:
            entries.append(entry)
    return entries


def posix_process_entries() -> ProcessEntryDiscoveryResult:
    proc_entries = procfs_process_entries(Path("/proc"))
    if proc_entries is not None:
        return ProcessEntryDiscoveryResult(entries=proc_entries, warnings=[])
    return ps_process_entries()


def procfs_process_entries(proc_root: Path) -> list[ProcessEntry] | None:
    if not proc_root.is_dir():
        return None
    entries: list[ProcessEntry] = []
    try:
        children = list(proc_root.iterdir())
    except OSError:
        return None
    for path in children:
        if not path.name.isdigit():
            continue
        try:
            payload = (path / "cmdline").read_bytes()
        except OSError:
            continue
        if not payload:
            continue
        argv = tuple(
            item.decode("utf-8", errors="replace")
            for item in payload.rstrip(b"\0").split(b"\0")
            if item
        )
        if argv:
            entries.append(ProcessEntry(pid=int(path.name), argv=argv, command_line=""))
    return entries


def ps_process_entries() -> ProcessEntryDiscoveryResult:
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,args="],
            capture_output=True,
            text=True,
            timeout=PROCESS_DISCOVERY_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ProcessEntryDiscoveryResult(
            entries=[],
            warnings=[f"process discovery degraded: POSIX process provider unavailable: {exc}"],
        )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        suffix = f": {detail}" if detail else ""
        return ProcessEntryDiscoveryResult(
            entries=[],
            warnings=[f"process discovery degraded: POSIX process provider failed{suffix}"],
        )
    entries: list[ProcessEntry] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        entry = process_entry_from_command_line(
            pid=pid,
            command_line=parts[1],
            platform="posix",
        )
        if entry is not None:
            entries.append(entry)
    return ProcessEntryDiscoveryResult(entries=entries, warnings=[])


def process_entry_from_command_line(
    *,
    pid: int,
    command_line: str,
    platform: Literal["windows", "posix"],
) -> ProcessEntry | None:
    argv = (
        split_windows_command_line(command_line)
        if platform == "windows"
        else split_posix_command_line(command_line)
    )
    if not argv:
        return None
    return ProcessEntry(pid=pid, argv=tuple(argv), command_line=command_line)


def split_posix_command_line(command_line: str) -> list[str]:
    try:
        return shlex.split(command_line)
    except ValueError:
        return []


def split_windows_command_line(command_line: str) -> list[str]:
    args: list[str] = []
    current: list[str] = []
    in_quotes = False
    had_token = False
    index = 0
    length = len(command_line)
    while index < length:
        char = command_line[index]
        if char in " \t" and not in_quotes:
            if had_token:
                args.append("".join(current))
                current = []
                had_token = False
            index += 1
            continue
        if char == "\\":
            slash_start = index
            while index < length and command_line[index] == "\\":
                index += 1
            slash_count = index - slash_start
            if index < length and command_line[index] == '"':
                current.extend("\\" * (slash_count // 2))
                if slash_count % 2:
                    current.append('"')
                else:
                    in_quotes = not in_quotes
                had_token = True
                index += 1
                continue
            current.extend("\\" * slash_count)
            had_token = True
            continue
        if char == '"':
            in_quotes = not in_quotes
            had_token = True
            index += 1
            continue
        current.append(char)
        had_token = True
        index += 1
    if had_token:
        args.append("".join(current))
    return args


def parse_serve_process_candidate(entry: ProcessEntry) -> ServeProcessCandidate | None:
    serve_args = serve_args_from_argv(entry.argv)
    if serve_args is None:
        return None
    parsed = parse_serve_args(serve_args)
    if parsed is None:
        return None
    host, port, root = parsed
    return ServeProcessCandidate(pid=entry.pid, host=host, port=port, root=root)


def serve_args_from_argv(argv: Sequence[str]) -> tuple[str, ...] | None:
    for index, token in enumerate(argv):
        if token == "-m" and index + 2 < len(argv):
            module_name = argv[index + 1]
            if module_name in {"llmwiki_serve", "llmwiki_serve.cli"} and argv[index + 2] == "serve":
                return tuple(argv[index + 3 :])
        if is_llmwiki_serve_command(token) and index + 1 < len(argv) and argv[index + 1] == "serve":
            return tuple(argv[index + 2 :])
        if is_llmwiki_serve_module(token) and index + 1 < len(argv) and argv[index + 1] == "serve":
            return tuple(argv[index + 2 :])
    return None


def is_llmwiki_serve_command(token: str) -> bool:
    name = command_basename(token).lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name == "llmwiki-serve"


def is_llmwiki_serve_module(token: str) -> bool:
    normalized = token.replace("\\", "/")
    return normalized.endswith("/llmwiki_serve/cli.py") or token in {
        "llmwiki_serve",
        "llmwiki_serve.cli",
    }


def command_basename(token: str) -> str:
    normalized = token.replace("\\", "/").rstrip("/")
    return normalized.rsplit("/", 1)[-1]


SERVE_OPTIONS_WITH_VALUES = {
    "--host",
    "--port",
    "--cors-origin",
    "--refresh-interval-seconds",
    "--producer-manifest",
    "--io-log",
    "--projection-store",
    "--redis-url",
    "--redis-failure-policy",
    "--cache-namespace",
    "--source-id",
    "--graph-default-limit",
    "--context-default-limit",
    "--mcp-server-name",
    "--mcp-title",
    "--mcp-instructions",
    "--mcp-tool-description-prefix",
}


def parse_serve_args(args: Sequence[str]) -> tuple[str, int, str] | None:
    host = DEFAULT_SERVE_HOST
    port = DEFAULT_SERVE_PORT
    root = ""
    index = 0
    positional_mode = False
    while index < len(args):
        token = args[index]
        if positional_mode:
            if not root:
                root = token
            index += 1
            continue
        if token == "--":
            positional_mode = True
            index += 1
            continue
        if token.startswith("--"):
            option, has_value, inline_value = token.partition("=")
            if option == "--host":
                if has_value:
                    host = inline_value or host
                    index += 1
                    continue
                if index + 1 >= len(args):
                    return None
                host = args[index + 1] or host
                index += 2
                continue
            if option == "--port":
                value: str
                if has_value:
                    value = inline_value
                    index += 1
                else:
                    if index + 1 >= len(args):
                        return None
                    value = args[index + 1]
                    index += 2
                parsed_port = parse_port(value)
                if parsed_port is None:
                    return None
                port = parsed_port
                continue
            if option in SERVE_OPTIONS_WITH_VALUES and not has_value:
                index += 2
                continue
            index += 1
            continue
        if token.startswith("-") and token != "-":
            index += 1
            continue
        if not root:
            root = token
        index += 1
    if port <= 0 or port > 65_535:
        return None
    return host, port, root


def parse_port(value: str) -> int | None:
    try:
        port = int(value)
    except ValueError:
        return None
    if port <= 0 or port > 65_535:
        return None
    return port


def read_instance_records(*, state_dir: Path | None = None) -> list[StoredInstanceRecord]:
    directory = instance_registry_dir(state_dir)
    if not directory.exists():
        return []
    records: list[StoredInstanceRecord] = []
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        record = instance_record_from_payload(payload)
        if record is None:
            continue
        records.append(StoredInstanceRecord(path=path, record=record))
    return records


def instance_record_from_payload(payload: dict[str, Any]) -> InstanceRecord | None:
    try:
        pid = int(payload["pid"])
        port = int(payload["port"])
    except (KeyError, TypeError, ValueError):
        return None
    if pid <= 0 or port <= 0 or port > 65_535:
        return None
    host = str(payload.get("host") or "")
    return InstanceRecord(
        pid=pid,
        host=host,
        port=port,
        root=str(payload.get("root") or ""),
        url=str(payload.get("url") or instance_url(host, port)),
        source_id=str(payload.get("source_id") or ""),
        bundle_id=str(payload.get("bundle_id") or ""),
        adapter=str(payload.get("adapter") or ""),
        implementation=str(payload.get("implementation") or ""),
        page_count=int_value(payload.get("page_count"), default=0),
        approved_page_count=int_value(payload.get("approved_page_count"), default=0),
        started_at=str(payload.get("started_at") or ""),
        schema_version=int_value(payload.get("schema_version"), default=REGISTRY_SCHEMA_VERSION),
    )


def cleanup_replaced_records(record: InstanceRecord, directory: Path) -> None:
    for stored in read_instance_records(state_dir=directory.parent):
        if stored.record.pid == record.pid or (
            stored.record.host == record.host and stored.record.port == record.port
        ):
            unregister_instance(stored.path)


def instance_record_filename(record: InstanceRecord) -> str:
    host = re.sub(r"[^A-Za-z0-9_.-]+", "_", record.host).strip("_") or "host"
    return f"{host}-{record.port}-{record.pid}.json"


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return windows_process_is_running(pid)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def windows_process_is_running(pid: int) -> bool:
    kernel32 = cast(Any, ctypes).windll.kernel32
    process_query_limited_information = 0x1000
    still_active = 259
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def probe_instance_health(
    record: InstanceRecord,
    *,
    timeout_seconds: float = HEALTH_PROBE_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    health = probe_llmwiki_health(record, timeout_seconds=timeout_seconds)
    return None if health is None else health.source


def probe_llmwiki_health(
    record: InstanceRecord,
    *,
    timeout_seconds: float = HEALTH_PROBE_TIMEOUT_SECONDS,
) -> HealthProbeResult | None:
    request = Request(health_url(record), headers={"accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", response.getcode())
            if status != 200:
                return None
            body = response.read(HTTP_PROBE_READ_LIMIT + 1)
    except (HTTPError, OSError, TimeoutError, URLError, ValueError):
        return None
    if len(body) > HTTP_PROBE_READ_LIMIT:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("service") != "llmwiki-serve":
        return None
    source = payload.get("source")
    version = str(payload.get("version") or "")
    return HealthProbeResult(source=source if isinstance(source, dict) else {}, version=version)


def health_url(record: InstanceRecord) -> str:
    return f"{probe_base_url(record)}/health"


def probe_base_url(record: InstanceRecord) -> str:
    host = record.host.strip()
    if host in {"0.0.0.0", "::", "[::]"}:
        host = DEFAULT_SERVE_HOST
    return f"http://{url_host(host)}:{record.port}"


def instance_url(host: str, port: int) -> str:
    return f"http://{url_host(host)}:{port}"


def url_host(host: str) -> str:
    stripped = host.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        return stripped
    if ":" in stripped:
        return f"[{stripped}]"
    return stripped or DEFAULT_SERVE_HOST


def endpoint_key(host: str, port: int) -> tuple[str, int]:
    return normalized_endpoint_host(host), port


def normalized_endpoint_host(host: str) -> str:
    normalized = host.strip().lower()
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    if normalized in {"", "0.0.0.0", "::", "localhost"}:
        return DEFAULT_SERVE_HOST
    return normalized


def annotate_instance_notes(infos: list[InstanceInfo]) -> None:
    active = [info for info in infos if not info.stale]
    for group in grouped_by(active, lambda item: item.record.bundle_id):
        if len(group) > 1 and group[0].record.bundle_id:
            add_note(group, "duplicate-bundle")
    for group in grouped_by(active, lambda item: normalized_root_key(item.record.root)):
        if len(group) > 1 and group[0].record.root:
            add_note(group, "same-root")
    for index, left in enumerate(active):
        left_root = resolved_root(left.record.root)
        if left_root is None:
            continue
        for right in active[index + 1 :]:
            right_root = resolved_root(right.record.root)
            if right_root is None or same_path(left_root, right_root):
                continue
            if is_relative_to(right_root, left_root):
                append_note(left, "parent-root")
                append_note(right, "subfolder-root")
            elif is_relative_to(left_root, right_root):
                append_note(left, "subfolder-root")
                append_note(right, "parent-root")


def grouped_by(
    infos: list[InstanceInfo],
    key_func: Callable[[InstanceInfo], str],
) -> list[list[InstanceInfo]]:
    groups: dict[str, list[InstanceInfo]] = {}
    for info in infos:
        key = key_func(info)
        if not key:
            continue
        groups.setdefault(key, []).append(info)
    return list(groups.values())


def add_note(infos: list[InstanceInfo], note: str) -> None:
    for info in infos:
        append_note(info, note)


def append_note(info: InstanceInfo, note: str) -> None:
    if note not in info.notes:
        info.notes.append(note)


def resolved_root(value: str) -> Path | None:
    if not value:
        return None
    try:
        return Path(value).expanduser().resolve(strict=False)
    except OSError:
        return None


def normalized_root_key(value: str) -> str:
    root = resolved_root(value)
    if root is None:
        return ""
    return os.path.normcase(str(root))


def same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def int_value(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
