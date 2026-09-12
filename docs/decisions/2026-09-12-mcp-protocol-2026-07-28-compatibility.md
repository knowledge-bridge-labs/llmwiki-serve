# ADR: MCP Protocol 2026-07-28 Compatibility

## Status

Accepted.

## Context

MCP revision `2026-07-28` changes Streamable HTTP from a
handshake/session-era transport to a request-scoped modern transport. Modern
requests carry protocol version in headers and may also carry client
capabilities in `_meta`, may mirror method/name metadata in headers, and do not
use `Mcp-Session-Id`.

`llmwiki-serve` 0.2.10 already has two MCP-facing surfaces:

- `/mcp`, a local MCP-style JSON-RPC compatibility route used by tests and
  simple clients.
- `/mcp/stream`, an official SDK Streamable HTTP route previously mounted from
  FastMCP.

The seven tool names and payloads are already part of the 0.2.10 compatibility
surface and should not change during protocol modernization.

## Decision

Upgrade the MCP Python SDK dependency to v2 and implement the SDK-backed
Streamable HTTP server with `MCPServer`. Keep the repository-owned
`create_mcp_stream_server(...)` factory, public `/mcp/stream` endpoint, and
`/mcp` JSON-RPC compatibility route.

Use the SDK v2 dual-era Streamable HTTP app for `/mcp/stream`. Modern clients
can call `server/discover`, `tools/list`, and `tools/call` with
`MCP-Protocol-Version: 2026-07-28` as the preferred modern version. The
Streamable HTTP path also accepts `2025-06-18` from current Codex MCP clients
as a compatibility version. Supplied request `_meta` is validated for malformed
or conflicting standard fields, while known current clients that omit or
partially populate `_meta` are supported by synthesizing missing standard
metadata before SDK dispatch. Mirrored `Mcp-Method` and `Mcp-Name` headers are
validated when present, but the JSON-RPC body remains the source of truth for
current clients that omit those headers. Legacy clients that begin with
`initialize` remain on the SDK-managed compatibility path.

Preserve the existing seven read-only tools, metadata scoping, configured
default limits, draft filtering, source-bundle identity, graph behavior, CORS,
I/O logging, and safe error messages.

## Consequences

- Modern MCP clients can connect to `/mcp/stream` using the `2026-07-28`
  request model.
- The modern Streamable HTTP path does not mint or echo `Mcp-Session-Id`.
- Older clients that still initialize can continue through SDK v2 legacy
  compatibility.
- `/mcp` remains a local compatibility harness rather than a full modern MCP
  transport.
- Release probes should cover both explicit `_meta` and current-client
  compatible omitted/partial `_meta` requests when exercising `/mcp/stream`;
  mirrored method/name headers should be covered as optional consistency
  checks, not unconditional requirements.

## Follow-Ups

- Refresh external docs portal snippets after this repository update lands.
- Revisit full conformance language only after a separate MCP Inspector or
  protocol-suite validation task.

## References

- Spec: `specs/mcp-protocol-2026-07-28/`
- Existing metadata ADR:
  `docs/decisions/2026-07-23-mcp-scoped-metadata-and-default-limits.md`
- MCP specification `2026-07-28`: `https://modelcontextprotocol.io/specification/2026-07-28`
- MCP Python SDK v2 documentation: `https://py.sdk.modelcontextprotocol.io/`
