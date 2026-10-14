# Tests: Context Graph Limit

## Acceptance Criteria

- The built-in default stays `120` and context packs still carry graph nodes.
- A positive limit bounds nodes and edges; every edge connects kept nodes.
- `0` returns an empty `{nodes, edges}` graph while evidence is still present.
- Evidence order and `retrieval_guidance` match the default for the same query.
- Invalid values (`-1`, `2001`, `True`, `1.5`, `"3"`) are rejected by the
  service; out-of-range values are rejected by `create_app`.
- HTTP `/query`, MCP JSON-RPC `llmwiki_context`, and MCP Streamable HTTP
  `llmwiki_context` all honor the configured limit.
- `serve --context-graph-limit` and `LLMWIKI_CONTEXT_GRAPH_LIMIT` resolve to the
  app setting; an invalid flag value exits non-zero.

## Targeted Validation

- `uv run pytest -q tests/test_context_graph_limit.py`
- `uv run python scripts/export_openapi.py --check`
