# Tests: Operator Default Limits

## Acceptance Criteria

- HTTP `/graph` with no `limit` returns the built-in conservative default.
- HTTP `/graph` with an explicit larger limit still returns more than the
  default and clamps to `2000` for oversized requests.
- MCP JSON-RPC `llmwiki_graph` with no arguments uses the configured graph
  default.
- MCP JSON-RPC `llmwiki_context` with no explicit `limit` uses the configured
  context default.
- MCP Streamable HTTP registered tool metadata advertises the configured
  context and graph defaults in tool descriptions.
- `create_app(...)` startup defaults affect omitted HTTP/MCP limits.
- `llmwiki-serve serve` CLI flags and environment variables resolve to the
  expected app defaults.
- Invalid startup defaults fail without a traceback.

## Targeted Validation

- `uv run pytest -q tests/test_service.py::test_http_graph_default_limit_is_conservative_configurable_and_preserves_explicit_max tests/test_service.py::test_context_default_limit_is_configurable_across_http_and_mcp tests/test_service.py::test_cli_resolves_graph_and_context_default_limit_options_and_env tests/test_service.py::test_fastmcp_metadata_uses_manifest_scope_and_overrides tests/test_public_api.py`
- `uv run python scripts/export_openapi.py --check`
