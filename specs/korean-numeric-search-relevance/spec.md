# Spec: Korean Numeric Search Relevance

## Status

Draft.

## Problem

A Korean query such as `3차 계약` can rank a long aggregate `INDEX` page above a
focused content page. The current tokenizer loses Korean ordinal morphemes such
as `차`, raw term frequency rewards long pages, and prose issue markers such as
`#20` are harvested as tags that add numeric noise to the search text.

## Goals

- Preserve Korean ordinal/numeric compounds such as `3차`.
- Keep one-character Hangul morphemes instead of dropping them.
- Add length-normalized ranking so long aggregate pages do not dominate
  specific content pages through raw frequency.
- Stop harvesting pure numeric inline hashtags from prose into page tags.
- Trim search snippets to a smaller default while preserving the existing
  result schema.
- Add focused regressions for `3차 계약` and numeric pseudo-tags.

## Non-Goals

- Do not add a new vector or semantic-search dependency.
- Do not add literal grep/find endpoints in this slice.
- Do not add `fields`, `exclude_page_ids`, `min_score`, role filters, or read
  projections in this slice.
- Do not change HTTP, MCP, or model response schemas.

## Requirements

- `REQ-SEARCH-001`: tokenization emits a compound token for digit-plus-Hangul
  forms such as `3차`.
- `REQ-SEARCH-002`: tokenization retains one-character Hangul tokens.
- `REQ-SEARCH-003`: Korean text tokens support conservative bigram recall for
  longer Hangul terms.
- `REQ-SEARCH-004`: search scoring uses bounded term frequency and document
  length normalization.
- `REQ-SEARCH-005`: role boosts must not be additive constants that let meta
  pages win despite weak normalized matches.
- `REQ-SEARCH-006`: pure numeric inline hashtags such as `#20` are not added to
  `WikiPage.tags` or projected tag nodes.
- `REQ-SEARCH-007`: search result snippets keep the existing `snippet` field but
  use a smaller default context window.

## Compatibility

This is a relevance behavior change with stable public schemas. Search result
order and scores may change. Existing clients keep the same HTTP/MCP request and
response shapes.

## Data Safety

The change does not expose new paths, credentials, drafts, or raw files. It
reduces default search-result text volume without adding new payload fields.

## Follow-Up Scope

- Literal `llmwiki_find` or `llmwiki_grep` tool for exact substring and negative
  existence checks.
- Search response field projection, `snippet_chars`, `min_score`, role filters,
  and `exclude_page_ids`.
- Read-response projection to avoid returning summary/headings alongside full
  text when callers do not need them.

## References

- GitHub issue: `#25`
- Architecture: `docs/architecture.md#layer-model`
