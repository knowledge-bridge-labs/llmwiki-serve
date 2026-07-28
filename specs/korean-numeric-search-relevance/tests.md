# Tests: Korean Numeric Search Relevance

## Acceptance

- `tokenize("3차 계약")` includes `3차`, `차`, and `계약`.
- In a fixture with a long aggregate `index.md` and a focused contract page,
  `LlmWikiService.search("3차 계약")` ranks the focused contract page first.
- A page containing prose markers such as `#20` and `#27` does not expose those
  values through `tags` or projected tag nodes.
- Existing HTTP/MCP search shapes still return `results` containing
  `SearchResult` fields.

## Validation

- Focused pytest tests for the tokenizer, scorer, and parser tag filter.
- Full `pytest -q`.
- CLI smoke against the sample wiki.
