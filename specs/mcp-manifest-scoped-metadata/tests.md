# MCP Manifest-Scoped Metadata Tests

## Targeted Tests

- JSON-RPC `tools/list` includes manifest title and source id in tool
  descriptions while preserving tool names.
- `create_app(...)` override parameters can replace the JSON-RPC tool
  description prefix.
- FastMCP metadata uses manifest-derived server name and instructions, and its
  registered tools use the same scoped descriptions.
- FastMCP override parameters replace server name, instructions, and
  description prefix.
- `serve` CLI accepts override flags and passes the resulting metadata into the
  created app.

## Regression Checks

- Existing MCP `tools/call` tests continue to prove response compatibility.
- Existing include-draft and graph-neighborhood tests prove issue #21/default
  limit behavior was not changed.

## Validation Command

```bash
uv run pytest -q tests/test_service.py -k "mcp or cli_uses_mcp_metadata"
```
