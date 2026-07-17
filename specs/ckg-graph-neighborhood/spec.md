# Spec: CKG Graph Neighborhood

## Status

Draft.

## Problem

Agent-facing skills can tell Codex, Claude Code, Copilot, and similar host
agents to inspect graph context before guessing dependency or prerequisite
chains. Today the only graph-shaped serving surface is the full `/graph`
payload or MCP `llmwiki_graph` tool. That works for inspection, but it is a
coarse primitive for CKG-like workflows because clients often need only a small
neighborhood around a known page, source reference, tag, or sidecar node.

## Goals

- Add a read-only graph-neighborhood operation over the existing projected
  graph.
- Keep `llmwiki-serve` as the source-owned projection layer, not a graph
  compiler, model runtime, or orchestration engine.
- Make the operation available through HTTP and both MCP tool surfaces.
- Preserve draft filtering and local-path redaction behavior.
- Provide a local performance check that compares graph neighborhood lookup
  with the existing full graph path.

## Non-Goals

- Do not define a formal CKG standard or claim conformance to one.
- Do not add a separate CKG MCP server.
- Do not make `llmwiki-serve` decide final answer synthesis or multi-source
  routing.
- Do not expose raw file paths, credentials, private endpoints, or executable
  tool connection details.

## Requirements

- `REQ-001`: Clients can request a subgraph around one or more seed nodes or
  labels with bounded depth, direction, relation filters, and limit.
- `REQ-002`: Neighborhood lookup uses the same approved-only graph visibility
  rules as `/graph` unless draft access is explicitly enabled.
- `REQ-003`: HTTP, MCP JSON-RPC, and MCP Streamable HTTP expose the same
  operation.
- `REQ-004`: Unknown seed values return a successful empty graph with an
  `unmatched` list, not a server error.
- `REQ-005`: The response contains only projected node/edge data and traversal
  metadata; it does not expose local source roots.
- `REQ-006`: Existing `/graph`, `/query`, `/search`, `/read`, and source-bundle
  behavior remains backward compatible.

## User / Agent Flow

1. The host agent calls `llmwiki_context` or `/query` for orientation.
2. If the question is about dependencies, prerequisites, architecture,
   ownership, schema, source lineage, or policy, the host agent calls graph
   neighborhood lookup around a returned page, tag, source ref, or sidecar node.
3. The host agent reads exact pages when it needs precise claims.
4. The host agent composes the final answer or delegates synthesis through
   `llmwiki-agent-bridge`.

## Compatibility

- CLI: no new CLI command in this slice.
- HTTP: additive `GET /graph/neighborhood`.
- MCP: additive `llmwiki_graph_neighbors` tool on JSON-RPC and Streamable HTTP.
- A2A-style: no new A2A-specific shape in this slice.
- Source-folder adapters: no input format change.
- Existing clients: no existing endpoint or field is removed or renamed.

## Data Safety

Neighborhood lookup operates only on the already projected graph. Network
surfaces must continue to suppress local roots and draft-only nodes unless the
app is explicitly configured to serve drafts.

## Open Questions

- Should future source-bundle guidance advertise preferred seed nodes for
  common agent tasks?
- Should typed sidecar graph metadata be widened beyond the currently projected
  confidence/source/path fields?

## References

- ADR: `docs/decisions/2026-07-17-serve-graph-neighborhood-boundary.md`
