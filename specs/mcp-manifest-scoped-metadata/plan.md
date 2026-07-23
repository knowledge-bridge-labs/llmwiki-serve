# MCP Manifest-Scoped Metadata Plan

## Implementation

- Add a shared MCP metadata builder in `src/llmwiki_serve/api.py`.
- Use the builder from `create_mcp_stream_server(...)` for FastMCP name,
  instructions, and registered tool descriptions.
- Use the builder from JSON-RPC `handle_mcp(..., method="tools/list")` so the
  compatibility endpoint reports the same scoped descriptions.
- Add optional `create_app(...)` parameters for MCP server name, instructions,
  and tool-description prefix.
- Add `serve` CLI flags and environment fallback for those overrides.

## Affected Modules

- `src/llmwiki_serve/api.py`
- `src/llmwiki_serve/cli.py`
- `tests/test_service.py`

## Risks

- Eager manifest reads for FastMCP metadata could alter missing-root startup.
  Mitigation: metadata derivation falls back to the previous generic text when
  manifest loading fails.
- Exact-description clients may notice description text changes. Mitigation:
  tool names and call contracts are unchanged.

## Rollout

Ship as an additive public-preview metadata improvement.
