# Tests: CKG Graph Neighborhood

## Acceptance Criteria

- `REQ-001`: HTTP and MCP neighborhood tests assert bounded subgraphs around a
  known page with relation and direction filters.
- `REQ-002`: Draft-filtering tests assert neighborhood lookup cannot expose
  draft-only nodes unless app-level draft access is enabled.
- `REQ-003`: MCP JSON-RPC and Streamable HTTP list and call
  `llmwiki_graph_neighbors`.
- `REQ-004`: Unknown seeds return `unmatched` and empty graph.
- `REQ-005`: Existing safe-output tests continue to assert no local roots leak.
- `REQ-006`: Existing graph and context tests remain unchanged.

## Traversal Loop Matrix

- `GT-010`: Context/query calls return orientation-first and evidence seed pages
  for follow-up traversal without requiring the full graph endpoint.
- `GT-020`: Service, HTTP, MCP JSON-RPC, and MCP Streamable HTTP
  `graph_neighbors` calls return the same typed sidecar relationship edge from
  an overview seed.
- `GT-030`: Relation filtering narrows a seed neighborhood to the requested
  relationship type across the same four surfaces.
- `GT-040`: Unknown seeds return a successful empty neighborhood with
  `unmatched` populated across the same four surfaces.
- `GT-050`: Draft-adjacent nodes stay hidden by default and appear only when
  draft traversal is explicitly enabled on service and network surfaces.

## Unit Tests

- Test neighborhood lookup from a sidecar graph page through `supports`
  relation.
- Test incoming traversal from an external issue node.
- Test unknown seeds.

## Integration / Contract Tests

- Test `GET /graph/neighborhood`.
- Test MCP JSON-RPC `llmwiki_graph_neighbors`.
- Test MCP Streamable HTTP `llmwiki_graph_neighbors`.
- Regenerate `docs/openapi.json`.

## E2E / Smoke Tests

- Existing release smoke remains the default smoke path.

## Manual Checks

- Compare synthetic large-graph `/graph` and `/graph/neighborhood` response time
  and payload size.

## Skipped Or Deferred

- Agent-bridge skill/docs updates are a companion-repo follow-up.
- CKG sidecar metadata expansion is deferred until the neighborhood primitive is
  validated.
