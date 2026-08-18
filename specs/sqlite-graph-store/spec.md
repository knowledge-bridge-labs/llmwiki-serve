# Spec: SQLite GraphStore

## Status

Implementation branch.

## Problem

Graph-heavy agent workflows repeatedly call full graph and neighborhood
surfaces over the same projected wiki. The current in-process graph view is
correct, but there is no persistent local graph snapshot cache for repeated
server starts or cache-aware GraphRAG validation.

## Goals

- Add an opt-in SQLite-derived graph snapshot cache with no new dependency.
- Preserve default behavior when the graph store is not configured.
- Keep the source folder read-only.
- Preserve existing HTTP and MCP graph contracts.
- Keep approved-only and draft-inclusive graph visibility isolated.
- Support internal typed graph operations without exposing raw SQL or Cypher.
- Document the future PostgreSQL 19 SQL/PGQ migration path.

## Non-goals

- Do not make SQLite the source of truth.
- Do not expose raw graph query languages to public clients.
- Do not replace Redis/Valkey projection-store behavior.
- Do not add vector search, embedding storage, answer synthesis, or hosted RAG.
- Do not make Apache AGE the default production graph backend.

## Requirements

- `REQ-GS-001`: Default install and default `serve` continue without a graph
  store.
- `REQ-GS-002`: CLI supports `--graph-store none|sqlite`,
  `--graph-store-path`, and `--graph-store-failure-policy`.
- `REQ-GS-003`: CLI rejects graph-store paths under the served source root.
- `REQ-GS-004`: SQLite keys include schema version, namespace, source id,
  bundle id, projection signature, and visibility scope.
- `REQ-GS-005`: Cache miss or invalid payload falls back to current projection
  when failure policy is `fallback-local`.
- `REQ-GS-006`: Backend exceptions raise redacted `RuntimeError` when failure
  policy is `fail-fast`.
- `REQ-GS-007`: Snapshot rows include a payload digest; digest mismatch is a
  cache miss.
- `REQ-GS-008`: `/graph`, `/graph/neighborhood`, `llmwiki_graph`, and
  `llmwiki_graph_neighbors` return the same payloads with and without SQLite
  GraphStore for the same source and visibility.
- `REQ-GS-009`: The manifest advertises graph-store capability only when a
  graph store is configured.
- `REQ-GS-010`: Internal graph query provider supports typed operations and
  keeps `raw_query=false`.

## Compatibility

The public HTTP and MCP graph payload shapes do not change. OpenAPI does not
need a new public raw-query endpoint.

## Data Safety

SQLite GraphStore files are sensitive derived local state. They may contain
page paths, node labels, source-ref nodes, tag nodes, relations, and graph
metadata. They must stay outside the served source root and must not be
committed, attached to issues, or used as public evidence.

## Acceptance Criteria

- Focused unit/integration tests pass for SQLite store, service, HTTP, MCP, CLI,
  representative wiki variants, draft filtering, corrupt payload handling, and
  internal typed graph operations.
- `ruff`, `mypy`, OpenAPI check, build, and release smoke pass.
- A live GraphRAG smoke against a local `llmwiki-serve` server confirms
  context/search/read/graph evidence works with SQLite GraphStore enabled.

## References

- ADR: `../../docs/decisions/2026-08-18-sqlite-graphstore-release-and-pg19-path.md`
- Architecture: `../../docs/architecture.md`
- Release checklist: `../../docs/release.md`
