# Spec: Redis Projection Store

## Status

Implemented; documentation and release-governance refresh.

## Problem

The default in-process projection cache is correct for local use, but
multi-worker or production-style deployments may rebuild the same derived
`WikiIndex` repeatedly. Operators need an opt-in shared projection cache without
making Redis a dependency, freshness authority, source of truth, or low-risk
storage location.

## Goals

- Keep process memory as the default projection store.
- Add Redis/Valkey as an optional `llmwiki-serve[redis]` extra.
- Reuse derived projections across service instances after source freshness has
  been validated by the normal source signature path or producer manifest path.
- Expose redacted projection-store diagnostics for operators.
- Document Redis as sensitive derived storage that may contain page text,
  front matter, source refs, graph facts, and draft pages.
- Keep Redis key names portable, namespaced, and free of raw local paths.
- Define fallback, fail-fast, corruption, and release-validation expectations.

## Non-Goals

- Do not require Redis for the quickstart or default PyPI install.
- Do not make Redis authoritative for Markdown pages, sidecar graph facts,
  source refs, review state, source freshness, or producer freshness.
- Do not add RedisVL, embeddings, vector ranking, or semantic search in this
  slice.
- Do not merge multiple source roots into one served source.
- Do not expose Redis URLs, credentials, local roots, or raw Redis payloads in
  public responses.

## Requirements

- `REQ-REDIS-001`: Default `llmwiki-serve serve <root>` uses process memory and
  requires no Redis server or `redis` Python package.
- `REQ-REDIS-002`: Redis support is selected only with `--projection-store redis`
  or `LLMWIKI_PROJECTION_STORE=redis` after installing
  `llmwiki-serve[redis]`.
- `REQ-REDIS-003`: Selecting Redis without `--redis-url` or `LLMWIKI_REDIS_URL`
  fails with an actionable configuration error.
- `REQ-REDIS-004`: Selecting Redis without the optional extra fails with an
  actionable install message that mentions `pip install "llmwiki-serve[redis]"`.
- `REQ-REDIS-005`: CLI flags win over environment variables. The implemented
  environment contract is `LLMWIKI_PROJECTION_STORE`, `LLMWIKI_REDIS_URL`,
  `LLMWIKI_CACHE_NAMESPACE`, and `LLMWIKI_SOURCE_ID`; failure policy is CLI-only.
- `REQ-REDIS-006`: Redis keys are derived from schema version, namespace,
  source id, and projection signature. Unsafe key parts are normalized with a
  deterministic hash suffix; raw local paths must not be used as key parts.
- `REQ-REDIS-007`: Operators should provide explicit `--source-id` and
  `--cache-namespace` values for shared Redis deployments to avoid collisions
  between roots with similar folder names.
- `REQ-REDIS-008`: Redis is a read-through derived cache only. The source
  signature, producer manifest marker, refresh interval, or explicit refresh
  decides whether a cached projection is eligible.
- `REQ-REDIS-009`: Producer manifest mode keeps its documented trust boundary.
  Redis may reuse a cached projection for the marker-approved generation, but it
  does not detect source changes when the trusted producer marker is stale.
- `REQ-REDIS-010`: Redis payloads must omit the local root path and reattach the
  current service root when hydrated.
- `REQ-REDIS-011`: Redis payloads are sensitive. They may contain derived
  `WikiIndex` content including page bodies, front matter, source refs, graph
  metadata, and drafts that network responses still withhold by default.
- `REQ-REDIS-012`: `--redis-failure-policy fallback-local` keeps serving from
  process memory after Redis client failure; `fail-fast` surfaces Redis failure
  as an error.
- `REQ-REDIS-013`: Corrupt payloads, schema mismatches, namespace mismatches,
  source-id mismatches, or projection-signature mismatches are cache misses and
  should rebuild from disk rather than serving untrusted data.
- `REQ-REDIS-014`: `GET /diagnostics/projection-store` reports backend,
  namespace, cache source id, availability, and last error without exposing
  Redis URLs, credentials, or local root paths.
- `REQ-REDIS-015`: Redis and memory stores return equivalent public payloads for
  manifest, source bundle, query, search, read, graph, MCP, and MCP Streamable
  HTTP surfaces for the same source generation.

## User / Agent Flow

Default local flow remains unchanged:

```bash
pip install llmwiki-serve
llmwiki-serve serve ./wiki --host 127.0.0.1 --port 8765
```

Redis/Valkey projection cache flow:

```bash
pip install "llmwiki-serve[redis]"
llmwiki-serve serve ./wiki \
  --projection-store redis \
  --redis-url redis://127.0.0.1:6379/0 \
  --cache-namespace acme-prod \
  --source-id project-alpha \
  --redis-failure-policy fail-fast
```

Environment equivalents:

```text
LLMWIKI_PROJECTION_STORE=redis
LLMWIKI_REDIS_URL=redis://127.0.0.1:6379/0
LLMWIKI_CACHE_NAMESPACE=acme-prod
LLMWIKI_SOURCE_ID=project-alpha
```

## Compatibility

- CLI: additive projection-store options for `serve`.
- Environment: additive Redis projection-store configuration.
- HTTP: additive `/diagnostics/projection-store` endpoint.
- OpenAPI: updated for the diagnostics response model.
- Existing clients: no behavior change unless operators opt into Redis.
- Packaging: default install stays local-first; Redis is an optional extra.

## Data Safety

Redis/Valkey must be treated with the same sensitivity as the served wiki.
Derived projection payloads can contain approved and draft page text,
frontmatter values, graph metadata, and source-reference labels. Network draft
filtering still happens after hydration, so a Redis operator who can inspect
the database may see data that default HTTP/MCP responses withhold.

Do not publish Redis URLs, passwords, raw keys, cached values, local root paths,
or private wiki snippets in release notes, issue comments, diagnostics
screenshots, or generated artifacts. Use isolated namespaces per deployment and
secure Redis/Valkey with appropriate network isolation, authentication, TLS, and
backup retention policies.

## References

- ADR: `docs/decisions/2026-07-22-redis-projection-store-derived-cache-boundary.md`
- Architecture: `docs/architecture.md#projection-store-backends`
- Freshness matrix: `specs/freshness-loop-test-matrix/tests.md`
- Adoption plan: `docs/research/redis-projection-store-adoption-plan.md`
