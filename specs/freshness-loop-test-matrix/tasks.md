# Tasks: Freshness Loop Test Matrix

- [x] Review existing freshness docs, ADRs, research notes, and service tests.
- [x] Add Loop 0 prior-art/library-fit as the required first gate.
- [x] Add a standardized freshness loop test matrix.
- [x] Link the producer manifest test spec to the shared matrix.
- [x] Run lightweight validation for touched docs and related freshness tests.
- [x] Fill shared current-mode cells for `FL-040`, `FL-050`, and `FL-060`.

## Follow-Up Work

- [x] Add `FL-*` references to current unit test names or comments where tests are
  materially expanded.
- [ ] Add a fixture helper that can run the same loop against service, HTTP, and
  MCP surfaces without copying mutation setup.
- [ ] Refresh `PA-010` before implementing watcher or provider
  expansion when dependency, platform, or source-boundary assumptions change.
- [ ] Fill watcher dirty-flag rows when a watcher provider interface exists.
- [x] Fill Redis projection cache rows when a cache key and storage boundary are
  implemented.

## Validation

- `git diff --check -- specs/ax-knowledge-warehouse-rubrics specs/freshness-loop-test-matrix docs/research/2026-07-17-freshness-invalidation-core-libraries.md`
- `uv run pytest -q tests/test_freshness_loop_matrix.py`
- `uv run ruff check tests/test_freshness_loop_matrix.py`
- `uv run ruff format tests/test_freshness_loop_matrix.py`
- `uv run ruff format --check tests/test_freshness_loop_matrix.py`
- `uv run pytest -q tests/test_service.py -k "refresh or producer_manifest"`

## LLMWiki Ingestion Candidates

- `specs/ax-knowledge-warehouse-rubrics/`
- `specs/freshness-loop-test-matrix/`
- `specs/producer-manifest-freshness/tests.md`
- `docs/research/2026-07-17-freshness-invalidation-core-libraries.md`
- `tests/test_freshness_loop_matrix.py`
