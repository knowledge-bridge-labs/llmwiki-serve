# Spec: Context Graph Limit

## Status

Draft.

## Problem

Every `/query` and MCP `llmwiki_context` pack embeds a graph sample whose size
is fixed at 120 nodes in `LlmWikiService.context()`. On link-dense wikis that
sample dominates the pack: on one 98-page production wiki the graph field was
48.6K of a roughly 56K-character pack, while orientation, evidence, and
`retrieval_guidance` together were about 7K.

The recommended direct-agent workflow is context first
(`docs/decisions/2026-08-02-agent-guided-lexical-default-agent-workflow.md`).
When the context pack is too large for a chat agent that resends tool results
on every turn, operators disable `llmwiki_context` entirely, and agents lose
authored orientation and retrieval guidance. Operators have no way to keep the
workflow while dropping the graph sample, which the
`operator-default-limits` spec already identifies as a payload risk.

## Goals

- Let operators set the number of graph nodes embedded in each context pack,
  including `0` to omit graph nodes and edges.
- Keep the built-in default at `120` so existing responses are unchanged.
- Keep the `graph` field and its `{nodes, edges}` shape on every context pack.
- Apply the same setting to HTTP `/query`, MCP JSON-RPC, MCP Streamable HTTP,
  and opt-in A2A-style context calls.

## Non-Goals

- Do not add a per-request graph-size argument to `/query` or
  `llmwiki_context`; tool schemas stay unchanged.
- Do not change `/graph`, `llmwiki_graph`, graph-neighborhood tools, graph
  projection, or the `graph_default_limit` contract.
- Do not change evidence, orientation, ranking, or `retrieval_guidance`.

## Requirements

- `REQ-CGL-001`: `LlmWikiService(..., context_graph_limit=N)` embeds at most
  `N` graph nodes in each context pack, with edges restricted to those nodes
  and bounded by the same limit (existing `closed_graph_payload` semantics).
- `REQ-CGL-002`: The default is `120`, the previous hard-coded value.
- `REQ-CGL-003`: `0` returns `graph == {"nodes": [], "edges": []}`.
- `REQ-CGL-004`: Values outside `0..2000`, booleans, and non-integers raise
  `ValueError` naming `context_graph_limit` at construction time.
- `REQ-CGL-005`: `create_app(..., context_graph_limit=N)` passes the setting to
  its service; `llmwiki-serve serve --context-graph-limit N` and
  `LLMWIKI_CONTEXT_GRAPH_LIMIT` resolve to it. Invalid CLI values fail without
  a traceback.
- `REQ-CGL-006`: Evidence, orientation, limitations, and `retrieval_guidance`
  are identical for the same query regardless of the setting.

## Compatibility

- Additive. Without the new option every response is byte-compatible.
- No OpenAPI or MCP schema change; `graph` stays a required field.

## Data Safety

The setting only reduces response size. It exposes no new source content,
local roots, credentials, or draft-only nodes; draft filtering still applies
to the graph view before the limit.

## References

- `specs/operator-default-limits/spec.md`
- `docs/decisions/2026-08-02-agent-guided-lexical-default-agent-workflow.md`
- `docs/architecture.md`
