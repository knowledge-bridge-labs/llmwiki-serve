# ADR: Redis Projection Store Derived Cache Boundary

## Status

Accepted.

## Context

`llmwiki-serve` projects Markdown-compatible wiki folders and optional sidecar
graph facts into a derived `WikiIndex`. The default service keeps that
projection in process memory and refreshes it by checking source freshness.

Production-style deployments may run multiple workers or restart services often
enough that rebuilding the same projection is wasteful. Redis/Valkey can share a
derived projection cache across service instances, but it also changes the
operational data boundary: cached projections can contain page text,
frontmatter, source-reference labels, graph metadata, and draft pages.

The project also has an existing producer-manifest freshness mode. That marker
is an operator-trusted freshness signal for generated wiki outputs, but it is
not a public projection identity and it does not make any cache authoritative.

## Decision

Add Redis/Valkey as an optional projection store for derived `WikiIndex`
records only.

Redis is never the source of truth. Markdown-compatible source folders, adapter
markers/config, and optional `graph/graph.json` sidecars remain canonical.
Freshness remains owned by the source signature path, refresh interval rules,
explicit refresh calls, or the operator-selected producer manifest marker. Redis
can reuse a projection only after those mechanisms identify the eligible
projection signature.

The default remains process memory with no external service. Redis is selected
only by installing the optional `llmwiki-serve[redis]` extra and configuring the
server with `--projection-store redis` or `LLMWIKI_PROJECTION_STORE=redis`.

Redis keys include a schema version, namespace, source id, and projection
signature. Key parts are sanitized before being used in Redis names, and
payloads omit the local source root path. Operators should still provide
explicit deployment-specific `--cache-namespace` and `--source-id` values for
shared Redis instances.

Redis payloads are sensitive derived storage. They may include derived page
text, YAML front matter, graph facts, source refs, and draft pages. Network
responses continue to filter drafts by default, but Redis operators who can
inspect the database may see content that public HTTP/MCP responses withhold.
Redis URLs, credentials, local roots, and raw payloads must not appear in
diagnostics, logs, release notes, or public issue comments.

The supported Redis failure policies are:

- `fallback-local`: default optional-cache posture; use process memory after a
  Redis client failure.
- `fail-fast`: surface Redis failure as an error when shared cache availability
  is required.

Corrupt or mismatched Redis payloads are cache misses, not source evidence.

## Consequences

- Local quickstart and default PyPI installs remain unchanged.
- Multi-worker deployments can reuse derived projections without rebuilding
  from disk for every service instance.
- Redis deployment security becomes an operator responsibility when enabled.
- Producer manifest mode composes with Redis, but a stale producer marker can
  still authorize reuse of a stale projection because Redis is not a freshness
  oracle.
- The diagnostics endpoint can explain cache backend health without exposing
  sensitive connection details or local paths.

## Follow-Ups

- Run optional live Redis/Valkey release validation against non-sensitive
  fixtures before publishing Redis-affecting releases.
- Mirror the optional-extra and sensitive-cache posture in `llmwiki-docs` if the
  public deployment guide is updated.
- Keep RedisVL and semantic/vector search as a separate ADR/spec because that
  changes retrieval behavior and privacy/cost posture.
- Consider stricter CLI validation for explicit namespace/source-id inputs if
  operators need earlier feedback than key-part sanitization.

## References

- Spec: `specs/redis-projection-store/`
- Architecture: `docs/architecture.md#projection-store-backends`
- Release checklist: `docs/release.md`
- Producer manifest ADR:
  `docs/decisions/2026-07-17-producer-manifest-freshness-boundary.md`
