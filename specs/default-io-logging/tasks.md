# Tasks: Default Serve I/O Logging

- [x] Confirm ADR requirement for default logging and redaction boundary.
- [x] Create spec files.
- [x] Implement ASGI middleware and JSONL sink.
- [x] Wire default/env/CLI configuration.
- [x] Add tests for default capture, opt-out, configured path, and redaction.
- [x] Run validation.
- [x] Mark files that should be ingested into project LLMWiki.

## Validation

- `uv run pytest -q tests/test_io_logging.py`
- `uv run pytest -q tests/test_service.py::test_http_health_and_query tests/test_service.py::test_mcp_context_matches_http_query_shape tests/test_service.py::test_quickstart_a2a_request_body_smoke tests/test_service.py::test_mcp_streamable_http_tools_list_and_call_smoke`
- `uv run pytest -q tests/test_service.py::test_network_manifest_redacts_root_and_cli_manifest_keeps_it tests/test_service.py::test_origin_header_is_enforced_for_browser_requests tests/test_public_api.py::test_openapi_contract_covers_core_http_response_models`
- `uv run pytest -q tests/test_service.py::test_source_signature_ignores_runtime_dirs_relative_to_root_only`
- `uv run ruff format --check src/llmwiki_serve/io_logging.py src/llmwiki_serve/api.py src/llmwiki_serve/cli.py src/llmwiki_serve/service.py tests/test_io_logging.py tests/test_service.py`
- `uv run ruff check src/llmwiki_serve/io_logging.py src/llmwiki_serve/api.py src/llmwiki_serve/cli.py src/llmwiki_serve/service.py tests/test_io_logging.py tests/test_service.py`
- `uv run mypy src`
- `uv run python scripts/export_openapi.py --check`

## LLMWiki Ingestion Candidates

- `specs/default-io-logging/`
- `docs/decisions/2026-07-20-default-serve-io-logging.md`
- `README.md`
- `docs/architecture.md`
- `CHANGELOG.md`
