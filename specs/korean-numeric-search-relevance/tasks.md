# Tasks: Korean Numeric Search Relevance

- [x] Read issue `#25`, architecture docs, parser/search code, and current
  search tests.
- [x] Create feature spec files.
- [x] Update Korean/numeric tokenizer.
- [x] Add BM25-style ranking length normalization.
- [x] Change role boosts from additive constants to small normalized
  multipliers.
- [x] Filter pure numeric inline hashtags from parsed tags.
- [x] Trim default search snippet length without changing schema.
- [x] Add focused Korean query and numeric pseudo-tag tests.
- [x] Run validation.

## Follow-Up Tasks

- [x] Add API/MCP/CLI-compatible literal search mode for exact substring checks.
- [x] Add search/query payload field projection and snippet controls.
- [x] Add read payload projection to avoid redundant fields when requested.
- [x] Add `min_score` and `exclude_page_ids` controls.
- [x] Keep role filters deferred until there is concrete caller evidence.
- [x] Keep broader CJK recall deferred beyond the existing conservative bigram
  support until there is a failing corpus.
- [x] Run final validation after OpenAPI regeneration.

## LLMWiki Ingestion Candidates

- `specs/korean-numeric-search-relevance/`
