# Tests: OKF v0.2 Read-Only Consumer

## Focused

- `uv run --extra dev pytest -q tests/test_adapters.py tests/test_service.py -k "okf_v02" -p no:cacheprovider`

## Regression

- `uv run --extra dev ruff check .`
- `uv run --extra dev mypy src`
- `uv run --extra dev python scripts/export_openapi.py --check`
- `uv run --extra dev pytest -q tests/test_adapters.py tests/test_service.py tests/test_public_api.py -p no:cacheprovider`
- `uv run --extra dev pytest -q tests/test_sqlite_graph_store.py -p no:cacheprovider`
- `uv run --extra dev pytest -q -p no:cacheprovider`

## CLI Smoke

- `uv run llmwiki-serve manifest tests/fixtures/okf-v0.2-bundle`
- `uv run llmwiki-serve query tests/fixtures/okf-v0.2-bundle zzokfrevenueapproved`
- `uv run llmwiki-serve source-refs tests/fixtures/okf-v0.2-bundle`

