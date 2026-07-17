# Plan: CKG Graph Neighborhood

## Approach

Reuse the existing projected graph and visibility rules. Add a small graph view
inside `LlmWikiService` so clients can fetch a bounded neighborhood without
requesting the entire graph payload. Keep the implementation deterministic and
read-only.

The operation resolves seed values as node ids first, then page ids, paths,
labels, and slugs. It performs bounded BFS over outgoing, incoming, or both
directions and filters relation types when requested.

## Affected Areas

- Source module: `src/llmwiki_serve/models.py`, `src/llmwiki_serve/service.py`,
  `src/llmwiki_serve/api.py`
- Tests: focused service/API/MCP tests in `tests/test_service.py`
- Docs: `README.md`, `docs/architecture.md`, `docs/openapi.json`
- Contracts: additive HTTP and MCP tool shape

## Risks

- Risk: traversal accidentally reveals draft-only adjacent metadata.
  Mitigation: build neighborhood lookup over the same approved graph view used
  by `/graph`.

- Risk: agents treat neighborhood lookup as complete global search.
  Mitigation: document it as a follow-up graph inspection primitive, not a
  replacement for `/query` or exact reads.

- Risk: large graph traversal creates expensive responses.
  Mitigation: clamp depth and limit on network surfaces.

## Rollout

- Local validation: targeted pytest, full pytest if time allows, OpenAPI check,
  and synthetic graph performance comparison.
- CI validation: existing Python lint/type/test workflow.
- Docs / LLMWiki ingestion: ingest this spec, ADR, README, architecture, and
  release notes after review.
