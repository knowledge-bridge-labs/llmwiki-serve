from __future__ import annotations

import concurrent.futures
import ctypes
import json
import os
import re
import shlex
import socket
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import WikiManifest

_psutil: ModuleType | None
try:
    import psutil as _psutil
except ImportError:  # pragma: no cover - exercised only in degraded installs
    _psutil = None

REGISTRY_SCHEMA_VERSION = 2
STATE_DIR_ENV = "LLMWIKI_SERVE_STATE_DIR"
INSTANCE_RECORD_DIR = "instances"
HEALTH_PROBE_TIMEOUT_SECONDS = 1.5
HTTP_PROBE_READ_LIMIT = 256 * 1024
PROCESS_DISCOVERY_COMMAND_TIMEOUT_SECONDS = 3.0
PROCESS_HEALTH_PROBE_MAX_WORKERS = 8
PROCESS_HEALTH_PROBE_TOTAL_BUDGET_SECONDS = 3.0
DEFAULT_SERVE_HOST = "127.0.0.1"
DEFAULT_SERVE_PORT = 8765
MANUAL_PROBE_HOST = "127.0.0.1"
UNKNOWN_PID = 0
_PSUTIL = cast(Any, _psutil)

InstanceStatus = Literal["healthy", "unhealthy", "running", "stale"]
DiscoverySource = Literal["registry", "process", "manual-probe"]
RootSource = Literal["registry", "process-args", "unknown"]
HealthProbeFailureReason = Literal[
    "health-connection-failed",
    "health-http-error",
    "health-invalid-response",
    "health-local-listener-unverified",
    "health-non-llmwiki-service",
    "health-probe-budget-exceeded",
    "health-timeout",
]


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
    process_create_time: float | None = None
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
            process_create_time=current_process_create_time(pid),
        )

    def model_dump(self, *, include_process_create_time: bool = False) -> dict[str, Any]:
        payload = asdict(self)
        if not include_process_create_time:
            payload.pop("process_create_time", None)
        return payload

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
            process_create_time=self.process_create_time,
            schema_version=self.schema_version,
        )

    def with_pid(self, pid: int) -> InstanceRecord:
        return InstanceRecord(
            pid=pid,
            host=self.host,
            port=self.port,
            root=self.root,
            url=self.url,
            source_id=self.source_id,
            bundle_id=self.bundle_id,
            adapter=self.adapter,
            implementation=self.implementation,
            page_count=self.page_count,
            approved_page_count=self.approved_page_count,
            started_at=self.started_at,
            process_create_time=self.process_create_time,
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
            process_create_time=None,
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
class HealthProbeFailure:
    reason: HealthProbeFailureReason


@dataclass(frozen=True)
class ProcessEntry:
    pid: int
    argv: tuple[str, ...]
    command_line: str = ""
    cwd: str = ""
    create_time: float | None = None
    ppid: int | None = None


@dataclass(frozen=True)
class ListenerEndpoint:
    pid: int
    host: str
    port: int
    probe_host: str


@dataclass(frozen=True)
class ServeProcessCandidate:
    pid: int
    host: str
    port: int
    root: str
    listener_pid_verified: bool = False
    probe_host: str = ""


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
    service_verified: bool = False

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
                "service_verified": self.service_verified,
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
        json.dumps(
            record.model_dump(include_process_create_time=True),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
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
    listener_pids_by_endpoint: dict[tuple[str, int], int] | None = None,
    listeners_by_endpoint: dict[tuple[str, int], ListenerEndpoint] | None = None,
) -> list[InstanceInfo]:
    return discover_local_instances(
        state_dir=state_dir,
        probe=probe,
        prune_stale=prune_stale,
        probe_timeout_seconds=probe_timeout_seconds,
        processes=processes,
        manual_probe_ports=manual_probe_ports,
        process_entries=process_entries,
        listener_pids_by_endpoint=listener_pids_by_endpoint,
        listeners_by_endpoint=listeners_by_endpoint,
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
    listener_pids_by_endpoint: dict[tuple[str, int], int] | None = None,
    listeners_by_endpoint: dict[tuple[str, int], ListenerEndpoint] | None = None,
) -> LocalInstanceDiscoveryResult:
    stored_records = read_instance_records(state_dir=state_dir)
    warnings: list[str] = []
    process_result: ProcessEntryDiscoveryResult | None = None
    if processes:
        if process_entries is None:
            process_result = current_platform_process_entries()
            warnings.extend(process_result.warnings)
        else:
            listener_endpoints = listeners_by_endpoint or listener_endpoints_from_pids(
                listener_pids_by_endpoint or {}
            )
            process_result = ProcessEntryDiscoveryResult(
                entries=list(process_entries),
                warnings=[],
                listener_pids_by_endpoint=listener_pids_by_endpoint or {},
                listeners_by_endpoint=listener_endpoints,
            )
    listener_pids = process_result.listener_pids_by_endpoint if process_result else {}
    serve_processes = (
        discover_serve_processes(
            process_entries=process_result.entries,
            listener_pids_by_endpoint=process_result.listener_pids_by_endpoint,
            listeners_by_endpoint=process_result.listeners_by_endpoint,
        )
        if process_result is not None
        else None
    )
    verified_listener_endpoints = (
        {
            endpoint_key(candidate.host, candidate.port)
            for candidate in serve_processes.candidates
            if candidate.listener_pid_verified
        }
        if serve_processes is not None
        else set()
    )
    infos: list[InstanceInfo] = []
    for stored in stored_records:
        record = stored.record
        running = process_is_running(record.pid)
        notes: list[str] = []
        if running and record.process_create_time is not None:
            active_create_time = current_process_create_time(record.pid)
            if active_create_time is not None and not same_process_create_time(
                active_create_time,
                record.process_create_time,
            ):
                running = False
                notes.append("pid-reused")
        listener_pid = listener_pids.get(endpoint_key(record.host, record.port))
        if (
            running
            and listener_pid is not None
            and listener_pid > 0
            and listener_pid != record.pid
            and process_is_running(listener_pid)
        ):
            record = record.with_pid(listener_pid)
            notes.append("listener-pid-corrected")
            if endpoint_key(record.host, record.port) not in verified_listener_endpoints:
                notes.append("listener-pid-unverified")
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
        service_verified = healthy
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
                notes=notes,
                pruned=pruned,
                registered=True,
                orphan=False,
                version=version,
                discovery_source="registry",
                root_source="registry",
                service_verified=service_verified,
            )
        )
    seen_endpoints = active_endpoint_keys(infos)
    if serve_processes is not None:
        process_infos = probe_process_candidates(
            serve_processes.candidates,
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
    pending: list[tuple[tuple[str, int], ServeProcessCandidate]] = []
    for candidate in candidates:
        candidate_endpoint = endpoint_key(candidate.host, candidate.port)
        if candidate_endpoint in seen_endpoints:
            continue
        if not candidate.probe_host:
            infos.append(
                process_candidate_info_from_failure(
                    candidate,
                    reason="health-local-listener-unverified",
                )
            )
            seen_endpoints.add(candidate_endpoint)
            continue
        pending.append((candidate_endpoint, candidate))

    outcomes = probe_process_candidates_concurrently(
        [candidate for _, candidate in pending],
        timeout_seconds=timeout_seconds,
    )
    for candidate_endpoint, candidate in pending:
        outcome = outcomes[candidate]
        info = process_candidate_info_from_outcome(candidate, outcome)
        if info is None:
            continue
        infos.append(info)
        seen_endpoints.add(candidate_endpoint)
    return infos


def probe_process_candidates_concurrently(
    candidates: Sequence[ServeProcessCandidate],
    *,
    timeout_seconds: float,
) -> dict[ServeProcessCandidate, HealthProbeResult | HealthProbeFailure]:
    if not candidates:
        return {}
    outcomes: dict[ServeProcessCandidate, HealthProbeResult | HealthProbeFailure] = {}
    total_budget = max(timeout_seconds, PROCESS_HEALTH_PROBE_TOTAL_BUDGET_SECONDS)
    max_workers = min(len(candidates), PROCESS_HEALTH_PROBE_MAX_WORKERS)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    futures = {
        executor.submit(
            probe_llmwiki_health_outcome,
            process_probe_record(candidate),
            timeout_seconds=timeout_seconds,
        ): candidate
        for candidate in candidates
    }
    try:
        done, pending = concurrent.futures.wait(futures, timeout=total_budget)
        for future in done:
            candidate = futures[future]
            try:
                outcomes[candidate] = future.result()
            except Exception:
                outcomes[candidate] = HealthProbeFailure("health-connection-failed")
        for future in pending:
            outcomes[futures[future]] = HealthProbeFailure("health-probe-budget-exceeded")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return outcomes


def process_candidate_info_from_outcome(
    candidate: ServeProcessCandidate,
    outcome: HealthProbeResult | HealthProbeFailure,
) -> InstanceInfo | None:
    if isinstance(outcome, HealthProbeFailure):
        if outcome.reason == "health-non-llmwiki-service":
            return None
        return process_candidate_info_from_failure(candidate, reason=outcome.reason)

    record = InstanceRecord.from_health(
        pid=candidate.pid,
        host=process_probe_host(candidate),
        port=candidate.port,
        root=candidate.root,
        source=outcome.source,
    )
    notes = process_candidate_notes(candidate)
    version = outcome.version
    if not version:
        notes.append("version-unknown")
    return InstanceInfo(
        record=record,
        status="healthy",
        healthy=True,
        stale=False,
        notes=notes,
        registered=False,
        orphan=True,
        version=version,
        discovery_source="process",
        root_source="process-args" if candidate.root else "unknown",
        service_verified=True,
    )


def process_candidate_info_from_failure(
    candidate: ServeProcessCandidate,
    *,
    reason: HealthProbeFailureReason,
) -> InstanceInfo:
    record = process_probe_record(candidate)
    notes = process_candidate_notes(candidate)
    notes.extend(["service-unverified", reason])
    return InstanceInfo(
        record=record,
        status="unhealthy",
        healthy=False,
        stale=False,
        notes=notes,
        registered=False,
        orphan=True,
        version="",
        discovery_source="process",
        root_source="process-args" if candidate.root else "unknown",
        service_verified=False,
    )


def process_candidate_notes(candidate: ServeProcessCandidate) -> list[str]:
    notes = ["orphan", "process-discovered"]
    if not candidate.listener_pid_verified:
        notes.append("listener-pid-unverified")
    return notes


def process_probe_record(candidate: ServeProcessCandidate) -> InstanceRecord:
    host = process_probe_host(candidate)
    return InstanceRecord(
        pid=candidate.pid,
        host=host,
        port=candidate.port,
        root=candidate.root,
        url=instance_url(host, candidate.port),
        source_id="",
        bundle_id="",
        adapter="",
        implementation="",
        page_count=0,
        approved_page_count=0,
        started_at="",
    )


def process_probe_host(candidate: ServeProcessCandidate) -> str:
    return candidate.probe_host or candidate.host


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
                service_verified=True,
            )
        )
        seen_endpoints.add(candidate_endpoint)
    return infos


def discover_serve_processes(
    *,
    process_entries: Sequence[ProcessEntry] | None = None,
    listener_pids_by_endpoint: dict[tuple[str, int], int] | None = None,
    listeners_by_endpoint: dict[tuple[str, int], ListenerEndpoint] | None = None,
) -> ProcessDiscoveryResult:
    if process_entries is None:
        process_result = current_platform_process_entries()
        entries = process_result.entries
        warnings = process_result.warnings
        listener_endpoints = process_result.listeners_by_endpoint
    else:
        entries = list(process_entries)
        warnings = []
        listener_pids = listener_pids_by_endpoint or {}
        listener_endpoints = listeners_by_endpoint or listener_endpoints_from_pids(listener_pids)
    parent_pids = {entry.pid: entry.ppid for entry in entries if entry.ppid is not None}
    groups: dict[tuple[str, int], list[ServeProcessCandidate]] = {}
    for entry in entries:
        candidate = parse_serve_process_candidate(entry)
        if candidate is None:
            continue
        key = endpoint_key(candidate.host, candidate.port)
        groups.setdefault(key, []).append(candidate)
    candidates = [
        select_endpoint_candidate(
            group,
            listener=listener_endpoints.get(endpoint),
            parent_pids=parent_pids,
        )
        for endpoint, group in groups.items()
    ]
    return ProcessDiscoveryResult(candidates=candidates, warnings=warnings)


def select_endpoint_candidate(
    candidates: Sequence[ServeProcessCandidate],
    *,
    listener: ListenerEndpoint | None,
    parent_pids: dict[int, int],
) -> ServeProcessCandidate:
    selected = candidates[0]
    if listener is None or listener.pid <= 0:
        return selected
    listener_pid = listener.pid
    listener_candidate = next(
        (candidate for candidate in candidates if candidate.pid == listener_pid),
        None,
    )
    if listener_candidate is not None:
        selected = listener_candidate
        root = selected.root
    else:
        root = ""
    listener_pid_verified = pid_matches_candidate_tree(
        listener_pid,
        candidate_pids={candidate.pid for candidate in candidates},
        parent_pids=parent_pids,
    )
    return ServeProcessCandidate(
        pid=listener_pid,
        host=selected.host,
        port=selected.port,
        root=root,
        listener_pid_verified=listener_pid_verified,
        probe_host=listener.probe_host,
    )


def pid_matches_candidate_tree(
    pid: int,
    *,
    candidate_pids: set[int],
    parent_pids: dict[int, int],
) -> bool:
    if pid in candidate_pids:
        return True
    seen: set[int] = set()
    current = pid
    while current not in seen:
        seen.add(current)
        parent = parent_pids.get(current)
        if parent is None or parent <= 0:
            return False
        if parent in candidate_pids:
            return True
        current = parent
    return False


@dataclass(frozen=True)
class ProcessEntryDiscoveryResult:
    entries: list[ProcessEntry]
    warnings: list[str]
    listener_pids_by_endpoint: dict[tuple[str, int], int] = field(default_factory=dict)
    listeners_by_endpoint: dict[tuple[str, int], ListenerEndpoint] = field(default_factory=dict)


def current_platform_process_entries() -> ProcessEntryDiscoveryResult:
    primary = psutil_process_entries()
    if not psutil_process_provider_failed(primary):
        return primary
    fallback = windows_process_entries() if os.name == "nt" else posix_process_entries()
    return ProcessEntryDiscoveryResult(
        entries=fallback.entries,
        warnings=primary.warnings + fallback.warnings,
        listener_pids_by_endpoint=fallback.listener_pids_by_endpoint,
        listeners_by_endpoint=fallback.listeners_by_endpoint,
    )


def psutil_process_provider_failed(result: ProcessEntryDiscoveryResult) -> bool:
    return any("psutil process provider" in warning for warning in result.warnings)


def psutil_process_entries() -> ProcessEntryDiscoveryResult:
    if _PSUTIL is None:
        return ProcessEntryDiscoveryResult(
            entries=[],
            warnings=["process discovery degraded: psutil process provider unavailable"],
        )
    try:
        processes = list(_PSUTIL.process_iter(["pid"]))
    except Exception:  # pragma: no cover - provider-level OS failure
        return ProcessEntryDiscoveryResult(
            entries=[],
            warnings=["process discovery degraded: psutil process provider failed"],
        )

    entries: list[ProcessEntry] = []
    access_denied_cmdline = False
    access_denied_cwd = False
    access_denied_create_time = False
    access_denied_ppid = False
    for process in processes:
        try:
            pid = int(process.pid)
            raw_argv = process.cmdline()
        except _PSUTIL.AccessDenied:
            access_denied_cmdline = True
            continue
        except (
            _PSUTIL.NoSuchProcess,
            _PSUTIL.ZombieProcess,
            OSError,
            TypeError,
            ValueError,
        ):
            continue
        if not raw_argv:
            continue
        try:
            cwd = process.cwd()
        except _PSUTIL.AccessDenied:
            access_denied_cwd = True
            cwd = ""
        except (
            _PSUTIL.NoSuchProcess,
            _PSUTIL.ZombieProcess,
            OSError,
        ):
            cwd = ""
        try:
            create_time = float(process.create_time())
        except _PSUTIL.AccessDenied:
            access_denied_create_time = True
            create_time = None
        except (
            _PSUTIL.NoSuchProcess,
            _PSUTIL.ZombieProcess,
            OSError,
            TypeError,
            ValueError,
        ):
            create_time = None
        try:
            ppid = int(process.ppid())
        except _PSUTIL.AccessDenied:
            access_denied_ppid = True
            ppid = None
        except (
            _PSUTIL.NoSuchProcess,
            _PSUTIL.ZombieProcess,
            OSError,
            TypeError,
            ValueError,
        ):
            ppid = None
        argv = tuple(str(item) for item in raw_argv if str(item))
        if argv:
            entries.append(
                ProcessEntry(
                    pid=pid,
                    argv=argv,
                    cwd=str(cwd) if cwd else "",
                    create_time=create_time,
                    ppid=ppid,
                )
            )

    listeners, listener_warning = psutil_listeners_by_endpoint()
    warnings = [listener_warning] if listener_warning else []
    if access_denied_cmdline:
        warnings.append(
            "process discovery degraded: psutil denied access to some process command lines"
        )
    if access_denied_cwd:
        warnings.append(
            "process discovery degraded: psutil denied access to some process cwd values"
        )
    if access_denied_create_time:
        warnings.append(
            "process discovery degraded: psutil denied access to some process create_time values"
        )
    if access_denied_ppid:
        warnings.append(
            "process discovery degraded: psutil denied access to some process parent PIDs"
        )
    return ProcessEntryDiscoveryResult(
        entries=entries,
        warnings=warnings,
        listener_pids_by_endpoint={
            endpoint: listener.pid for endpoint, listener in listeners.items()
        },
        listeners_by_endpoint=listeners,
    )


def psutil_listeners_by_endpoint() -> tuple[dict[tuple[str, int], ListenerEndpoint], str]:
    if _PSUTIL is None:
        return {}, "process discovery degraded: psutil socket provider unavailable"
    try:
        connections = _PSUTIL.net_connections(kind="tcp")
    except _PSUTIL.AccessDenied:
        return {}, "process discovery degraded: psutil denied access to listener sockets"
    except Exception:  # pragma: no cover - provider-level OS failure
        return {}, "process discovery degraded: psutil socket provider failed"

    listeners: dict[tuple[str, int], ListenerEndpoint] = {}
    for connection in connections:
        try:
            if connection.status != _PSUTIL.CONN_LISTEN or connection.pid is None:
                continue
            host_port = socket_laddr_host_port(connection.laddr)
        except (AttributeError, TypeError, ValueError):
            continue
        if host_port is None:
            continue
        host, port = host_port
        listener = ListenerEndpoint(
            pid=int(connection.pid),
            host=host,
            port=port,
            probe_host=listener_probe_host(host),
        )
        for key in listener_endpoint_keys(host, port):
            listeners.setdefault(key, listener)
    return listeners, ""


def listener_endpoints_from_pids(
    listener_pids_by_endpoint: dict[tuple[str, int], int],
) -> dict[tuple[str, int], ListenerEndpoint]:
    return {
        (host, port): ListenerEndpoint(
            pid=pid,
            host=host,
            port=port,
            probe_host=listener_probe_host(host),
        )
        for (host, port), pid in listener_pids_by_endpoint.items()
        if pid > 0
    }


def listener_probe_host(host: str) -> str:
    normalized = normalized_listener_host(host)
    if normalized == "::":
        return "::1"
    if normalized in {"", "0.0.0.0", "localhost"}:
        return DEFAULT_SERVE_HOST
    return normalized


def normalized_listener_host(host: str) -> str:
    normalized = host.strip().lower()
    if normalized.startswith("[") and normalized.endswith("]"):
        return normalized[1:-1]
    return normalized


def listener_endpoint_keys(host: str, port: int) -> set[tuple[str, int]]:
    keys = {endpoint_key(host, port)}
    normalized = host.strip().lower()
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    if normalized in {"0.0.0.0", "::", ""}:
        keys.add((DEFAULT_SERVE_HOST, port))
    if normalized in {"::", "::1", ""}:
        keys.add(("::1", port))
    return keys


def socket_laddr_host_port(value: Any) -> tuple[str, int] | None:
    host = getattr(value, "ip", None)
    port = getattr(value, "port", None)
    if host is None or port is None:
        try:
            host = value[0]
            port = value[1]
        except (IndexError, TypeError):
            return None
    try:
        parsed_port = int(port)
    except (TypeError, ValueError):
        return None
    if parsed_port <= 0 or parsed_port > 65_535:
        return None
    return str(host), parsed_port


def windows_process_entries() -> ProcessEntryDiscoveryResult:
    provider_failed = False
    provider_seen = False
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
        except FileNotFoundError:
            continue
        except (OSError, subprocess.SubprocessError):
            provider_failed = True
            continue
        provider_seen = True
        if result.returncode != 0:
            provider_failed = True
            continue
        entries = windows_process_entries_from_json(result.stdout)
        return ProcessEntryDiscoveryResult(entries=entries, warnings=[])
    reason = "failed" if provider_failed or provider_seen else "unavailable"
    return ProcessEntryDiscoveryResult(
        entries=[],
        warnings=[f"process discovery degraded: Windows process provider {reason}"],
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
    proc_result = procfs_process_entries(Path("/proc"))
    if proc_result is not None:
        return proc_result
    return ps_process_entries()


def procfs_process_entries(proc_root: Path) -> ProcessEntryDiscoveryResult | None:
    if not proc_root.is_dir():
        return None
    entries: list[ProcessEntry] = []
    try:
        children = list(proc_root.iterdir())
    except OSError:
        return ProcessEntryDiscoveryResult(
            entries=[],
            warnings=["process discovery degraded: procfs process provider failed"],
        )
    cmdline_read_failed = False
    cwd_read_failed = False
    for path in children:
        if not path.name.isdigit():
            continue
        try:
            payload = (path / "cmdline").read_bytes()
        except OSError:
            cmdline_read_failed = True
            continue
        if not payload:
            continue
        argv = tuple(
            item.decode("utf-8", errors="replace")
            for item in payload.rstrip(b"\0").split(b"\0")
            if item
        )
        if argv:
            cwd = ""
            try:
                cwd = str((path / "cwd").resolve(strict=True))
            except OSError:
                cwd_read_failed = True
                cwd = ""
            entries.append(ProcessEntry(pid=int(path.name), argv=argv, command_line="", cwd=cwd))
    warnings: list[str] = []
    if cmdline_read_failed:
        warnings.append(
            "process discovery degraded: procfs could not read some process command lines"
        )
    if cwd_read_failed:
        warnings.append("process discovery degraded: procfs could not read some process cwd values")
    return ProcessEntryDiscoveryResult(entries=entries, warnings=warnings)


def ps_process_entries() -> ProcessEntryDiscoveryResult:
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,args="],
            capture_output=True,
            text=True,
            timeout=PROCESS_DISCOVERY_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ProcessEntryDiscoveryResult(
            entries=[],
            warnings=["process discovery degraded: POSIX process provider unavailable"],
        )
    if result.returncode != 0:
        return ProcessEntryDiscoveryResult(
            entries=[],
            warnings=["process discovery degraded: POSIX process provider failed"],
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
    cwd: str = "",
    create_time: float | None = None,
) -> ProcessEntry | None:
    argv = (
        split_windows_command_line(command_line)
        if platform == "windows"
        else split_posix_command_line(command_line)
    )
    if not argv:
        return None
    return ProcessEntry(
        pid=pid,
        argv=tuple(argv),
        command_line=command_line,
        cwd=cwd,
        create_time=create_time,
    )


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
    return ServeProcessCandidate(
        pid=entry.pid,
        host=host,
        port=port,
        root=resolve_process_root(root, entry.cwd),
    )


def resolve_process_root(root: str, cwd: str) -> str:
    if not root:
        return ""
    try:
        path = Path(root).expanduser()
        if not path.is_absolute():
            if not cwd:
                return root
            path = Path(cwd).expanduser() / path
        return str(path.resolve(strict=False))
    except (OSError, ValueError):
        return root


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
        process_create_time=float_value(payload.get("process_create_time")),
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
    if _PSUTIL is not None:
        return bool(_PSUTIL.pid_exists(pid))
    if os.name == "nt":
        return windows_process_is_running(pid)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def current_process_create_time(pid: int) -> float | None:
    if pid <= 0 or _PSUTIL is None:
        return None
    try:
        return float(_PSUTIL.Process(pid).create_time())
    except (
        _PSUTIL.NoSuchProcess,
        _PSUTIL.AccessDenied,
        _PSUTIL.ZombieProcess,
        OSError,
        TypeError,
        ValueError,
    ):
        return None


def same_process_create_time(left: float, right: float) -> bool:
    return abs(left - right) < 0.001


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
    outcome = probe_llmwiki_health_outcome(record, timeout_seconds=timeout_seconds)
    return outcome if isinstance(outcome, HealthProbeResult) else None


def probe_llmwiki_health_outcome(
    record: InstanceRecord,
    *,
    timeout_seconds: float = HEALTH_PROBE_TIMEOUT_SECONDS,
) -> HealthProbeResult | HealthProbeFailure:
    request = Request(health_url(record), headers={"accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", response.getcode())
            if status != 200:
                return HealthProbeFailure("health-http-error")
            body = response.read(HTTP_PROBE_READ_LIMIT + 1)
    except HTTPError:
        return HealthProbeFailure("health-http-error")
    except TimeoutError:
        return HealthProbeFailure("health-timeout")
    except URLError as exc:
        if is_timeout_error(exc):
            return HealthProbeFailure("health-timeout")
        return HealthProbeFailure("health-connection-failed")
    except (OSError, ValueError) as exc:
        if is_timeout_error(exc):
            return HealthProbeFailure("health-timeout")
        return HealthProbeFailure("health-connection-failed")
    if len(body) > HTTP_PROBE_READ_LIMIT:
        return HealthProbeFailure("health-invalid-response")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return HealthProbeFailure("health-invalid-response")
    if not isinstance(payload, dict):
        return HealthProbeFailure("health-invalid-response")
    if payload.get("service") != "llmwiki-serve":
        return HealthProbeFailure("health-non-llmwiki-service")
    source = payload.get("source")
    version = str(payload.get("version") or "")
    return HealthProbeResult(source=source if isinstance(source, dict) else {}, version=version)


def is_timeout_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return True
    reason = getattr(exc, "reason", None)
    return isinstance(reason, (TimeoutError, socket.timeout))


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


def float_value(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
