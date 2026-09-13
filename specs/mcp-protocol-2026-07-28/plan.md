# MCP Protocol 2026-07-28 Compatibility Plan

## Implementation

- Upgrade the MCP dependency range and regenerate `uv.lock`.
- Replace `mcp.server.fastmcp.FastMCP` imports with SDK v2 `MCPServer` imports.
- Keep `create_mcp_stream_server(...)` as the repository-owned factory so
  existing tests and app construction do not need to know SDK details.
- Keep `streamable_http_path="/stream"` and mount the SDK ASGI app at `/mcp` so
  the public endpoint remains `/mcp/stream`.
- Keep the existing seven tool handlers and service calls, only adjusting SDK
  return/error wrappers where v2 names changed.
- Register safe MCP page resources, page resource templates, and the
  `llmwiki_source_grounded_query` prompt as additive read-only affordances.
- Add a thin request validation middleware for modern Streamable HTTP headers,
  optional mirrored method/name header matching, strict validation for supplied
  `_meta`, and minimal `_meta` synthesis for known current clients that omit it
  before SDK dispatch.
- Update release smoke and docs examples to include modern MCP headers and
  request `_meta` for `/mcp/stream`, while documenting the compatibility path
  for current clients that omit `_meta`.

## Affected Modules

- `pyproject.toml`
- `uv.lock`
- `src/llmwiki_serve/api.py`
- `scripts/release_smoke.py`
- `docs/release.md`
- `README.md`
- `docs/architecture.md`
- `tests/test_service.py`
- `tests/test_mcp_protocol_2026_07_28.py`

## Risks

- SDK v2 may change result field casing or generated schema details. Mitigation:
  test the externally observed Streamable HTTP JSON, not SDK internals.
- Modern header validation may reject current clients that omit optional request
  details. Mitigation: require `MCP-Protocol-Version` and required Accept
  values, validate supplied `_meta` strictly, synthesize minimal `_meta` when it
  is omitted for `2026-07-28`, and treat `Mcp-Method` and `Mcp-Name` as
  optional mirrors that are rejected only when they conflict with the JSON-RPC
  body.
- Resource/prompt additions may be mistaken for Serve-owned answer generation.
  Mitigation: document them as source-scoped read/prompt helpers only; answer
  synthesis remains owned by the caller, bridge, or runtime layer.
- Legacy clients may rely on `initialize`. Mitigation: preserve the SDK v2
  dual-era transport and add a focused legacy initialize check.

## Rollout

Ship as a 0.2.11 additive compatibility update on top of the 0.2.10 GraphStore
release. Release notes should describe the `/mcp/stream` modernization as
preserving existing tools and paths while adding MCP `2026-07-28` request
compatibility and progressive read-only metadata.
