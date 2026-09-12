# MCP Protocol 2026-07-28 Compatibility

## Problem

`llmwiki-serve` 0.2.10 exposes the same seven read-only tools through an
MCP-style JSON-RPC compatibility route and an SDK-backed MCP Streamable HTTP
route. The SDK-backed route currently depends on MCP Python SDK v1 FastMCP,
which implements the handshake/session-era Streamable HTTP behavior. MCP
revision `2026-07-28` makes Streamable HTTP request-scoped and requires modern
per-request protocol metadata.

## Goals

- Use MCP Python SDK v2 (`mcp>=2.0.0,<3`) for the SDK-backed Streamable HTTP
  server.
- Preserve the public `/mcp/stream` path and the local `/mcp` JSON-RPC
  compatibility route.
- Preserve all 0.2.10 tools and service behavior:
  `llmwiki_context`, `llmwiki_search`, `llmwiki_read`, `llmwiki_graph`,
  `llmwiki_graph_neighbors`, `llmwiki_source_refs`, and
  `llmwiki_source_bundle`.
- Add safe source-scoped MCP resources for page reads and one source-grounded
  prompt while preserving the seven existing tools.
- Support modern `2026-07-28` Streamable HTTP requests carrying
  `MCP-Protocol-Version`. Supplied `_meta` request metadata is validated
  strictly; known current clients that omit `_meta` are supported by
  synthesizing minimal request metadata before SDK dispatch. Mirrored
  `Mcp-Method` and `Mcp-Name` headers are validated when present, while
  current clients that rely on the JSON-RPC body method and params remain
  usable.
- Avoid minting or echoing `Mcp-Session-Id` for modern requests.
- Keep legacy `initialize` compatibility for older clients through the SDK v2
  dual-era transport.

## Non-Goals

- Do not rename tools or change argument names.
- Do not change HTTP, CLI, search, read, graph, source-bundle, draft filtering,
  CORS, or I/O logging behavior beyond MCP transport compatibility.
- Do not add writable resources, resource subscriptions, OAuth, or
  server-initiated request workflows.
- Do not claim exhaustive MCP certification.

## Requirements

- `REQ-MCP-2026-001`: `pyproject.toml` depends on `mcp>=2.0.0,<3`, and
  `uv.lock` is refreshed consistently.
- `REQ-MCP-2026-002`: `create_mcp_stream_server(...)` uses SDK v2
  `MCPServer` while keeping the public mounted route at `/mcp/stream`.
- `REQ-MCP-2026-003`: The SDK-backed server registers the same seven tool
  handlers, descriptions, metadata overrides, default limit behavior, error
  sanitization, and structured result payloads as the 0.2.10 FastMCP surface.
- `REQ-MCP-2026-004`: Modern `server/discover`, `tools/list`, and
  `tools/call` requests succeed when they include `MCP-Protocol-Version:
  2026-07-28`. Current Codex MCP client requests using
  `MCP-Protocol-Version: 2025-06-18` are also accepted as a compatibility
  version. Standard `_meta` fields are validated when supplied, and omitted or
  partial `_meta` is synthesized for known current clients before SDK dispatch.
  Matching `Mcp-Method` and `Mcp-Name` headers are accepted and validated, but
  current-client-compatible requests without those mirrored headers also
  succeed when the JSON-RPC body method and params are valid.
- `REQ-MCP-2026-005`: Modern `tools/list` is deterministic and includes the
  full seven-tool set, including `llmwiki_graph_neighbors` and
  `llmwiki_source_bundle`.
- `REQ-MCP-2026-006`: Modern responses include protocol result metadata added
  by the SDK and do not include `Mcp-Session-Id`.
- `REQ-MCP-2026-007`: Legacy `initialize` requests without modern request
  headers still receive a successful SDK-managed compatibility response.
- `REQ-MCP-2026-008`: Release documentation and smoke probes use sanitized
  modern request examples where they exercise `/mcp/stream`.
- `REQ-MCP-2026-009`: Modern `resources/list`,
  `resources/templates/list`, and `resources/read` expose only source-scoped
  page resource URIs and return page text through the same visibility boundary
  as `llmwiki_read`.
- `REQ-MCP-2026-010`: Modern `prompts/list` and `prompts/get` expose the
  `llmwiki_source_grounded_query` prompt without changing runtime ownership;
  the prompt is a caller aid, not a Serve-owned answer generator.
- `REQ-MCP-2026-011`: Modern validation rejects mismatched mirrored
  `Mcp-Method` or `Mcp-Name`/resource URI/prompt name headers when present,
  missing required Accept values, malformed supplied `_meta` fields, or supplied
  `_meta` protocol fields that conflict with `MCP-Protocol-Version` before SDK
  dispatch. Missing standard `_meta` fields are filled for current-client
  compatibility.

## Compatibility

This is a transport modernization. Existing service tools, response payloads,
HTTP endpoints, CLI commands, source-bundle identity, graph surfaces, and local
JSON-RPC `/mcp` compatibility route remain intact. Modern MCP clients should
prefer `/mcp/stream` with per-request protocol metadata. Older MCP clients can
still use the SDK v2 legacy path, and existing local harnesses can keep using
`/mcp` for JSON-RPC compatibility checks.

## References

- MCP specification `2026-07-28`: `https://modelcontextprotocol.io/specification/2026-07-28`
- MCP Streamable HTTP `2026-07-28`:
  `https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http`
- MCP server discovery:
  `https://modelcontextprotocol.io/specification/2026-07-28/server/discover`
- MCP Python SDK v2 documentation: `https://py.sdk.modelcontextprotocol.io/`
