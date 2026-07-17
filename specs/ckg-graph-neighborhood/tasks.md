# Tasks: CKG Graph Neighborhood

- [x] Confirm or create related ADR if needed.
- [x] Update or create contract/schema docs if endpoints or artifacts change.
- [x] Implement code changes.
- [x] Add or update focused tests.
- [x] Add deterministic GT-010 through GT-050 traversal loop matrix in
  `tests/test_graph_traversal_loop_matrix.py`.
- [x] Update README/docs if user-facing behavior changes.
- [x] Run validation.
- [x] Record follow-up work.
- [x] Mark files that should be ingested into project LLMWiki.

## Validation

- `uv run pytest -q tests/test_graph_traversal_loop_matrix.py`
- `uv run pytest -q tests/test_service.py tests/test_public_api.py`
- `uv run ruff format --check .`
- `uv run ruff check .`
- `uv run mypy src`
- `uv run pytest -q`
- `uv run python scripts/export_openapi.py --check`
- `uv run python scripts/release_smoke.py`
- `uv run python scripts/ckg_neighborhood_perf.py --pages 1200 --edges-per-page 4 --iterations 25`
- `uv run python scripts/ckg_neighborhood_perf.py --pages 1200 --edges-per-page 4 --iterations 5 --refresh-interval-seconds 0`

Latest local result:

- Traversal loop matrix: `5 passed`.
- Full test suite: `146 passed, 2 skipped`.
- Hot path, `refresh_interval_seconds=60`, 1,200 pages and 4 sidecar edges per
  page:
  - context median: `9.116ms`, `41,574` JSON bytes.
  - full graph median: `9.158ms`, `972,792` JSON bytes.
  - neighborhood median: `0.033ms`, `5,425` JSON bytes.
- Strict path, `refresh_interval_seconds=0`, same fixture:
  - context median: `896.656ms`.
  - full graph median: `883.127ms`.
  - neighborhood median: `847.023ms`.

Freshness finding:

- Strict mode is dominated by `_SourceSignatureCache` path-state and file digest
  verification, not graph-neighborhood traversal.
- Redis projection storage can avoid rebuild work after a signature is known,
  but cannot remove strict no-change scan cost by itself.
- `refresh_interval_seconds` remains the explicit freshness/performance knob for
  long-running servers until a separate watcher or producer-manifest contract is
  designed.

## Follow-Up Work

- Update `llmwiki-agent-bridge` direct integration skills after this serve
  contract is reviewed.
- Consider source-bundle guidance fields for recommended seed nodes.
- Consider richer sidecar metadata only after a redaction-safe schema is agreed.

## LLMWiki Ingestion Candidates

- `specs/ckg-graph-neighborhood/`
- `docs/decisions/2026-07-17-serve-graph-neighborhood-boundary.md`
- `README.md`
- `docs/architecture.md`
- `docs/release.md`
