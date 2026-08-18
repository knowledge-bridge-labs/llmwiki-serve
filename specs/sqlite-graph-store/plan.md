# Plan: SQLite GraphStore

1. Add `graph_store.py` with SQLite schema, snapshot keys, payload digest, and
   redacted failure behavior.
2. Add an internal typed graph engine provider over normalized `GraphNode` and
   `GraphEdge` payloads.
3. Extend `LlmWikiService` constructor with optional graph store, failure
   policy, and graph engine injection.
4. Route `graph()` and `graph_neighbors()` through the graph-store-backed graph
   view when configured.
5. Add CLI options and environment resolution.
6. Update README, architecture, release checklist, ADR, and tests.
7. Validate with focused tests, full relevant service/API tests, type/lint,
   OpenAPI freshness, build, release smoke, and live GraphRAG smoke.

## Risks

- Accidentally writing cache files under the source root.
- Regressing draft filtering by caching unfiltered `index.nodes`.
- Confusing Redis projection cache with SQLite graph snapshot cache.
- Treating SQL/PGQ or Apache AGE as release dependencies before provider
  support is mature.

## Rollout

Keep the feature opt-in. The default graph store remains `none`; users can add
SQLite only when they have graph-heavy workloads or want local GraphRAG smoke
evidence.
