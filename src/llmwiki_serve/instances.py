from __future__ import annotations

import ctypes
import json
import os
import re
from collections.abc import Callable
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

InstanceStatus = Literal["healthy", "unhealthy", "running", "stale"]


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


@dataclass(frozen=True)
class StoredInstanceRecord:
    path: Path
    record: InstanceRecord


@dataclass
class InstanceInfo:
    record: InstanceRecord
    status: InstanceStatus
    healthy: bool
    stale: bool
    notes: list[str]
    pruned: bool = False

    def model_dump(self) -> dict[str, Any]:
        payload = self.record.model_dump()
        payload.update(
            {
                "status": self.status,
                "healthy": self.healthy,
                "stale": self.stale,
                "notes": sorted(self.notes),
                "pruned": self.pruned,
            }
        )
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
) -> list[InstanceInfo]:
    stored_records = read_instance_records(state_dir=state_dir)
    infos: list[InstanceInfo] = []
    for stored in stored_records:
        record = stored.record
        running = process_is_running(record.pid)
        status: InstanceStatus
        healthy = False
        stale = not running
        if stale:
            status = "stale"
        elif not probe:
            status = "running"
        else:
            health = probe_instance_health(record, timeout_seconds=probe_timeout_seconds)
            if health is None:
                status = "unhealthy"
            else:
                status = "healthy"
                healthy = True
                record = record.with_health(health)
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
            )
        )
    annotate_instance_notes(infos)
    return sorted(infos, key=lambda item: (item.record.host, item.record.port, item.record.pid))


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
    return source if isinstance(source, dict) else {}


def health_url(record: InstanceRecord) -> str:
    return f"{probe_base_url(record)}/health"


def probe_base_url(record: InstanceRecord) -> str:
    host = record.host.strip()
    if host in {"0.0.0.0", "::", "[::]"}:
        host = "127.0.0.1"
    return f"http://{url_host(host)}:{record.port}"


def instance_url(host: str, port: int) -> str:
    return f"http://{url_host(host)}:{port}"


def url_host(host: str) -> str:
    stripped = host.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        return stripped
    if ":" in stripped:
        return f"[{stripped}]"
    return stripped or "127.0.0.1"


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
