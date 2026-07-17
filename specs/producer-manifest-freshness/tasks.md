# Tasks: Producer Manifest Freshness

- [x] Confirm or create related ADR if needed.
- [x] Update docs for the opt-in producer/operator contract.
- [x] Implement code changes.
- [x] Add or update focused tests.
- [x] Run validation.
- [x] Record benchmark results.
- [x] Mark files that should be ingested into project LLMWiki.

## Validation

- `uv run ruff format --check .`
- `uv run ruff check .`
- `uv run mypy src`
- `uv run pytest -q`
- `uv run python scripts/export_openapi.py --check`
- `uv run python scripts/release_smoke.py`

## Benchmark Snapshot

Synthetic 1,200 page wiki, 4 edges per page, strict freshness mode:

| Mode | Context median | Full graph median | Neighborhood median |
| --- | ---: | ---: | ---: |
| Strict source scan | 783.886 ms | 786.439 ms | 759.292 ms |
| Producer manifest | 9.171 ms | 10.585 ms | 1.227 ms |
| Producer manifest + 60s refresh interval | 7.455 ms | 8.406 ms | 0.031 ms |

The producer manifest marker contract removes the repeated no-change source
tree scan from strict freshness checks. It does not make graph traversal itself
faster; it removes the dominant freshness validation cost before traversal.

## LLMWiki Ingestion Candidates

- `specs/producer-manifest-freshness/`
- `docs/decisions/2026-07-17-producer-manifest-freshness-boundary.md`
- `README.md`
- `docs/architecture.md`
- `docs/release.md`
- `CHANGELOG.md`
