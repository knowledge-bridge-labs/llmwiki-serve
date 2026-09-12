# MCP Protocol 2026-07-28 Compatibility Tests

## Acceptance Criteria

- Modern `server/discover` on `/mcp/stream` succeeds with protocol headers and
  `_meta`, advertises `2026-07-28`, server info, tools capability, instructions,
  and cache fields.
- Modern `tools/list` on `/mcp/stream` succeeds with protocol headers and
  `_meta`, returns all seven tools in deterministic order, includes cacheable
  result fields, and advertises SDK-native `outputSchema` plus read-only tool
  annotations.
- Modern `tools/call` on `/mcp/stream` succeeds for representative context,
  graph-neighborhood, and source-bundle calls with matching `Mcp-Name` headers.
- Modern `resources/list`, `resources/templates/list`, and `resources/read`
  expose source-scoped page resources and read one page through the same
  visibility boundary as the read tool. Page resources and the page template
  include resource annotations for assistant-facing progressive discovery.
- Modern `prompts/list` and `prompts/get` expose the
  `llmwiki_source_grounded_query` prompt.
- Current-client-compatible modern `tools/call` requests without mirrored
  `Mcp-Method`/`Mcp-Name` headers and with omitted or partial `params._meta`
  succeed when the JSON-RPC body carries a valid method and params plus
  `MCP-Protocol-Version: 2026-07-28` or the current Codex compatibility version
  `2025-06-18`.
- Modern malformed headers, mismatched `Mcp-Method`, mismatched
  `Mcp-Name`/resource URI/prompt name, missing required Accept values, or
  malformed/conflicting supplied `_meta` fields fail before SDK dispatch.
- Modern responses do not include `Mcp-Session-Id`.
- Legacy `initialize` on `/mcp/stream` without modern request headers still
  succeeds.
- Existing `/mcp` JSON-RPC compatibility tests continue to pass unchanged.

## Test Coverage

| ID | Test | Coverage |
| --- | --- | --- |
| `MCP-2026-010` | `tests/test_mcp_protocol_2026_07_28.py` | Modern `server/discover`, `tools/list`, `tools/call`, resources, prompts, header/name/meta validation, seven tools, no modern session id, and legacy initialize compatibility. |
| `MCP-2026-020` | `tests/test_service.py::test_mcp_streamable_http_tools_list_and_call_smoke` | Existing Streamable HTTP smoke updated to SDK v2 modern request shape. |
| `MCP-2026-030` | `scripts/release_smoke.py::assert_mcp_streamable_http` | Release smoke keeps fixture coverage for `/mcp/stream` with sanitized modern requests. |
