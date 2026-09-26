# Tests: Query-Action Judgment

## Focused

- `uv run pytest -p no:cacheprovider -q tests/test_query_action_judgment.py tests/test_public_api.py::test_openapi_contract_covers_core_http_response_models`

## Regression

- `uv run ruff format --check .`
- `uv run ruff check .`
- `uv run mypy src`
- `uv run python scripts/export_openapi.py --check`
- `uv run pytest -p no:cacheprovider`

## Release Artifact Smoke

- `uv build`
- `uv run python scripts/release_smoke.py --wheel <wheel> --sdist <sdist>`
- `uvx twine check <wheel> <sdist>`

## Public-Safety Checks

- Search tracked files for credentials, private endpoint URLs, private wiki
  snippets, absolute local paths in public docs/reports, and generated runtime
  artifacts.
- Confirm provider payload tests prove raw page text, raw snippets, source-ref
  labels, raw paths, local roots, private URLs, and obvious credentials are
  absent from provider export.
