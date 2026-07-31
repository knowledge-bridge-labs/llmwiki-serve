# Tasks: Lexical Postings Index

- [x] Create short spec package.
- [x] Record ADR rationale and boundary.
- [x] Record data-safety and rollback requirements.
- [x] Implement derived in-memory postings inside `SearchCorpus` and
  `_IndexViews`.
- [x] Preserve literal mode unchanged.
- [x] Add exact lexical parity tests for ranking, scores, tie/path order, role
  and managed-context prior behavior, exclusions, and `min_score`.
- [x] Add checks that excluded documents remain in corpus statistics.
- [x] Validate approved-only and all-drafts corpus views.
- [x] Run Windows repository validation and temporary current-default SciFact
  validation.
- [ ] Run DGX Spark Ubuntu benchmark validation.
- [x] Confirm normalized input and source bundle checksums, quality metrics,
  result payloads and their checksums, and payload byte quantiles are unchanged.
- [x] Confirm the report file checksum changes as expected with latency or
  index-build telemetry.
- [ ] Accept final immutable-revision public Windows and DGX Spark Ubuntu
  reports before publishing release benchmark evidence.
- [ ] Confirm a documented warm search p95 latency budget after final
  environment runs; index build time must remain reported separately.

## LLMWiki Ingestion Candidates

- `specs/lexical-postings-index/`
