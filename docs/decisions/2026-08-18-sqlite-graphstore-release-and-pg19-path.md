# 2026-08-18 Decision Record: SQLite GraphStore Release and PostgreSQL 19 Graph Path

## Status

Accepted for implementation branch.

## Context

`llmwiki-serve` already projects Markdown, Obsidian-style, and LLMWiki folders
into a normalized page/link/source-ref/tag graph. The public graph surface is
bounded and read-only: `/graph`, `/graph/neighborhood`, `llmwiki_graph`, and
`llmwiki_graph_neighbors`.

Redis/Valkey support in this repository is a derived `WikiIndex` projection
cache. It is useful for shared projection reuse, but it is not a graph query
engine and should not become the default GraphRAG backend.

The project needs a practical GraphRAG release path that works for local users
without a paid database, hosted vector store, or managed graph service. It also
needs a credible production migration path once native graph query support in
PostgreSQL is broadly usable.

PostgreSQL 19 adds SQL/PGQ property graph support in the PostgreSQL
documentation set, but the release and managed-provider support posture must
settle before `llmwiki-serve` relies on it as a production default. Ordinary
PostgreSQL tables with indexed `nodes` and `edges` plus bounded recursive CTEs
are available today and do not depend on graph-extension allowlists.

Apache AGE remains useful where it is explicitly available, but it is an
extension-based path with provider-specific operational constraints. It should
not be the first production default for this project.

## Decision

Ship SQLite GraphStore first as an opt-in derived graph snapshot cache.

- Default behavior remains unchanged: no graph store.
- Operators can enable `--graph-store sqlite --graph-store-path <path>`.
- CLI paths must be outside the served source root.
- GraphStore keys include schema version, namespace, source id, bundle id,
  projection signature, and visibility scope.
- Approved-only and draft-inclusive graph snapshots are separate.
- The SQLite snapshot stores projected graph nodes and edges after visibility
  filtering, plus a payload digest.
- Invalid, stale, malformed, or digest-mismatched snapshots are cache misses.
- Backend exceptions use redacted errors; `fallback-local` recomputes from the
  current projection, and `fail-fast` raises.
- SQLite GraphStore does not expose raw SQL, Cypher, or another raw query
  language over HTTP or MCP.

Add an internal typed graph engine provider for structured GraphRAG operations:
neighbors, backlinks, paths, by-source-ref, and by-tag. Keep it internal for
this release. Public clients continue to use `/graph`, `/graph/neighborhood`,
MCP graph tools, search, and read.

After this SQLite release, the first production graph persistence target is
ordinary PostgreSQL:

- `nodes` and `edges` tables keyed by source id, projection signature, and
  visibility scope.
- indexes for source/target/relation and common seed lookups.
- bounded recursive CTE traversal for neighborhoods and paths.
- no dependency on Apache AGE or another non-core graph extension.

When PostgreSQL 19 SQL/PGQ is generally available and managed providers prove
support, evaluate a PostgreSQL 19 SQL/PGQ provider as the preferred native graph
query path. Apache AGE remains optional/provider-specific, not the default
production migration target.

## Consequences

- Users get a zero-extra, OSS, local-first graph cache now.
- The source folder remains immutable input.
- SQLite cache files are sensitive derived local state and must not be
  committed or published.
- The public API stays stable; OpenAPI changes are not required for raw graph
  query language support.
- Future production backends have a typed provider boundary rather than a raw
  query passthrough.
- PostgreSQL can be adopted incrementally before SQL/PGQ is widely deployed.

## Follow-ups

- Add a PostgreSQL nodes/edges provider spec before implementation.
- Recheck PostgreSQL 19 SQL/PGQ after GA and after AWS/GCP/Azure managed
  PostgreSQL support is confirmed.
- Reconsider Apache AGE only for deployments where the extension is explicitly
  available and operationally acceptable.
- Keep GraphStore release evidence separate from semantic vector retrieval
  benchmark claims.

## References

- Spec: `../../specs/sqlite-graph-store/`
- Architecture: `../architecture.md`
- Release checklist: `../release.md`
- PostgreSQL 19 release notes:
  <https://www.postgresql.org/docs/19/release-19.html>
- PostgreSQL 19 property graphs:
  <https://www.postgresql.org/docs/19/ddl-property-graphs.html>
- PostgreSQL recursive queries:
  <https://www.postgresql.org/docs/current/queries-with.html>
