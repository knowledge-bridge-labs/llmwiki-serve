# Spec: Operator Default Limits

## Status

Draft.

## Problem

`/graph` and MCP `llmwiki_graph` currently default to a large full-graph
payload. On larger wiki projections, a client or agent that omits `limit` can
accidentally request hundreds of nodes and edges before it has decided whether
the full graph is useful.

Operators also cannot change the omitted-argument defaults at server startup.
They can only rely on every caller passing an explicit `limit`.

## Goals

- Lower the built-in full-graph default to a conservative value.
- Keep the existing explicit request maximum of `2000` graph nodes.
- Allow operators to configure omitted-argument defaults for graph and context
  calls when creating the app or running `serve`.
- Communicate the configured MCP graph/context defaults and graph payload risk
  in JSON-RPC and Streamable HTTP tool metadata.
- Preserve draft filtering, local-path redaction, and graph-neighborhood
  behavior.

## Non-Goals

- Do not change source projection ownership or graph generation.
- Do not add a new graph summarization algorithm.
- Do not remove full-graph access for clients that explicitly request larger
  limits.
- Do not change issue #20 MCP metadata controls except where limit descriptions
  need to compose with them.

## Requirements

- `REQ-LIMITS-001`: Built-in `/graph`, MCP JSON-RPC `llmwiki_graph`, MCP
  Streamable HTTP `llmwiki_graph`, and `LlmWikiService.graph()` defaults return
  at most `100` graph nodes.
- `REQ-LIMITS-002`: Explicit graph limits are still clamped to
  `GRAPH_LIMIT_MIN..GRAPH_LIMIT_MAX`, preserving `GRAPH_LIMIT_MAX = 2000`.
- `REQ-LIMITS-003`: `create_app(...)` accepts `graph_default_limit` and
  `context_default_limit` startup defaults.
- `REQ-LIMITS-004`: `llmwiki-serve serve` accepts equivalent CLI options and
  environment variables.
- `REQ-LIMITS-005`: Invalid startup defaults fail early with operator-readable
  errors instead of silently changing the serving contract.
- `REQ-LIMITS-006`: MCP JSON-RPC and Streamable HTTP tool metadata describe the
  configured context default, graph default, graph maximum, and full-graph
  payload risk.

## Compatibility

- HTTP: `/graph` default changes from `500` to `100`; explicit `limit` behavior
  remains backward compatible up to the existing max.
- HTTP: `/query` and `/search` use the configured context default only when the
  request omits `limit`; explicit request bodies keep their existing validation.
- MCP JSON-RPC and Streamable HTTP: omitted `limit` for `llmwiki_context`,
  `llmwiki_search`, and `llmwiki_graph` uses the configured defaults.
- CLI: additive `serve` options and environment variables.
- Service API: `LlmWikiService.graph()` default changes to `100`; explicit
  service calls remain unchanged.

## Data Safety

The change only affects response size defaults and metadata text. It does not
expose new source content, local roots, credentials, private endpoints, or
draft-only nodes.

## References

- GitHub issue: `#21`
- Architecture: `docs/architecture.md#graph-projection`
- Related ADR: `docs/decisions/2026-07-17-serve-graph-neighborhood-boundary.md`
