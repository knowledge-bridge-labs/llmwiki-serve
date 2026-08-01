# Plan: Lexical Postings Index

## Approach

Build postings while `build_search_corpus()` already computes token counters
and document statistics. Store only derived in-memory structures on
`SearchCorpus`, keyed by token and document index. Lexical search then scores
candidate documents from postings instead of scanning every document.

Keep the scorer, result materialization, sorting, filtering, rounding, and
literal mode behavior unchanged. If exact parity cannot be proven, rollback the
optimization and keep the current all-document scan.

## Affected Areas

- Source: `src/llmwiki_serve/search.py` and any local helper types already used
  by `SearchCorpus` or `_IndexViews`.
- Tests: focused parity and performance tests around service search behavior.
- Benchmarks: repeat existing SciFact runs on Windows and DGX Spark Ubuntu.
- Contracts: no HTTP, MCP, CLI, OpenAPI, persisted file, or benchmark artifact
  schema change.

## Implementation Notes

- Use immutable or effectively immutable postings values after corpus build.
- Keep approved-only and all-drafts corpora independent.
- Keep excluded documents in corpus statistics even when they are filtered from
  candidate scoring or output.
- Preserve current result ordering by materializing scored candidates in the
  same deterministic order before existing ranking logic runs.
- Report index build time separately from warm search latency.

## Risks

- Memory use increases because token counts and postings coexist.
  Mitigation: keep postings compact, measure both corpus views, and document the
  observed memory budget before release.

- Candidate-only scoring could accidentally skip a document that should tie or
  pass a low-score threshold.
  Mitigation: add exact parity checks over representative lexical queries,
  exclusions, and `min_score` cases.

- Floating-point accumulation order could change rounded scores.
  Mitigation: preserve query-token iteration order and score accumulation order
  for each document.

## Rollback

Remove the derived postings fields and route lexical search back to the current
all-document scan. Because there is no persistence or public contract change,
rollback does not require data migration.
