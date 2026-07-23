# Worklog: MCP Metadata And Default Limits

## Scope

Addressed llmwiki-serve remote issues #20 and #21:

- derive MCP server/tool metadata from the served wiki manifest, with operator
  overrides;
- lower full-graph omitted-limit defaults and make graph/context omitted-limit
  behavior configurable.

## Implementation Notes

- Added manifest-scoped MCP metadata shared by JSON-RPC `tools/list` and the
  FastMCP Streamable HTTP server.
- Added `create_app(...)` options for MCP metadata overrides and graph/context
  default limits.
- Added `llmwiki-serve serve` flags and environment-variable fallbacks for those
  options.
- Changed full-graph omitted-limit defaults from 500 to 100 while preserving the
  explicit graph maximum of 2,000.
- Kept source files canonical and changed only serving metadata/defaults.

## Validation Plan

- Focused API/MCP/CLI tests in `tests/test_service.py`: passed.
- Public API/OpenAPI tests in `tests/test_public_api.py`: passed.
- `docs/openapi.json` regenerated and `scripts/export_openapi.py --check`
  passed.
- Full test suite: `221 passed, 4 skipped`.
- Type check: `uv run mypy` passed.
- Package build: `uv build` passed.

## LLMWiki Ingestion Candidates

- `specs/mcp-manifest-scoped-metadata/`
- `specs/operator-default-limits/`
- `docs/decisions/2026-07-23-mcp-scoped-metadata-and-default-limits.md`
- `docs/worklogs/2026-07-23-mcp-metadata-default-limits.md`
