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

- [ ] Design literal grep/find tool and API/MCP contract.
- [ ] Design search payload field projection and snippet controls.
- [ ] Design read payload projection to avoid redundant fields.
- [ ] Consider `min_score`, confidence metadata, role filters, and
  `exclude_page_ids`.

## LLMWiki Ingestion Candidates

- `specs/korean-numeric-search-relevance/`
