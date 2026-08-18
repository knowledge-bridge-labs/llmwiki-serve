# Tests: SQLite GraphStore

## Focused automated tests

- `tests/test_sqlite_graph_store.py`
  - SQLite round-trip and key isolation.
  - corrupt JSON and digest mismatch cache misses.
  - non-SQLite file safety.
  - replace/removal behavior.
  - service, HTTP, MCP graph parity against no-store serving.
  - draft filtering and `allow_drafts` gate.
  - backend exception fallback/fail-fast behavior.
  - cache hit avoids graph recompute.
  - source change creates a new projection-backed graph snapshot.
  - CLI enablement and outside-root path rejection.
  - representative LLMWiki/Obsidian/Foam/Dendron/Quartz/Logseq fixtures.
  - generated OpenWiki-style Markdown fixture.

- `tests/test_graph_engine.py`
  - provider capability flags.
  - typed neighbors, backlinks, paths, by-source-ref, and by-tag.
  - draft visibility isolation.
  - unknown node rejection.

## Release validation

```bash
uv run ruff check .
uv run mypy src
uv run pytest -q tests/test_sqlite_graph_store.py tests/test_graph_engine.py
uv run pytest -q tests/test_service.py tests/test_public_api.py -p no:cacheprovider
uv run python scripts/export_openapi.py --check
uv build
uv run python scripts/release_smoke.py --dist-dir dist
```

## Live GraphRAG smoke

Start a local server against a non-sensitive source with:

```bash
uv run llmwiki-serve serve ./examples/sample-wiki \
  --host 127.0.0.1 \
  --port 8765 \
  --graph-store sqlite \
  --graph-store-path .runtime-logs/sample-wiki-graph.sqlite
```

Then verify:

- `/manifest` advertises `llmwiki_graph_store_sqlite`.
- two `/graph?limit=500` calls return identical closed graphs.
- `/graph/neighborhood?seed=hot&depth=1&limit=20` returns expected neighbors.
- MCP `llmwiki_context`, `llmwiki_search`, `llmwiki_read`,
  `llmwiki_graph`, and `llmwiki_graph_neighbors` return the same source
  evidence as no-store serving.
