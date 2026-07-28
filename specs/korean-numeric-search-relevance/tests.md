# Tests: Korean Numeric Search Relevance

## Acceptance

- `tokenize("3차 계약")` includes `3차`, `차`, and `계약`.
- In a fixture with a long aggregate `index.md` and a focused contract page,
  `LlmWikiService.search("3차 계약")` ranks the focused contract page first.
- Literal search mode finds `3차 계약` as an exact Korean/numeric substring and
  returns a snippet containing the phrase; a missing literal returns no results.
- `min_score` can drop the lower-confidence long `INDEX` hit while preserving
  the focused contract page.
- `exclude_page_ids` prevents already-seen pages from being returned again.
- Search/query `fields` and `snippet_chars` reduce payloads and omit unrequested
  result fields.
- Read `fields` projection can return summary metadata without the full `text`
  body or redundant headings.
- A page containing prose markers such as `#20` and `#27` does not expose those
  values through `tags` or projected tag nodes.
- Existing default HTTP/MCP search shapes still return `results` containing full
  `SearchResult` fields.

## Validation

- Focused pytest tests for the tokenizer, scorer, and parser tag filter.
- Focused pytest tests for literal search, payload projection, `min_score`,
  `exclude_page_ids`, read projection, and HTTP/MCP/CLI parity.
- Generated OpenAPI check for the additive request and projection schemas.
- Full `pytest -q`.
- CLI smoke against the sample wiki.
