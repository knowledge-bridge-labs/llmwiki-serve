# Spec: Korean Numeric Search Relevance

## Status

Implemented through the `0.2.3` relevance slice plus the follow-up payload and
literal-retrieval slice for issue `#25`.

## Problem

A Korean query such as `3차 계약` can rank a long aggregate `INDEX` page above a
focused content page. The current tokenizer loses Korean ordinal morphemes such
as `차`, raw term frequency rewards long pages, and prose issue markers such as
`#20` are harvested as tags that add numeric noise to the search text.

After the first relevance slice, agents still needed a cheap way to answer
"does this exact string exist?" and a way to avoid paying for snippets, repeated
pages, and full read bodies when they only need triage metadata.

## Goals

- Preserve Korean ordinal/numeric compounds such as `3차`.
- Keep one-character Hangul morphemes instead of dropping them.
- Add length-normalized ranking so long aggregate pages do not dominate
  specific content pages through raw frequency.
- Stop harvesting pure numeric inline hashtags from prose into page tags.
- Trim search snippets to a smaller default while preserving the existing
  result schema.
- Add a literal search mode for exact substring retrieval and trustworthy
  negative checks.
- Add bounded search/query result projection controls for snippets, result
  fields, low-score filtering, and already-seen page exclusions.
- Add read field projection so callers can request metadata or summaries
  without the full page body.
- Add focused regressions for `3차 계약` and numeric pseudo-tags.

## Non-Goals

- Do not add a new vector or semantic-search dependency.
- Do not add a separate grep/find endpoint while `mode=literal` on the existing
  search/query surfaces covers exact substring lookup.
- Do not add role filters in this slice.
- Do not add confidence-envelope metadata beyond the explicit `min_score`
  caller control.

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
- `REQ-SEARCH-008`: search and query support `mode=literal` for exact
  substring matching, including Korean/numeric phrases such as `3차 계약`.
- `REQ-SEARCH-009`: search and query support `snippet_chars`, `fields`,
  `min_score`, and `exclude_page_ids` without changing default full-payload
  behavior.
- `REQ-SEARCH-010`: read supports `fields` projection over `WikiPage` fields;
  omitted fields are not serialized on HTTP/MCP projected reads.

## Compatibility

This is an additive contract change. Default search, query, and read responses
remain full and backward-compatible. Callers that opt into `fields` receive
partial result/page objects, so generated OpenAPI documents projection variants.
Search result order and scores may still differ from pre-`0.2.3` behavior
because lexical ranking uses BM25-style normalization.

## Data Safety

The change does not expose new paths, credentials, drafts, or raw files. Network
manifest and health responses continue to redact local roots. Projection options
only remove fields from already-authorized search/query/read payloads.

## Follow-Up Scope

- Role filters can be considered later if there is evidence that callers need
  explicit meta-page inclusion/exclusion beyond current ranking and
  `exclude_page_ids`.
- Broader CJK recall beyond the existing conservative bigram support remains
  deferred until a concrete failing corpus is available.

## References

- GitHub issue: `#25`
- Architecture: `docs/architecture.md#layer-model`
