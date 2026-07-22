from __future__ import annotations

import hashlib
import importlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

from .models import WikiIndex

PROJECTION_STORE_SCHEMA_VERSION = "projection-store-v1"
REDIS_EXTRA_MESSAGE = (
    "Redis projection store requires llmwiki-serve[redis]. "
    'Install with: pip install "llmwiki-serve[redis]"'
)
ProjectionStoreBackend = Literal["memory", "redis"]
RedisFailurePolicy = Literal["fallback-local", "fail-fast"]


@dataclass(frozen=True)
class ProjectionKey:
    namespace: str
    source_id: str
    projection_signature: str
    schema_version: str = PROJECTION_STORE_SCHEMA_VERSION

    @property
    def bundle_identity(self) -> str:
        return f"{self.source_id}:{self.projection_signature}"


@dataclass(frozen=True)
class ProjectionRecord:
    key: ProjectionKey
    index: WikiIndex


class ProjectionStore(Protocol):
    def get(self, key: ProjectionKey, *, root: Path) -> ProjectionRecord | None: ...

    def put(self, record: ProjectionRecord) -> None: ...

    def invalidate_source(self, *, namespace: str, source_id: str) -> None: ...


class InMemoryProjectionStore:
    backend_kind: Literal["memory"] = "memory"
    endpoint: str | None = None

    def __init__(self) -> None:
        self._records: dict[ProjectionKey, ProjectionRecord] = {}

    def get(self, key: ProjectionKey, *, root: Path) -> ProjectionRecord | None:
        return self._records.get(key)

    def put(self, record: ProjectionRecord) -> None:
        self._records[record.key] = record

    def invalidate_source(self, *, namespace: str, source_id: str) -> None:
        self._records = {
            key: value
            for key, value in self._records.items()
            if key.namespace != namespace or key.source_id != source_id
        }


class RedisProjectionStore:
    backend_kind: Literal["redis"] = "redis"

    def __init__(
        self,
        *,
        url: str,
        failure_policy: RedisFailurePolicy = "fallback-local",
        client: Any | None = None,
    ) -> None:
        self.url = url
        self.endpoint = safe_redis_endpoint_label(url)
        self.failure_policy = failure_policy
        self._available = True
        self._last_error = ""
        self._memory_fallback = InMemoryProjectionStore()
        self._client: Any
        if client is not None:
            self._client = client
            return
        try:
            redis_module = importlib.import_module("redis")
        except ModuleNotFoundError as exc:
            raise RuntimeError(REDIS_EXTRA_MESSAGE) from exc
        self._client = redis_module.Redis.from_url(url, decode_responses=True)

    @property
    def available(self) -> bool:
        return self._available

    @property
    def last_error(self) -> str:
        return self._last_error

    def get(self, key: ProjectionKey, *, root: Path) -> ProjectionRecord | None:
        if not self._available:
            return self._memory_fallback.get(key, root=root)
        try:
            raw = self._client.get(redis_projection_key(key))
        except Exception as exc:
            self._handle_failure(exc)
            return self._memory_fallback.get(key, root=root)
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
            record = projection_record_from_payload(key, payload, root=root)
            self._last_error = ""
            return record
        except (TypeError, ValueError, KeyError) as exc:
            self._last_error = safe_error_message(exc)
            return None

    def put(self, record: ProjectionRecord) -> None:
        self._memory_fallback.put(record)
        if not self._available:
            return
        try:
            self._client.set(
                redis_projection_key(record.key), json.dumps(record_to_payload(record))
            )
            self._client.set(
                redis_latest_key(record.key.namespace, record.key.source_id),
                record.key.projection_signature,
            )
            self._last_error = ""
        except Exception as exc:
            self._handle_failure(exc)

    def invalidate_source(self, *, namespace: str, source_id: str) -> None:
        self._memory_fallback.invalidate_source(namespace=namespace, source_id=source_id)
        if not self._available:
            return
        try:
            keys = list(
                self._client.scan_iter(
                    match=redis_source_projection_key_pattern(namespace, source_id)
                )
            )
            keys.append(redis_latest_key(namespace, source_id))
            self._client.delete(*keys)
        except Exception as exc:
            self._handle_failure(exc)

    def _handle_failure(self, exc: Exception) -> None:
        self._last_error = safe_error_message(exc)
        if self.failure_policy == "fail-fast":
            raise RuntimeError(f"Redis projection store failed: {self._last_error}") from exc
        self._available = False


def create_projection_store(
    backend: ProjectionStoreBackend,
    *,
    redis_url: str | None = None,
    redis_failure_policy: RedisFailurePolicy = "fallback-local",
) -> ProjectionStore:
    if backend == "memory":
        return InMemoryProjectionStore()
    if not redis_url:
        raise ValueError("--redis-url is required when --projection-store=redis")
    return RedisProjectionStore(url=redis_url, failure_policy=redis_failure_policy)


def redis_projection_key(key: ProjectionKey) -> str:
    return (
        f"llmwiki:{safe_key_part(key.namespace)}:projections:"
        f"{safe_key_part(key.schema_version)}:{safe_key_part(key.source_id)}:"
        f"{safe_key_part(key.projection_signature)}"
    )


def redis_source_projection_key_pattern(namespace: str, source_id: str) -> str:
    return (
        f"llmwiki:{safe_key_part(namespace)}:projections:"
        f"{safe_key_part(PROJECTION_STORE_SCHEMA_VERSION)}:{safe_key_part(source_id)}:*"
    )


def redis_latest_key(namespace: str, source_id: str) -> str:
    return f"llmwiki:{safe_key_part(namespace)}:sources:{safe_key_part(source_id)}:latest"


def safe_key_part(value: str) -> str:
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    if not candidate:
        candidate = "empty"
    if candidate == value:
        return candidate
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{candidate[:80]}-{digest}"


def safe_error_message(exc: Exception) -> str:
    if isinstance(exc, (TypeError, ValueError, KeyError, json.JSONDecodeError)):
        return f"{exc.__class__.__name__}: cache payload rejected"
    return f"{exc.__class__.__name__}: backend operation failed"


def safe_redis_endpoint_label(url: str) -> str:
    try:
        parts = urlsplit(url)
        scheme = parts.scheme or "redis"
        host = parts.hostname
        port = parts.port
    except ValueError:
        return "redis://<redacted>"

    if not host:
        return f"{scheme}://<redacted>"

    display_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    netloc = f"{display_host}:{port}" if port is not None else display_host
    path = parts.path if re.fullmatch(r"/\d+", parts.path or "") else ""
    return urlunsplit((scheme, netloc, path, "", ""))


def record_to_payload(record: ProjectionRecord) -> dict[str, Any]:
    index = record.index.model_dump(mode="json", exclude={"root"})
    return {
        "schema_version": record.key.schema_version,
        "namespace": record.key.namespace,
        "source_id": record.key.source_id,
        "projection_signature": record.key.projection_signature,
        "index": index,
    }


def projection_record_from_payload(
    key: ProjectionKey, payload: dict[str, Any], *, root: Path
) -> ProjectionRecord:
    if payload["schema_version"] != key.schema_version:
        raise ValueError("projection payload schema_version mismatch")
    if payload["namespace"] != key.namespace:
        raise ValueError("projection payload namespace mismatch")
    if payload["source_id"] != key.source_id:
        raise ValueError("projection payload source_id mismatch")
    if payload["projection_signature"] != key.projection_signature:
        raise ValueError("projection payload signature mismatch")
    index_payload = dict(payload["index"])
    index_payload["root"] = root
    return ProjectionRecord(key=key, index=WikiIndex.model_validate(index_payload))
