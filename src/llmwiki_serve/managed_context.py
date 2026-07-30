from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .models import GraphEdge, SearchResult, WikiIndex, WikiPage
from .search import role_rank, to_result, tokenize, unique_tokens

MANAGED_CONTEXT_SCHEMA_VERSION = "managed-context-v1"
MANAGED_CONTEXT_CONFIG_VERSION = "local-sidecar-v1"
STATE_DIR_ENV = "LLMWIKI_SERVE_STATE_DIR"
MANAGED_CONTEXT_STATE_DIR_ENV = "LLMWIKI_MANAGED_CONTEXT_STATE_DIR"
MANAGED_CONTEXT_ENABLED_ENV = "LLMWIKI_MANAGED_CONTEXT"
MANAGED_CONTEXT_NAMESPACE_ENV = "LLMWIKI_MANAGED_CONTEXT_NAMESPACE"
MANAGED_CONTEXT_BACKEND_ENV = "LLMWIKI_MANAGED_CONTEXT_BACKEND"
DEFAULT_MANAGED_CONTEXT_NAMESPACE = "default"
DEFAULT_MAX_MANAGED_BOOST = 0.05
DEFAULT_LEXICAL_TIE_BAND = 0.05
DEFAULT_HIT_HALF_LIFE_SECONDS = 7 * 24 * 60 * 60
DEFAULT_MAX_HIT_COUNTER = 10.0
DEFAULT_HIT_INCREMENT = 1.0
DEFAULT_MAX_HIT_ENTRIES = 256
DEFAULT_ORIENTATION_LIMIT = 3
DEFAULT_QUERY_ORIENTATION_LIMIT = 2
DEFAULT_ORIENTATION_SNIPPET_CHARS = 160
DEFAULT_ORIENTATION_MIN_EVIDENCE_SCORE = 0.2
SIDECAR_RECORD_READ_MAX_BYTES = 512 * 1024
SIDECAR_SALT_READ_MAX_BYTES = 16 * 1024
SIDECAR_LOCK_TIMEOUT_SECONDS = 30.0
SIDECAR_LOCK_RETRY_SECONDS = 0.01
SIDECAR_LOCK_STALE_SECONDS = 1.0
AUTHORED_ORIENTATION_PAGE_NAMES = frozenset({"hot.md", "index.md", "overview.md", "quickstart.md"})
MANAGED_ORIENTATION_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "could",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "please",
        "show",
        "tell",
        "that",
        "the",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "with",
        "would",
    }
)

ManagedContextBackend = Literal["local-sidecar"]

_PATH_LOCKS: dict[Path, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class ManagedContextConfig:
    enabled: bool = False
    backend: ManagedContextBackend = "local-sidecar"
    namespace: str = DEFAULT_MANAGED_CONTEXT_NAMESPACE
    state_dir: Path | None = None
    namespace_secret: str | None = None
    max_boost: float = DEFAULT_MAX_MANAGED_BOOST
    lexical_tie_band: float = DEFAULT_LEXICAL_TIE_BAND
    hit_half_life_seconds: float = DEFAULT_HIT_HALF_LIFE_SECONDS
    max_hit_counter: float = DEFAULT_MAX_HIT_COUNTER
    hit_increment: float = DEFAULT_HIT_INCREMENT
    max_hit_entries: int = DEFAULT_MAX_HIT_ENTRIES
    orientation_limit: int = DEFAULT_ORIENTATION_LIMIT
    query_orientation_limit: int = DEFAULT_QUERY_ORIENTATION_LIMIT
    orientation_snippet_chars: int = DEFAULT_ORIENTATION_SNIPPET_CHARS
    orientation_min_evidence_score: float = DEFAULT_ORIENTATION_MIN_EVIDENCE_SCORE


@dataclass(frozen=True)
class ManagedContextScope:
    namespace: str
    source_id: str
    adapter_kind: str
    projection_signature_digest: str
    source_signature_digest: str

    @property
    def namespace_digest(self) -> str:
        return stable_digest(self.namespace)

    @property
    def source_id_digest(self) -> str:
        return stable_digest(self.source_id)


@dataclass(frozen=True)
class ManagedPageHit:
    page_key: str
    counter: float
    last_hit_at: float


@dataclass(frozen=True)
class ManagedContextRecord:
    scope: ManagedContextScope
    generation: int
    created_at: float
    updated_at: float
    hits: dict[str, ManagedPageHit]

    def decayed_boost(
        self,
        page_key: str,
        *,
        now: float,
        config: ManagedContextConfig,
    ) -> float:
        hit = self.hits.get(page_key)
        if hit is None:
            return 0.0
        counter = decayed_counter(hit.counter, hit.last_hit_at, now, config.hit_half_life_seconds)
        if counter <= 0:
            return 0.0
        normalized = min(counter, config.max_hit_counter) / config.max_hit_counter
        return min(config.max_boost, config.max_boost * normalized)


ManagedContextOption = bool | ManagedContextConfig | None
ManagedPrior = Callable[[SearchResult], float]


def managed_context_config_from_env() -> ManagedContextConfig:
    enabled = env_bool(os.getenv(MANAGED_CONTEXT_ENABLED_ENV))
    backend = os.getenv(MANAGED_CONTEXT_BACKEND_ENV) or "local-sidecar"
    namespace = os.getenv(MANAGED_CONTEXT_NAMESPACE_ENV) or DEFAULT_MANAGED_CONTEXT_NAMESPACE
    state_dir_env = os.getenv(MANAGED_CONTEXT_STATE_DIR_ENV)
    config = ManagedContextConfig(
        enabled=enabled,
        backend=backend,  # type: ignore[arg-type]
        namespace=namespace,
        state_dir=Path(state_dir_env).expanduser() if state_dir_env else None,
    )
    return validate_managed_context_config(config)


def normalize_managed_context_config(value: ManagedContextOption) -> ManagedContextConfig:
    if value is None:
        return validate_managed_context_config(ManagedContextConfig())
    if isinstance(value, bool):
        return validate_managed_context_config(ManagedContextConfig(enabled=value))
    return validate_managed_context_config(value)


def validate_managed_context_config(config: ManagedContextConfig) -> ManagedContextConfig:
    if not config.enabled:
        return config
    if config.backend != "local-sidecar":
        raise ValueError("managed_context backend must be 'local-sidecar'")
    if not config.namespace.strip():
        raise ValueError("managed_context namespace must be non-empty")
    if config.max_boost < 0:
        raise ValueError("managed_context max_boost must be non-negative")
    if config.lexical_tie_band < 0:
        raise ValueError("managed_context lexical_tie_band must be non-negative")
    if config.hit_half_life_seconds <= 0:
        raise ValueError("managed_context hit_half_life_seconds must be positive")
    if config.max_hit_counter <= 0:
        raise ValueError("managed_context max_hit_counter must be positive")
    if config.hit_increment <= 0:
        raise ValueError("managed_context hit_increment must be positive")
    if config.max_hit_entries <= 0:
        raise ValueError("managed_context max_hit_entries must be positive")
    if config.orientation_limit <= 0:
        raise ValueError("managed_context orientation_limit must be positive")
    if config.query_orientation_limit <= 0:
        raise ValueError("managed_context query_orientation_limit must be positive")
    if config.orientation_snippet_chars < 0:
        raise ValueError("managed_context orientation_snippet_chars must be non-negative")
    if config.orientation_min_evidence_score < 0:
        raise ValueError("managed_context orientation_min_evidence_score must be non-negative")
    return config


def env_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def default_managed_context_state_dir() -> Path:
    env_value = os.getenv(MANAGED_CONTEXT_STATE_DIR_ENV)
    if env_value:
        return Path(env_value).expanduser()
    state_env = os.getenv(STATE_DIR_ENV)
    if state_env:
        return Path(state_env).expanduser() / "managed-context"
    if os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        if local_app_data:
            return Path(local_app_data) / "llmwiki-serve" / "managed-context"
    xdg_state_home = os.getenv("XDG_STATE_HOME")
    if xdg_state_home:
        return Path(xdg_state_home).expanduser() / "llmwiki-serve" / "managed-context"
    return Path.home() / ".local" / "state" / "llmwiki-serve" / "managed-context"


def resolve_managed_context_state_dir(root: Path, config: ManagedContextConfig) -> Path:
    state_dir = (config.state_dir or default_managed_context_state_dir()).expanduser()
    lexical_state_dir = lexical_absolute_path(state_dir)
    lexical_root = lexical_absolute_path(root.expanduser())
    resolved_state_dir = state_dir.resolve(strict=False)
    resolved_root = root.expanduser().resolve(strict=False)
    if same_or_relative_to(lexical_state_dir, lexical_root) or same_or_relative_to(
        resolved_state_dir, resolved_root
    ):
        raise ValueError("managed_context state_dir must be outside the served source root")
    return resolved_state_dir


def lexical_absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(path))


def same_or_relative_to(child: Path, parent: Path) -> bool:
    if os.path.normcase(str(child)) == os.path.normcase(str(parent)):
        return True
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


class ManagedContextRuntime:
    def __init__(
        self,
        root: Path,
        config: ManagedContextOption = None,
    ) -> None:
        self.config = normalize_managed_context_config(config)
        self.store: LocalManagedContextStore | None = None
        if self.config.enabled:
            self.store = LocalManagedContextStore(
                resolve_managed_context_state_dir(root, self.config),
                namespace=self.config.namespace,
                namespace_secret=self.config.namespace_secret,
            )

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def active_for_index(self, index: WikiIndex) -> bool:
        return self.config.enabled and managed_context_applies_to_index(index)

    def scope(
        self,
        *,
        source_id: str,
        adapter_kind: str,
        projection_signature_digest: str,
        source_signature_digest: str,
    ) -> ManagedContextScope:
        return ManagedContextScope(
            namespace=self.config.namespace,
            source_id=source_id,
            adapter_kind=adapter_kind,
            projection_signature_digest=projection_signature_digest,
            source_signature_digest=source_signature_digest,
        )

    def orientation(
        self,
        index: WikiIndex,
        scope: ManagedContextScope,
        *,
        query: str,
        evidence_results: Sequence[SearchResult],
        include_drafts: bool,
        snippet_chars: int | None,
        now: float | None = None,
    ) -> list[SearchResult] | None:
        if not self.active_for_index(index):
            return None
        related_page_ids = query_related_orientation_page_ids(
            index,
            query,
            evidence_results,
            include_drafts=include_drafts,
            min_evidence_score=self.config.orientation_min_evidence_score,
        )
        if related_page_ids == set():
            return []
        record = self._record(scope, now=now)
        pages = derived_orientation_pages(
            index,
            scope,
            record=record,
            config=self.config,
            store=self.store,
            include_drafts=include_drafts,
            now=time_value(now),
        )
        if related_page_ids is not None:
            pages = [page for page in pages if page.id in related_page_ids]
        limit = self.config.orientation_limit
        if related_page_ids is not None:
            limit = min(limit, self.config.query_orientation_limit)
        effective_snippet_chars = managed_orientation_snippet_chars(snippet_chars, self.config)
        return [
            to_result(
                page,
                score=1.0 - rank * 0.01,
                query_tokens=[],
                route="orientation",
                snippet_chars=effective_snippet_chars,
            )
            for rank, page in enumerate(pages[:limit])
        ]

    def prior(
        self,
        index: WikiIndex,
        scope: ManagedContextScope,
        *,
        now: float | None = None,
    ) -> ManagedPrior | None:
        if not self.active_for_index(index):
            return None
        store = self.store
        initialized = False
        record: ManagedContextRecord | None = None
        page_keys: dict[str, str] = {}

        def boost(result: SearchResult) -> float:
            nonlocal initialized, record, page_keys
            if not initialized:
                initialized = True
                secret = (
                    store.secret(create=False)
                    if store is not None
                    else (self.config.namespace_secret)
                )
                if not secret:
                    return 0.0
                record = self._record(scope, now=now)
                if record is None:
                    return 0.0
                page_keys = page_keys_by_id(index.pages, scope, secret)
            page_key = page_keys.get(result.page_id)
            if page_key is None or record is None:
                return 0.0
            return record.decayed_boost(page_key, now=time_value(now), config=self.config)

        return boost

    def record_hits(
        self,
        index: WikiIndex,
        scope: ManagedContextScope,
        page_ids: Iterable[str],
        *,
        include_drafts: bool,
        now: float | None = None,
    ) -> None:
        if not self.active_for_index(index) or self.store is None:
            return
        visible = {
            page.id: page for page in index.pages if include_drafts or page.approved_for_serving
        }
        selected_ids = dedupe(item for item in page_ids if item in visible)
        if not selected_ids:
            return
        if self.store.record_lock_is_stale(scope):
            return
        secret = self.store.secret(create=True)
        if secret is None:
            return
        page_keys = [opaque_page_key(scope, page_id, secret) for page_id in selected_ids]
        self.store.update_hits(
            scope,
            page_keys,
            config=self.config,
            now=time_value(now),
        )

    def _record(
        self,
        scope: ManagedContextScope,
        *,
        now: float | None = None,
    ) -> ManagedContextRecord | None:
        if self.store is None:
            return None
        return self.store.read(scope, config=self.config, now=time_value(now))


class LocalManagedContextStore:
    def __init__(
        self,
        state_dir: Path,
        *,
        namespace: str,
        namespace_secret: str | None = None,
    ) -> None:
        self.state_dir = state_dir
        self.namespace = namespace
        self.namespace_secret = namespace_secret

    def record_path(self, scope: ManagedContextScope) -> Path:
        return self.namespace_dir / f"{record_digest(scope)}.json"

    def record_lock_is_stale(self, scope: ManagedContextScope) -> bool:
        return sidecar_lock_is_stale(sidecar_lock_path(self.record_path(scope)))

    @property
    def namespace_dir(self) -> Path:
        return self.state_dir / f"ns-{stable_digest(self.namespace)[:24]}"

    def read(
        self,
        scope: ManagedContextScope,
        *,
        config: ManagedContextConfig,
        now: float,
    ) -> ManagedContextRecord | None:
        return self._read(scope, config=config, now=now, skip_locked=True)

    def _read(
        self,
        scope: ManagedContextScope,
        *,
        config: ManagedContextConfig,
        now: float,
        skip_locked: bool,
    ) -> ManagedContextRecord | None:
        path = self.record_path(scope)
        if skip_locked and sidecar_lock_present(path):
            return None
        payload = read_json_payload(path, max_bytes=SIDECAR_RECORD_READ_MAX_BYTES)
        if payload is None:
            return None
        return record_from_payload(payload, scope=scope, config=config, now=now)

    def update_hits(
        self,
        scope: ManagedContextScope,
        page_keys: Sequence[str],
        *,
        config: ManagedContextConfig,
        now: float,
    ) -> None:
        if not page_keys:
            return
        path = self.record_path(scope)
        try:
            with sidecar_file_lock(path):
                current = self._read(scope, config=config, now=now, skip_locked=False)
                effective_now = max(now, current.updated_at) if current else now
                hits = decayed_hits(
                    current.hits if current else {},
                    config=config,
                    now=effective_now,
                )
                for page_key in dedupe(page_keys):
                    existing = hits.get(page_key)
                    counter = existing.counter if existing else 0.0
                    hits[page_key] = ManagedPageHit(
                        page_key=page_key,
                        counter=round(
                            min(config.max_hit_counter, counter + config.hit_increment), 6
                        ),
                        last_hit_at=effective_now,
                    )
                created_at = current.created_at if current else effective_now
                generation = current.generation + 1 if current else 1
                record = ManagedContextRecord(
                    scope=scope,
                    generation=generation,
                    created_at=created_at,
                    updated_at=effective_now,
                    hits=trim_hits(hits, config),
                )
                atomic_write_json(path, record_to_payload(record))
        except OSError:
            return

    def secret(self, *, create: bool) -> str | None:
        if self.namespace_secret:
            return self.namespace_secret
        path = self.namespace_dir / "salt.json"
        existing = read_salt(path)
        if existing:
            return existing
        if not create:
            return None

        try:
            with sidecar_file_lock(path):
                existing = read_salt(path)
                if existing:
                    return existing
                salt = secrets.token_hex(32)
                payload = {
                    "schema_version": MANAGED_CONTEXT_SCHEMA_VERSION,
                    "kind": "managed-context-salt",
                    "salt": salt,
                }
                atomic_write_json(path, payload)
                return salt
        except OSError:
            return None


def read_salt(path: Path) -> str | None:
    payload = read_json_payload(path, max_bytes=SIDECAR_SALT_READ_MAX_BYTES)
    if payload is None:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != MANAGED_CONTEXT_SCHEMA_VERSION:
        return None
    if payload.get("kind") != "managed-context-salt":
        return None
    value = payload.get("salt")
    if not isinstance(value, str) or not value:
        return None
    return value


def read_json_payload(path: Path, *, max_bytes: int) -> Any | None:
    try:
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError:
        return None
    if len(data) > max_bytes:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def record_from_payload(
    payload: Any,
    *,
    scope: ManagedContextScope,
    config: ManagedContextConfig,
    now: float,
) -> ManagedContextRecord | None:
    if not isinstance(payload, dict):
        return None
    expected = {
        "schema_version": MANAGED_CONTEXT_SCHEMA_VERSION,
        "config_version": MANAGED_CONTEXT_CONFIG_VERSION,
        "namespace_digest": scope.namespace_digest,
        "source_id_digest": scope.source_id_digest,
        "adapter_kind": scope.adapter_kind,
        "projection_signature_digest": scope.projection_signature_digest,
        "source_signature_digest": scope.source_signature_digest,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        return None
    raw_hits = payload.get("page_hit_prior")
    if not isinstance(raw_hits, list):
        return None
    hits: dict[str, ManagedPageHit] = {}
    for item in raw_hits:
        if not isinstance(item, dict):
            continue
        page_key = item.get("page_key")
        if not isinstance(page_key, str) or not page_key.startswith("pk_"):
            continue
        raw_counter = item.get("counter")
        raw_last_hit_at = item.get("last_hit_at")
        if raw_counter is None or raw_last_hit_at is None:
            continue
        try:
            counter = float(raw_counter)
            last_hit_at = float(raw_last_hit_at)
        except (TypeError, ValueError):
            continue
        if counter <= 0 or not math.isfinite(counter) or not math.isfinite(last_hit_at):
            continue
        decayed = decayed_counter(counter, last_hit_at, now, config.hit_half_life_seconds)
        if decayed <= 0:
            continue
        hits[page_key] = ManagedPageHit(
            page_key=page_key,
            counter=round(min(config.max_hit_counter, counter), 6),
            last_hit_at=last_hit_at,
        )
    try:
        generation = int(payload.get("generation") or 0)
        created_at = float(payload.get("created_at") or now)
        updated_at = float(payload.get("updated_at") or now)
    except (TypeError, ValueError):
        return None
    if (
        generation < 1
        or not math.isfinite(created_at)
        or not math.isfinite(updated_at)
        or updated_at < created_at
    ):
        return None
    return ManagedContextRecord(
        scope=scope,
        generation=generation,
        created_at=created_at,
        updated_at=updated_at,
        hits=trim_hits(hits, config),
    )


def record_to_payload(record: ManagedContextRecord) -> dict[str, Any]:
    return {
        "schema_version": MANAGED_CONTEXT_SCHEMA_VERSION,
        "config_version": MANAGED_CONTEXT_CONFIG_VERSION,
        "namespace_digest": record.scope.namespace_digest,
        "source_id_digest": record.scope.source_id_digest,
        "adapter_kind": record.scope.adapter_kind,
        "projection_signature_digest": record.scope.projection_signature_digest,
        "source_signature_digest": record.scope.source_signature_digest,
        "created_at": round(record.created_at, 6),
        "updated_at": round(record.updated_at, 6),
        "generation": record.generation,
        "orientation_generation": {
            "strategy": "projection-derived-v1",
            "generated_at": round(record.updated_at, 6),
        },
        "page_hit_prior": [
            {
                "page_key": hit.page_key,
                "counter": round(hit.counter, 6),
                "last_hit_at": round(hit.last_hit_at, 6),
            }
            for hit in sorted(record.hits.values(), key=lambda item: item.page_key)
        ],
    }


def managed_context_applies_to_index(index: WikiIndex) -> bool:
    return (
        index.adapter == "generic-markdown"
        and index.implementation == "generic-markdown"
        and not has_authored_orientation_pages(index.pages)
    )


def has_authored_orientation_pages(pages: Sequence[WikiPage]) -> bool:
    return any(
        page.role in {"hot", "index", "overview"}
        and Path(page.path).name.casefold() in AUTHORED_ORIENTATION_PAGE_NAMES
        for page in pages
    )


def derived_orientation_pages(
    index: WikiIndex,
    scope: ManagedContextScope,
    *,
    record: ManagedContextRecord | None,
    config: ManagedContextConfig,
    store: LocalManagedContextStore | None,
    include_drafts: bool,
    now: float,
) -> list[WikiPage]:
    pages = [page for page in index.pages if include_drafts or page.approved_for_serving]
    if not pages:
        return []
    inbound, outbound = page_degree_counts(index.edges)
    prior_boosts: dict[str, float] = {}
    secret = store.secret(create=False) if store is not None else config.namespace_secret
    if record is not None and secret:
        for page in pages:
            page_key = opaque_page_key(scope, page.id, secret)
            prior_boosts[page.id] = record.decayed_boost(page_key, now=now, config=config)
    return sorted(
        pages,
        key=lambda page: (
            -orientation_score(page, inbound, outbound, prior_boosts.get(page.id, 0.0)),
            role_rank(page.role),
            page.path,
        ),
    )


def query_related_orientation_page_ids(
    index: WikiIndex,
    query: str,
    evidence_results: Sequence[SearchResult],
    *,
    include_drafts: bool,
    min_evidence_score: float,
) -> set[str] | None:
    tokens = significant_query_tokens(query)
    if not query.strip():
        return None
    if not tokens or not evidence_results:
        return set()
    visible = {page.id: page for page in index.pages if include_drafts or page.approved_for_serving}
    matched: set[str] = set()
    for result in evidence_results:
        if result.score < min_evidence_score:
            continue
        page = visible.get(result.page_id)
        if page is None:
            continue
        if page_matches_query(page, tokens):
            matched.add(page.id)
    if not matched:
        return set()
    return matched | adjacent_page_ids(matched, index.edges)


def significant_query_tokens(query: str) -> list[str]:
    return [
        token
        for token in unique_tokens(tokenize(query))
        if token not in MANAGED_ORIENTATION_STOPWORDS and not low_information_token(token)
    ]


def low_information_token(token: str) -> bool:
    return len(token) == 1 and token.isalpha()


def page_matches_query(page: WikiPage, tokens: Sequence[str]) -> bool:
    haystack = " ".join(
        [
            page.title,
            page.path,
            page.summary,
            page.text,
            " ".join(page.tags),
            " ".join(page.source_refs),
            " ".join(page.headings),
        ]
    )
    page_tokens = set(tokenize(haystack))
    return any(token in page_tokens for token in tokens)


def adjacent_page_ids(page_ids: set[str], edges: Sequence[GraphEdge]) -> set[str]:
    nodes = {f"page:{page_id}" for page_id in page_ids}
    related = set(page_ids)
    for edge in edges:
        if edge.source in nodes and edge.target.startswith("page:"):
            related.add(edge.target.removeprefix("page:"))
        if edge.target in nodes and edge.source.startswith("page:"):
            related.add(edge.source.removeprefix("page:"))
    return related


def managed_orientation_snippet_chars(
    requested: int | None,
    config: ManagedContextConfig,
) -> int | None:
    if requested is not None:
        return requested
    return config.orientation_snippet_chars


def orientation_score(
    page: WikiPage,
    inbound: dict[str, int],
    outbound: dict[str, int],
    prior_boost: float,
) -> float:
    page_node = f"page:{page.id}"
    folder_depth = max(0, len(Path(page.path).parts) - 1)
    shallow_bonus = 1.0 / (1 + folder_depth)
    return (
        inbound.get(page_node, 0) * 2.0
        + outbound.get(page_node, 0) * 0.4
        + min(len(page.source_refs), 5) * 0.5
        + min(len(page.headings), 8) * 0.2
        + shallow_bonus
        + prior_boost
    )


def page_degree_counts(edges: Sequence[GraphEdge]) -> tuple[dict[str, int], dict[str, int]]:
    inbound: dict[str, int] = {}
    outbound: dict[str, int] = {}
    for edge in edges:
        if edge.target.startswith("page:"):
            inbound[edge.target] = inbound.get(edge.target, 0) + 1
        if edge.source.startswith("page:"):
            outbound[edge.source] = outbound.get(edge.source, 0) + 1
    return inbound, outbound


def page_keys_by_id(
    pages: Sequence[WikiPage],
    scope: ManagedContextScope,
    secret: str,
) -> dict[str, str]:
    return {page.id: opaque_page_key(scope, page.id, secret) for page in pages}


def opaque_page_key(scope: ManagedContextScope, page_id: str, secret: str) -> str:
    payload = "\0".join(
        [
            MANAGED_CONTEXT_SCHEMA_VERSION,
            scope.namespace_digest,
            scope.source_id_digest,
            scope.adapter_kind,
            scope.projection_signature_digest,
            scope.source_signature_digest,
            page_id,
        ]
    )
    digest = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"pk_{digest}"


def source_signature_digest(signature: tuple[tuple[str, int, int], ...] | None) -> str:
    if not signature:
        return ""
    entries = sorted((mtime, size) for _path, mtime, size in signature)
    payload = "\n".join(
        "\t".join([str(index), str(mtime), str(size)])
        for index, (mtime, size) in enumerate(entries)
    )
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def stable_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def record_digest(scope: ManagedContextScope) -> str:
    payload = "\0".join(
        [
            MANAGED_CONTEXT_SCHEMA_VERSION,
            scope.namespace_digest,
            scope.source_id_digest,
            scope.adapter_kind,
            scope.projection_signature_digest,
            scope.source_signature_digest,
        ]
    )
    return stable_digest(payload)


def decayed_counter(
    counter: float,
    last_hit_at: float,
    now: float,
    half_life_seconds: float,
) -> float:
    if (
        half_life_seconds <= 0
        or not math.isfinite(counter)
        or not math.isfinite(last_hit_at)
        or not math.isfinite(now)
    ):
        return 0.0
    elapsed = max(0.0, now - last_hit_at)
    return float(counter * (0.5 ** (elapsed / half_life_seconds)))


def decayed_hits(
    hits: dict[str, ManagedPageHit],
    *,
    config: ManagedContextConfig,
    now: float,
) -> dict[str, ManagedPageHit]:
    result: dict[str, ManagedPageHit] = {}
    for hit in hits.values():
        counter = decayed_counter(
            hit.counter,
            hit.last_hit_at,
            now,
            config.hit_half_life_seconds,
        )
        if counter <= 0.000001:
            continue
        result[hit.page_key] = ManagedPageHit(
            page_key=hit.page_key,
            counter=round(min(config.max_hit_counter, counter), 6),
            last_hit_at=now,
        )
    return trim_hits(result, config)


def trim_hits(
    hits: dict[str, ManagedPageHit],
    config: ManagedContextConfig,
) -> dict[str, ManagedPageHit]:
    ordered = sorted(
        hits.values(),
        key=lambda item: (-item.counter, -item.last_hit_at, item.page_key),
    )
    return {hit.page_key: hit for hit in ordered[: config.max_hit_entries]}


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


@contextmanager
def sidecar_file_lock(path: Path) -> Iterator[None]:
    lock_path = sidecar_lock_path(path)
    thread_lock = path_lock(lock_path)
    with thread_lock:
        acquire_directory_lock(lock_path)
        try:
            yield
        finally:
            release_directory_lock(lock_path)


def acquire_directory_lock(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + SIDECAR_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            path.mkdir()
            return
        except FileExistsError as exc:
            if sidecar_lock_is_stale(path):
                raise TimeoutError(f"stale sidecar lock: {path}") from exc
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for sidecar lock: {path}") from exc
            time.sleep(SIDECAR_LOCK_RETRY_SECONDS)
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(SIDECAR_LOCK_RETRY_SECONDS)


def release_directory_lock(path: Path) -> None:
    with suppress(FileNotFoundError):
        path.rmdir()


def sidecar_lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lockdir")


def sidecar_lock_present(path: Path) -> bool:
    try:
        return sidecar_lock_path(path).exists()
    except OSError:
        return True


def sidecar_lock_is_stale(path: Path) -> bool:
    try:
        return time.time() - path.stat().st_mtime >= SIDECAR_LOCK_STALE_SECONDS
    except OSError:
        return False


def fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    with suppress(OSError):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def path_lock(path: Path) -> threading.Lock:
    resolved = path.resolve(strict=False)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(resolved)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[resolved] = lock
        return lock


def dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def time_value(value: float | None) -> float:
    return time.time() if value is None else value
