# MCP Manifest-Scoped Metadata

## Problem

MCP clients choose tools from server names, instructions, and tool descriptions.
`llmwiki-serve` currently exposes generic MCP text even when it serves one
specific wiki, so multiple wiki servers are indistinguishable to an agent.

## Goals

- Default MCP Streamable HTTP server metadata should name the served wiki.
- Default MCP JSON-RPC and Streamable HTTP tool descriptions should include a
  wiki/source scope without changing tool names or argument shapes.
- Operators should be able to override the server name, instructions, and tool
  description prefix from the Python API and `serve` CLI.
- Missing or unsupported roots should preserve safe startup/error behavior.

## Non-Goals

- Do not rename MCP tools.
- Do not change tool call payloads, response shapes, ranking, or default limits.
- Do not add a separate MCP server per source.
- Keep default-limit behavior owned by the separate operator-default-limits
  spec.

## Requirements

- `REQ-MCP-META-001`: `create_app(...)` derives default FastMCP server name and
  instructions from `WikiManifest.title`, `description`, `source_id`,
  `public_uri`, `adapter`, and `implementation` when a manifest is available.
- `REQ-MCP-META-002`: MCP-style JSON-RPC `tools/list` descriptions include a
  served-wiki scope label based on manifest title and source identity.
- `REQ-MCP-META-003`: MCP Streamable HTTP tool descriptions use the same scoped
  descriptions as JSON-RPC.
- `REQ-MCP-META-004`: Python callers can override MCP server name,
  instructions, and tool description prefix through additive `create_app(...)`
  parameters.
- `REQ-MCP-META-005`: CLI operators can set the same overrides with additive
  flags or environment variables.
- `REQ-MCP-META-006`: Tool names, arguments, response payloads, draft filtering,
  CORS, I/O logging, source-bundle identity, and query limits remain compatible.

## Compatibility

This is an additive metadata change. Existing MCP clients can keep calling the
same tool names with the same arguments. Clients that match exact tool
description strings may see the new scoped prefix, which is intentional for
agent-side tool selection.
