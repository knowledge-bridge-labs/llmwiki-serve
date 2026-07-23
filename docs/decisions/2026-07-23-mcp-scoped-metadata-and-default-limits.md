# ADR: MCP Scoped Metadata And Operator Default Limits

## Status

Accepted.

## Context

`llmwiki-serve` can expose many local or private knowledge sources through the
same HTTP and MCP tool names. Generic MCP server names and tool descriptions
make those sources hard for agents to distinguish. The previous full-graph
default also allowed omitted-limit graph calls to return large payloads before a
client had selected a focused graph inspection path.

## Decision

Derive MCP-facing metadata from the served wiki manifest by default. The
FastMCP server name, FastMCP instructions, JSON-RPC `tools/list` descriptions,
and Streamable HTTP tool descriptions use the manifest title, description,
source id, public URI, adapter, and implementation when available.

Keep tool names and call payloads unchanged. Operators may override the MCP
server name, instructions, and tool-description prefix through `create_app(...)`
or `llmwiki-serve serve` options/environment variables.

Make omitted-limit defaults explicit startup configuration. Full-graph surfaces
default to 100 nodes and retain the explicit 2,000-node maximum. Context/search
surfaces default to 8 evidence/results and can be configured at app or CLI
startup. Invalid startup defaults fail early with operator-readable errors.

## Consequences

- Multi-wiki MCP clients get source-scoped metadata without per-source tool
  renaming.
- Existing clients can keep using the same tool names and response contracts.
- Clients that depended on omitted full-graph calls returning 500 nodes should
  pass an explicit `limit`.
- MCP descriptions now advertise configured default limits and steer clients
  toward graph-neighborhood inspection before large full-graph reads.
- Runtime limit changes are startup-scoped; per-request explicit limits keep the
  existing validation and clamp boundaries.

## Follow-Ups

- Mirror the option reference in `llmwiki-docs` when the public docs portal is
  refreshed.
- Revisit richer MCP instructions once bridge-side multi-source routing settles.

## References

- Spec: `specs/mcp-manifest-scoped-metadata/`
- Spec: `specs/operator-default-limits/`
- GitHub issue: `#20`
- GitHub issue: `#21`
