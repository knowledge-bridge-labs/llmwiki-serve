# Plan: English Lexical Analyzer Opt-In

## Approach

Implement the analyzer behind the existing lexical search path, but keep the
legacy analyzer as the product default. The public release surface is explicit
opt-in only:

- `llmwiki-serve serve --analyzer-profile legacy|english`.
- `llmwiki-serve query --analyzer-profile legacy|english`.
- `llmwiki-serve search --analyzer-profile legacy|english`.
- Python `create_app` with the same `legacy|english` profile selection.

The omitted analyzer profile must continue to select `legacy`. HTTP/MCP request
schemas stay unchanged; analyzer selection is startup/API configuration, not a
per-request field. The evaluated `english_additive` and `english_flatlike`
candidates remain decision evidence only; they are not shipped or supported
runtime profiles. `LlmWikiService` follows the same public Python boundary and
must not keep a broader internal profile allowance.

The decision evidence is mixed. On the official SciFact materialization
(`5,183` docs, `300` test queries, `339` qrels), reproducible Windows/DGX
metrics improved from the `legacy` profile:

- nDCG@10 `0.6023109375`, Recall@100 `0.8274444444`, Recall@5
  `0.6655555556`, Hit@5 `0.6833333333`, MRR@10 `0.5667962963`.

The `english` opt-in profile measured:

- nDCG@10 `0.6905159872`, Recall@100 `0.9286666667`, Recall@5
  `0.7459444444`, Hit@5 `0.7666666667`, MRR@10 `0.656265873`.

Published BEIR BM25 (`0.665` nDCG@10 / `0.908` Recall@100) and
Anserini/Pyserini flat BM25 (`0.6789` nDCG@10 / `0.9253` Recall@100) are
contextual same-data references only. Public SciFact reports for this work must
explicitly run `english` and be labeled English opt-in same-data comparisons.
CLI and programmatic public report generation must require explicit
`analyzer_profile` and `implementation_revision` values.

OpenWiki citation fix validation succeeded and latest native quality improved
overall, but the default-English compatibility gate failed. Remaining
generic-shadow regressions include global-map Recall@5 `-0.08`, citation recall
`-0.029412`, and known-item MRR `-0.051282`. Therefore the release decision is
to keep `legacy` as the product default.

Harden compatibility with two exact channels that remain outside normal BM25
statistics:

- Exact authored compound postings for title, summary, body, and tags. A
  single-token compound query must hit this channel, otherwise split English
  components are ignored and the result is empty.
- Exact metadata postings for path and source-reference tokens. These can add a
  small exact score only when the query contains the same original compound
  token; metadata is not stemmed into BM25 content.

Do not implement hybrid or fusion ranking in this spec. Defer that work to a
separate future spec.

## Affected Areas

- Search analyzer and corpus tokenization internals.
- Derived exact compound and metadata postings.
- CLI `serve`, `query`, and `search` option parsing.
- Python `create_app` configuration.
- Search/context ranking tests.
- Benchmark runner configuration and sanitized aggregate reports.
- Public report metadata validation for analyzer profile and implementation
  revision.
- Dependency and third-party notices only if `snowballstemmer` is added.

## Compatibility Notes

Keep omitted-profile behavior identical to the current product behavior:
`legacy`. Literal mode must remain exact substring matching. Legacy mode keeps
its previous BM25 text surface, including source references, and does not use
the exact English channels.

The only release runtime and public Python analyzer choices are `legacy` and
`english`. Internal experimental candidates such as `english_additive` and
`english_flatlike` must be rejected or hidden from public CLI and Python API
surfaces. Public report validation must reject non-public analyzer profiles and
the all-zero implementation-revision placeholder. Tests may use a nonzero
deterministic fake git hash, but tracked final reports must use a real commit.
HTTP/MCP request schemas remain unchanged and must not accept a per-request
analyzer profile.

## Rollback

Because `legacy` remains the default, rollback is limited to disabling or
withholding the public `english` opt-in path and its reports. Final immutable
Windows/DGX reports for
`git:9f03f39666edf0d2516cf1f6d9c7171802eabd2c` now pass public report
validation. Merge, package publish, hosted docs deployment, and full release
validation remain pending.
