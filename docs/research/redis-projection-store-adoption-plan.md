# Redis Projection Store Adoption Plan

Status: implemented for optional projection-store Redis/Valkey cache; RedisVL
semantic search remains deferred; production docs and live Redis validation are
recorded with sanitized details
Date: 2026-07-14
Scope: `llmwiki-serve` production hardening
Out of scope: mem0-backed projection storage

Decision alignment as of 2026-07-22: the accepted boundary is recorded in
`docs/decisions/2026-07-22-redis-projection-store-derived-cache-boundary.md`.
Redis/Valkey is an optional derived projection cache only. It is not source of
truth, not a freshness authority, and not part of the default install. Redis
payloads are sensitive because they may include derived page text,
frontmatter, source refs, graph metadata, and drafts. It is not orchestration
state, prompt/history cache, semantic/vector index, or agent memory; those
belong in bridge/chat/runtime systems such as `llmwiki-agent-bridge`,
`llmwiki-chat`, Hermes/DeepAgents, or the model runtime that owns conversation
state.

## Summary

`llmwiki-serve` should keep the current in-process memory behavior as the
default, then add Redis/Valkey as an optional projection cache backend. This
supports production-style deployments where multiple workers, multiple server
processes, or multiple source graphs should reuse derived projection artifacts
without rebuilding the same Markdown graph repeatedly.

This is not a move to make Redis the source of truth. Markdown-compatible source
folders and sidecar graph files remain canonical. Redis stores derived,
versioned, disposable artifacts keyed by source identity and projection
signature.

The first Redis work should focus on deterministic projection/cache reuse. A
RedisVL semantic/vector index can be added later as an optional search backend,
after the projection-store contract is stable.

## Goals

1. Introduce a `ProjectionStore` interface.
2. Keep `InMemoryProjectionStore` as the default behavior.
3. Add optional `RedisProjectionStore`.
4. Namespace Redis keys so one Redis/Valkey instance can hold many LLMWiki
   graphs safely.
5. Define Redis outage and fallback behavior.
6. Add RedisVL as a later optional semantic index path without changing the
   default lexical search semantics.
7. Use Python optional dependency extras so production backends are easy to
   install without making local quickstart heavier.

## Non-Goals

- Do not use mem0 as the `llmwiki-serve` projection backend.
- Do not make Redis or RedisVL required for the quickstart.
- Do not make Redis authoritative for pages, graph facts, review state, or
  source refs.
- Do not put prompt/history memory, runtime traces, orchestration state, prefix
  caches, or agent session state in `llmwiki-serve` Redis.
- Do not merge multiple graphs into one graph in this work.
- Do not change the default search ranking semantics in the first Redis PR.
- Do not expose local filesystem paths in Redis keys or network responses.

## Current Baseline

Today `LlmWikiService` owns:

- the source root,
- the cached `WikiIndex`,
- cached `_IndexViews` search corpora,
- source signature and projection signature,
- source signature cache,
- refresh interval behavior.

The service rebuilds with `project_wiki(load_wiki(root))` when file signatures
change. Search corpus caching is process-local. `--refresh-interval-seconds=0`
keeps strict per-request freshness by checking source signatures on each
request.

This is a good local-first default. Redis should extend it, not replace it.

## Target Architecture

```text
Markdown / sidecar graph files
  -> SignatureProvider
       computes source signature and projection signature
  -> ProjectionBuilder
       load_wiki(root) -> project_wiki(...) -> WikiIndex
  -> ProjectionStore
       memory by default
       optional Redis/Valkey for shared, derived projection artifacts
  -> SearchProvider
       lexical SearchCorpus by default
       optional RedisVL semantic index later
  -> API surfaces
       HTTP, MCP Streamable HTTP, MCP-style JSON-RPC, optional A2A-style compat
```

The source signature remains the freshness authority. Redis can save rebuild
work, but it does not remove the need to detect source changes.

## Phase 1: ProjectionStore Interface

### Objective

Extract the projection cache boundary without changing behavior.

### Proposed Types

```python
@dataclass(frozen=True)
class ProjectionKey:
    schema_version: str
    namespace: str
    source_id: str
    projection_signature: str

@dataclass(frozen=True)
class ProjectionRecord:
    key: ProjectionKey
    index: WikiIndex
    created_at: datetime
    service_version: str
    adapter: str
    implementation: str

class ProjectionStore(Protocol):
    def get(self, key: ProjectionKey) -> ProjectionRecord | None: ...
    def put(self, record: ProjectionRecord) -> None: ...
    def invalidate_source(self, namespace: str, source_id: str) -> None: ...
```

### Implementation Notes

- Move `_index`, `_views`, `_signature`, and `_projection_signature` ownership
  into a clear service/cache boundary.
- Preserve `LlmWikiService.index(refresh=False)` behavior.
- Start with synchronous methods because current service code is synchronous.
- Keep `_IndexViews` in process memory initially. Do not serialize search
  corpus until the `WikiIndex` persistence path is stable.

### Tests

- Existing service tests pass unchanged.
- Explicit tests that memory store produces byte-for-byte equivalent API
  responses to current behavior.
- Refresh tests remain strict by default.

## Phase 2: InMemoryProjectionStore Default

### Objective

Make the current behavior an explicit backend.

### User-Facing Behavior

No CLI change required for default usage:

```powershell
uv run llmwiki-serve serve ./wiki --port 8765
```

Optional explicit form:

```powershell
uv run llmwiki-serve serve ./wiki --projection-store memory
```

### Acceptance Criteria

- Default CLI behavior is unchanged.
- `--refresh-interval-seconds` behavior is unchanged.
- No new runtime dependency.
- No network or Redis requirement.
- OpenAPI output remains compatible unless new optional fields are added.

## Phase 3: RedisProjectionStore Optional Backend

### Objective

Add a Redis/Valkey-backed read-through projection cache.

### Dependency Shape

Use an optional extra:

```toml
[project.optional-dependencies]
redis = [
  "redis>=5",
]
```

RedisVL should not be included in this phase.

The install UX should be:

```powershell
# Default local-first server: no external cache dependency
pip install llmwiki-serve

# Redis/Valkey projection cache support
pip install "llmwiki-serve[redis]"

# uv equivalent
uv add "llmwiki-serve[redis]"
```

If a user selects Redis without installing the extra, the CLI and library path
should fail with a direct recovery message:

```text
Redis projection store requires llmwiki-serve[redis].
Install with: pip install "llmwiki-serve[redis]"
```

This is better than an unhandled `ModuleNotFoundError` because it keeps the
optional backend discoverable for OSS users and coding agents following docs.

### CLI Shape

```powershell
uv run llmwiki-serve serve ./wiki `
  --projection-store redis `
  --redis-url redis://127.0.0.1:6379/0 `
  --cache-namespace local
```

Environment alternatives:

```text
LLMWIKI_PROJECTION_STORE=redis
LLMWIKI_REDIS_URL=redis://127.0.0.1:6379/0
LLMWIKI_CACHE_NAMESPACE=local
```

CLI flags should win over environment variables.

### Read-Through Flow

```text
request arrives
  -> check refresh interval
  -> compute or reuse source signature snapshot
  -> compute projection signature
  -> build ProjectionKey(namespace, source_id, projection_signature)
  -> Redis GET projection
       hit: hydrate WikiIndex and use it
       miss: build projection from disk, Redis SET, use it
  -> build local _IndexViews if needed
```

### Serialization

Serialize `WikiIndex` as contract-safe JSON:

- Do not store absolute local root paths as the cross-process identity.
- Store portable metadata and pages/nodes/edges.
- Reattach the local service root on hydration.
- Include `schema_version` and package version in the payload.

Potential payload:

```json
{
  "schema_version": "projection-store-v1",
  "source_id": "project-alpha",
  "projection_signature": "sha256:...",
  "service_version": "0.1.0",
  "index": {
    "title": "...",
    "description": "...",
    "adapter": "...",
    "implementation": "...",
    "metadata": {},
    "pages": [],
    "nodes": [],
    "edges": []
  }
}
```

### Acceptance Criteria

- Redis backend returns the same `/manifest`, `/source-bundle`, `/query`,
  `/search`, `/read`, and `/graph` payloads as memory backend.
- Redis miss builds from disk once and stores the projection.
- Redis hit does not call `project_wiki(load_wiki(root))`.
- Source changes produce a new projection signature and therefore a new key.
- Draft filtering remains request-time behavior and does not leak drafts.

## Phase 4: Redis Namespace and Multi-Graph Cache Layout

### Objective

Allow one Redis/Valkey service to safely hold derived projections for multiple
LLMWiki graphs and multiple deployments.

### Key Layout

```text
llmwiki:{namespace}:sources:{source_id}:latest
llmwiki:{namespace}:projections:{source_id}:{bundle_id}
llmwiki:{namespace}:locks:{source_id}:{projection_signature}
llmwiki:{namespace}:stats:{source_id}
```

Where:

- `namespace` separates local/dev/staging/prod or tenant boundaries.
- `source_id` identifies the served knowledge source.
- `bundle_id` combines `source_id` and content-derived projection signature.
- `projection_signature` is content-derived and changes when projection inputs
  change.

### Source ID Collision Handling

The current `source_id` is derived from wiki title or folder name. Production
deployments should allow an explicit source id:

```powershell
uv run llmwiki-serve serve ./wiki `
  --source-id project-alpha `
  --projection-store redis `
  --cache-namespace acme-prod
```

If no explicit source id is provided, keep current behavior.

### Multi-Graph Clarification

This phase enables one Redis instance to cache many graphs. It does not mean one
`llmwiki-serve` process serves many roots. Multi-root serving should be a
separate `SourceRegistry` feature and should not block Redis cache adoption.

## Phase 5: Redis Outage and Fallback Behavior

### Objective

Make Redis optional in practice, not just in packaging.

### Fallback Modes

Current implemented CLI:

```powershell
--redis-failure-policy fallback-local
--redis-failure-policy fail-fast
```

Default recommendation: `fallback-local`.

Policy behavior:

- `fallback-local`
  - If Redis is unavailable, log a warning and use memory backend for the
    process lifetime or until a retry window expires.
  - Best for local/dev/small production.

- `fail-fast`
  - Startup fails if Redis cannot be reached.
  - Best when operators require cache sharing and want misconfiguration to be
    visible immediately.

The earlier `read-only-cache` sketch remains deferred because the current
operator contract only needs local fallback or fail-fast behavior.

### Observability

Add lightweight diagnostics:

```text
GET /health
GET /diagnostics/projection-store
```

The public health endpoint can remain simple, while diagnostics can show:

- backend type,
- Redis connection status,
- namespace,
- last cache hit/miss,
- last fallback reason,
- current source id and bundle id,
- a sanitized Redis endpoint label for UI status cards.

Avoid exposing credentials, local paths, raw Redis URLs, query parameters, raw
keys, or cached payloads. Sanitized endpoint labels should strip userinfo,
passwords, query parameters, and fragments.

### Tests

- Redis unavailable at startup with fallback-local.
- Redis unavailable at startup with fail-fast.
- Redis fails after first successful hit.
- Corrupt Redis payload is ignored and rebuilt from disk.
- Version mismatch causes miss/rebuild, not crash.

## Phase 6: RedisVL Semantic Index Optional Path

### Objective

Evaluate semantic/vector search as an optional backend after deterministic
projection caching is stable.

### Why Separate

Redis projection caching preserves current semantics. RedisVL semantic search
changes retrieval behavior by adding embeddings/vector ranking. That can improve
recall, but it also introduces:

- embedding model configuration,
- index rebuild lifecycle,
- ranking parity questions,
- additional dependencies,
- metadata filtering requirements,
- stronger privacy and cost considerations.

### Proposed Shape

Installation:

```powershell
pip install "llmwiki-serve[redisvl]"
uv add "llmwiki-serve[redisvl]"
```

Suggested packaging:

```toml
[project.optional-dependencies]
redis = [
  "redis>=5",
]
redisvl = [
  "redis>=5",
  "redisvl>=0.4",
]
```

Keep the dependency list explicit in `redisvl` instead of relying on package
self-extra references. This is simpler for packaging tools and easier for OSS
users to inspect.

Runtime:

```powershell
uv run llmwiki-serve serve ./wiki `
  --projection-store redis `
  --search-backend lexical

uv run llmwiki-serve serve ./wiki `
  --projection-store redis `
  --search-backend redisvl `
  --embedding-model text-embedding-... `
  --redis-url redis://127.0.0.1:6379/0
```

Default remains `lexical`.

### RedisVL Indexing Model

Index records should be derived from approved page-level or chunk-level
projection records:

```json
{
  "namespace": "acme-prod",
  "source_id": "project-alpha",
  "bundle_id": "project-alpha:sha256:abc123",
  "visibility": "approved",
  "page_id": "billing-architecture",
  "path": "billing-architecture.md",
  "title": "Billing Architecture",
  "role": "topic",
  "text": "...",
  "source_refs": ["Architecture Review"],
  "embedding": [0.01, 0.02]
}
```

Every query must filter by:

- namespace,
- source_id,
- bundle_id or latest pointer,
- visibility mode.

### Acceptance Criteria

- RedisVL is opt-in.
- Lexical search remains default and stable.
- Semantic search returns page ids and citations compatible with existing
  `SearchResult`.
- Query results never omit source identity in multi-source contexts.
- Reindex happens when projection signature changes.

## Configuration Matrix

| Use case | Projection store | Search backend | Notes |
| --- | --- | --- | --- |
| Local quickstart | memory | lexical | `pip install llmwiki-serve`, no external services |
| Large local graph | memory | lexical | Use `--refresh-interval-seconds` if acceptable |
| Multi-worker production | redis | lexical | `pip install "llmwiki-serve[redis]"` |
| Many graphs, one cache | redis | lexical | Namespace + explicit source ids |
| Semantic recall experiment | redis | redisvl | `pip install "llmwiki-serve[redisvl]"`, later opt-in phase |
| Agent/session memory | not serve | mem0 | Belongs in bridge/chat, not serve projection |

## Security and Governance

- Redis keys must not include absolute local paths.
- Redis payloads may include full derived `WikiIndex` content, including drafts;
  treat Redis as sensitive storage and enforce draft filtering after hydration
  with strong tests.
- Network manifests should continue redacting local roots.
- Raw Redis URLs and Redis URLs with passwords must be redacted from logs and
  diagnostics. Diagnostics may expose a sanitized endpoint label for UI status
  cards.
- Production docs should mention Redis/Valkey network exposure, auth, TLS, and
  backup policies.
- Redis records are not automatically expired in the current projection-store
  release. Operators should use Redis/Valkey eviction or TTL policy, rotate
  namespaces, or delete a deployment namespace when stale derived payload
  retention matters.

## PR Breakdown

### PR 1: ProjectionStore Refactor

- Add internal `ProjectionStore` protocol.
- Add `InMemoryProjectionStore`.
- Preserve current API behavior.
- Add parity tests.

### PR 2: Redis Backend

- Add optional dependency extra.
- Add Redis store implementation.
- Add CLI/env configuration.
- Add friendly missing-extra error message.
- Add Redis integration tests with a skip path when Redis is unavailable.
- Add docs.

### PR 3: Namespace and Source ID Hardening

- Add explicit `--source-id`.
- Add namespace config.
- Add key schema tests.
- Add collision tests.

### PR 4: Fallback and Diagnostics

- Add failure policies.
- Add diagnostics endpoint or diagnostics CLI command.
- Add corrupt payload and outage tests.

### PR 5: Production Docs

- Add deployment guide for memory vs Redis/Valkey.
- Add Docker/managed Redis/Valkey guidance.
- Add operational checklist.

### PR 6: RedisVL Spike

- Add `redisvl` optional dependency extra.
- Add experimental semantic search design and gated implementation.
- Keep `--search-backend lexical` default.
- Add benchmark and quality tests before enabling in docs as recommended.

## Open / Resolved Questions

| Question | Current answer |
| --- | --- |
| Should Redis store full `WikiIndex` including drafts, or only approved serving projection by default? | Current implementation stores the derived `WikiIndex`; treat Redis as sensitive because it may include draft page text/frontmatter even though network responses filter drafts by default. |
| Should `source_id` be derived, explicit, or required when Redis is enabled? | Explicit `--source-id` is recommended for shared Redis deployments; the default remains derived for compatibility. |
| Should Redis integration tests use Docker, a fake Redis, or testcontainers? | Unit tests use a fake Redis client; optional live integration tests run only when `LLMWIKI_REDIS_URL` is set. |
| Should diagnostics be public `/diagnostics/*`, CLI-only, or local-only? | Current endpoint is `GET /diagnostics/projection-store` with redacted fields only. |
| Should Redis projection payloads expire automatically? | No automatic TTL is implemented in this slice; eviction, retention, database cleanup, and namespace cleanup are operator-managed. |
| Should RedisVL live in `llmwiki-serve` or in `llmwiki-agent-bridge` as an aggregator-side search enhancement? | Still open and deferred; RedisVL needs its own ADR/spec because it changes retrieval behavior. |
| How should enterprise metadata and raw-origin hints be represented in Redis keys and vector metadata? | Still open and deferred; current Redis projection keys use schema version, namespace, source id, and projection signature only. |

## Release Validation

For the current Redis PR, production documentation has been refreshed and live
Redis validation has been completed with sanitized details only:

- `tests/test_redis_projection_store_integration.py` passed against an isolated
  local Docker Redis instance on a loopback database.
- Manual smoke covered `/manifest`, `/query`, and
  `/diagnostics/projection-store` with explicit namespace/source id and
  `--redis-failure-policy fail-fast`.
- Diagnostics did not expose a raw Redis URL, credentials, query parameters, or
  local root path.
- Manual namespace keys were cleaned after non-sensitive inspection and the
  container was stopped.

Keep RedisVL and enterprise/vector metadata design separate from the
projection-cache boundary.
