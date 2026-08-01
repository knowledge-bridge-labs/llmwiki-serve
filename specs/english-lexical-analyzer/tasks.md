# Tasks: English Lexical Analyzer Opt-In

- [x] Create concise spec package.
- [x] Record ADR for the language-aware analyzer decision.
- [x] Record official SciFact materialization size: `5,183` docs, `300` test
  queries, `339` qrels.
- [x] Record reproducible Windows/DGX `legacy` metrics:
  nDCG@10 `0.6023109375`, Recall@100 `0.8274444444`, Recall@5
  `0.6655555556`, Hit@5 `0.6833333333`, MRR@10 `0.5667962963`.
- [x] Record `english` opt-in metrics: nDCG@10 `0.6905159872`, Recall@100
  `0.9286666667`, Recall@5 `0.7459444444`, Hit@5 `0.7666666667`, MRR@10
  `0.656265873`.
- [x] Record published BEIR BM25 and Anserini/Pyserini flat BM25 references as
  contextual same-data comparison rows only.
- [x] Record OpenWiki result: citation fix succeeded, latest native improved
  overall, and default-English compatibility failed because generic-shadow
  regressions remained.
- [x] Revise final release decision: keep `legacy` as product default and make
  `english` public explicit opt-in.
- [x] Defer hybrid/fusion ranking to a separate future spec.
- [x] Implement `--analyzer-profile legacy|english` for `serve`, `query`, and
  `search`, defaulting to `legacy`.
- [x] Expose the same `legacy|english` selection through Python `create_app`
  and `LlmWikiService`, defaulting to `legacy`.
- [x] Keep HTTP/MCP request schemas unchanged; do not add per-request analyzer
  fields.
- [x] Keep evaluated experimental candidates `english_additive` and
  `english_flatlike` as evidence only; do not ship or support them as runtime
  profiles.
- [x] Implement or harden the `english` analyzer: ASCII punctuation splitting,
  possessives, stopword fallback, stemming, Hangul preservation, mixed
  identifiers, versions, numeric weighting, and literal mode compatibility.
- [x] Add exact authored compound postings for single-token identifier/version
  queries under `english`.
- [x] Replace exact authored compound regex matching on page/query text with a
  bounded linear scanner and adversarial long-input tests.
- [x] Exclude source references from English BM25 and add exact metadata
  postings for path/source-reference tokens.
- [x] Update managed-context compatibility tests so stopwords do not create
  unrelated evidence.
- [x] Harden CLI and programmatic public report generation so both
  `analyzer_profile` and `implementation_revision` are explicit.
- [x] Harden public report validation so it rejects non-public analyzer
  profiles and the all-zero implementation-revision placeholder. Tests may use
  a nonzero deterministic fake git hash, but tracked final reports must use a
  real commit.
- [x] Generate final immutable Windows/DGX sanitized public benchmark reports
  from a real release commit. These reports must explicitly select `english`
  and be labeled English opt-in same-data comparisons.
- [ ] Run the full suite and release validation against the final report
  package.
- [x] Add README and report README benchmark evidence with conservative
  reproduction links.
- [ ] Commit, push, deploy docs, and release after final reports pass
  validation.

## LLMWiki Ingestion Candidates

- `specs/english-lexical-analyzer/`
- `docs/decisions/2026-08-01-language-aware-lexical-analyzer.md`
