# 2026-08-01 Decision Record: Language-Aware Lexical Analyzer Opt-In

## Status

Accepted and implemented for the release runtime boundary. `legacy` remains the
product default because OpenWiki generic-shadow class gates regress under
English. The `english` analyzer profile is public explicit opt-in through
`--analyzer-profile legacy|english` on `serve`, `query`, and `search`, and
through Python `create_app` and `LlmWikiService`. HTTP/MCP request schemas stay
unchanged. Evaluated experimental candidates are evidence only, not shipped or
supported runtime profiles. Public report generation now requires explicit
analyzer profile and implementation revision metadata, and validation rejects
non-public profiles and all-zero placeholder revisions. Focused tests/checks
have passed. Final immutable Windows/DGX reports for
`git:8d04e8a46487827ee488a7ddab005aaab8dd885d` now pass public report
validation, and repository README/report evidence is updated. The English
tokenizer and exact-compound paths use explicit linear scanners. On PR #35,
Linux and Windows Python 3.11/3.12 CI jobs and both CodeQL checks pass at that
head. Full release validation, merge, hosted docs deployment, and package
release remain pending.

## Context

`llmwiki-serve` currently uses a lexical analyzer that retains ASCII punctuation
inside tokens and does not apply English stopword removal or stemming. The
official BEIR SciFact materialization used for this decision contains `5,183`
docs, `300` test queries, and `339` qrels.

The reproducible Windows/DGX `legacy` metrics on that materialization are:

- nDCG@10 `0.6023109375`.
- Recall@100 `0.8274444444`.
- Recall@5 `0.6655555556`.
- Hit@5 `0.6833333333`.
- MRR@10 `0.5667962963`.

The `english` opt-in metrics are:

- nDCG@10 `0.6905159872`.
- Recall@100 `0.9286666667`.
- Recall@5 `0.7459444444`.
- Hit@5 `0.7666666667`.
- MRR@10 `0.656265873`.

Published external references are contextual same-data comparison rows only:
BEIR BM25 reports nDCG@10 `0.665` and Recall@100 `0.908`; Anserini/Pyserini
flat BM25 reports nDCG@10 `0.6789` and Recall@100 `0.9253`.

OpenWiki citation fix validation succeeded, and latest native OpenWiki quality
improved overall:

- Baseline native `0.2.7` Recall@5/MRR/nDCG/citation recall:
  `0.91694`/`0.90545`/`0.84240`/`0.87421`.
- Latest native Recall@5/MRR/nDCG/citation recall:
  `0.92079`/`0.91090`/`0.85198`/`0.88050`.

The default-English compatibility gate still failed because regressions remained
in the generic-shadow class:

- Shadow baseline Recall@5/MRR/nDCG/citation recall:
  `0.99600`/`0.96667`/`0.91997`/`0.99265`.
- Latest English shadow Recall@5/MRR/nDCG/citation recall:
  `0.97633`/`0.95667`/`0.91202`/`0.96324`.
- Remaining gate regressions: generic-shadow global-map Recall@5 `-0.08`,
  citation recall `-0.029412`, known-item MRR `-0.051282`.

An ADR is required because analyzer selection changes ranking behavior observed
through CLI, HTTP, MCP-style, Streamable HTTP, Python app, and context outputs
even while HTTP/MCP request schemas remain unchanged.

The evaluated experimental candidates `english_additive` and
`english_flatlike` are retained as decision evidence only. They are not shipped
or supported runtime profiles.

## Decision

Keep `legacy` as the product default. Ship the standard `english` analyzer as a
public explicit opt-in profile only. Release runtime and public Python surfaces
support exactly `legacy|english`.

The selected analyzer splits ASCII punctuation, lowercases English terms,
handles possessives, removes English stopwords, and stems English tokens using
`snowballstemmer`. It preserves Hangul tokenization, numeric weighting, literal
exact-substring mode, the existing product text surface, and role multipliers.
Stopword removal may change lexical evidence ranking. Orientation remains a
separate context contract, and the index must not add hidden synthetic lexical
terms based on orientation roles.

Add two derived exact channels outside normal BM25:

- Authored exact compound postings from title, summary, body, and tags. If a
  query is a single compound identifier or version token such as
  `release.v1-beta`, `http_response`, or `v1.2.3`, split English components
  alone are not sufficient; an exact authored compound or exact metadata token
  must exist.
- Exact metadata postings from path and source-reference values. These tokens
  can retrieve or lightly boost only when the query contains the same original
  compound token. Source references are excluded from stemmed English BM25
  content so metadata cannot create broad hub matches.

Trailing sentence punctuation is not part of an exact compound token. For
example, authored text ending in `release.v1-beta.` contributes the exact token
`release.v1-beta`, not `release.v1-beta.`.

English raw-token extraction and exact compound token extraction are implemented
as linear scanners over page and query text. The tokenizer performs one pass
over supported Unicode code points and preserves the existing normalization,
stopword, stemming, and mixed ASCII/Hangul pipeline. Neither path may apply a
backtracking regular expression to untrusted authored content or
operator-provided queries.

Expose only `legacy|english` publicly:

- CLI: `--analyzer-profile legacy|english` on `serve`, `query`, and `search`.
- Python: `create_app` and `LlmWikiService` accept the same public choices and
  do not keep a broader internal analyzer profile allowance.
- HTTP/MCP request schemas: unchanged, with no per-request analyzer selector.

Evaluated experimental candidates such as `english_additive` and
`english_flatlike` are not public profiles, shipped profiles, or supported
runtime profiles. Public SciFact reports for this work must explicitly select
`english` and be labeled English opt-in same-data comparisons. CLI and
programmatic public report generation must require explicit `analyzer_profile`
and explicit `implementation_revision`; public validation must reject
non-public analyzer profiles and the all-zero placeholder revision. Tests may
use a nonzero deterministic fake git hash, but tracked final reports must use a
real commit. Hybrid/fusion ranking is deferred to a separate future spec.

## Consequences

- Existing users retain `legacy` ranking unless they explicitly opt in.
- The `english` profile can be compared publicly on SciFact without implying a
  default behavior change.
- Single-token identifier and version lookup becomes stricter only under the
  English opt-in profile: exact compound text or exact metadata is required.
- Source-reference matches remain available through exact metadata lookup under
  `english` but do not contribute split/stemmed BM25 terms there.
- `snowballstemmer` dependency metadata and `THIRD_PARTY_NOTICES.md` are part
  of the release surface if the implementation uses that package.
- Rollback is limited because `legacy` remains the default.
- Diagnostic timing numbers remain internal evidence and must not be used as
  public performance claims.
- Stale reports have been replaced by final immutable Windows/DGX reports from
  the release implementation revision.
- Final immutable Windows/DGX reports pass public report validation; release
  steps remain pending.

## Follow-Up Work

- Run the full suite and release validation against the final report package.
- Merge, deploy hosted docs, and release after final reports pass validation
  and release gates.
- Open a separate future spec for any hybrid/fusion ranking proposal.

## References

- Spec: `specs/english-lexical-analyzer/`
- BEIR repo: https://github.com/beir-cellar/beir
- BEIR dataset table: https://github.com/beir-cellar/beir/wiki/Datasets-available
- BEIR metrics: https://github.com/beir-cellar/beir/wiki/Metrics-available
- BEIR paper: https://datasets-benchmarks-proceedings.neurips.cc/paper/2021/file/65b9eea6e1cc6bb9f0cd2a47751a186f-Paper-round2.pdf
- Resources for Brewing BEIR: https://arxiv.org/abs/2306.07471
- Anserini SciFact flat BM25: https://github.com/castorini/anserini/blob/master/docs/reproduce/from-document-collection/beir-v1.0.0-scifact.flat.md
- SciFact repo: https://github.com/allenai/scifact
- SciFact license: https://github.com/allenai/scifact/blob/master/LICENSE.md
- SciFact paper: https://aclanthology.org/2020.emnlp-main.609/
- Snowball license: https://snowballstem.org/license.html
- snowballstemmer PyPI: https://pypi.org/project/snowballstemmer/
