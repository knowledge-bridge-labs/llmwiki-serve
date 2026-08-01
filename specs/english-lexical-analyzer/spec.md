# Spec: English Lexical Analyzer Opt-In

## Status

Implementation is complete for the release runtime boundary. `legacy` remains
the product default because OpenWiki generic-shadow class gates regress under
English. The `english` analyzer is a public explicit opt-in profile through
`--analyzer-profile legacy|english` on `serve`, `query`, and `search`, and
through Python `create_app` and `LlmWikiService`. HTTP/MCP request schemas stay
unchanged. Internal experimental candidates remain evaluated evidence only and
are not shipped or supported runtime profiles. Public report generation now
requires explicit analyzer profile and implementation revision metadata, and
validation rejects non-public profiles and all-zero placeholder revisions.
Focused tests/checks have passed. Final immutable Windows/DGX reports for
`git:8d04e8a46487827ee488a7ddab005aaab8dd885d` now pass public report
validation, and repository README/report evidence is updated. The English
tokenizer and exact-compound paths use explicit linear scanners. On PR #35,
Linux and Windows Python 3.11/3.12 CI jobs and both CodeQL checks pass at that
head. Full release validation, merge, hosted docs deployment, and package
release remain pending.

## Problem

The current lexical analyzer keeps ASCII punctuation inside tokens and does not
apply English stopword removal or stemming. The official BEIR SciFact
materialization used for this decision contains `5,183` docs, `300` test
queries, and `339` qrels.

The reproducible Windows/DGX `legacy` metrics are:

- nDCG@10 `0.6023109375`.
- Recall@100 `0.8274444444`.
- Recall@5 `0.6655555556`.
- Hit@5 `0.6833333333`.
- MRR@10 `0.5667962963`.

The `english` opt-in metrics on the same materialization are:

- nDCG@10 `0.6905159872`.
- Recall@100 `0.9286666667`.
- Recall@5 `0.7459444444`.
- Hit@5 `0.7666666667`.
- MRR@10 `0.656265873`.

Published external references are contextual same-data comparison rows only:
BEIR BM25 reports nDCG@10 `0.665` and Recall@100 `0.908`; Anserini/Pyserini
flat BM25 reports nDCG@10 `0.6789` and Recall@100 `0.9253`. These references
do not certify this implementation or any public report.

OpenWiki citation fix validation succeeded, and the latest native OpenWiki
numbers improved overall after that fix:

- Baseline native `0.2.7` Recall@5/MRR/nDCG/citation recall:
  `0.91694`/`0.90545`/`0.84240`/`0.87421`.
- Latest native Recall@5/MRR/nDCG/citation recall:
  `0.92079`/`0.91090`/`0.85198`/`0.88050`.

However, English default compatibility failed because regressions remained in
the OpenWiki generic-shadow class:

- Shadow baseline Recall@5/MRR/nDCG/citation recall:
  `0.99600`/`0.96667`/`0.91997`/`0.99265`.
- Latest English shadow Recall@5/MRR/nDCG/citation recall:
  `0.97633`/`0.95667`/`0.91202`/`0.96324`.
- Remaining gate regressions: generic-shadow global-map Recall@5 `-0.08`,
  citation recall `-0.029412`, known-item MRR `-0.051282`.

Candidate timing and internal experimental candidates are diagnostic only and
must not be used as public performance claims. The evaluated
`english_additive` and `english_flatlike` candidates remain decision evidence
only. Release runtime and public Python surfaces support exactly
`legacy|english`; `LlmWikiService` must not keep a broader internal analyzer
profile allowance.

Compatibility validation found that the standard English analyzer must not let
a single compound identifier query match pages that only contain split English
components. A query such as `release.v1-beta` or `http_response` is treated as
an exact compound lookup when it is the only query token. The exact lookup uses
derived postings from authored page title, summary, body, and tags. It does not
write synthetic terms into authored files and does not change the public search
payload shape.

English BM25 content excludes `source_refs`. Path and source-reference values
are available through a narrow exact metadata channel only when the query
contains the same original compound token. Metadata is not stemmed and cannot
create broad hub matches through split words.

## Goals

- Define and ship a language-aware English lexical analyzer as a public explicit
  opt-in profile while keeping `legacy` as the product default.
- Preserve current Hangul tokenization, numeric weighting, and literal mode.
- Preserve exact single-token lookup behavior for identifiers, versions, and
  path/source-reference metadata without broadening BM25 content.
- Ensure `serve`, `query`, `search`, Python `create_app`, and
  `LlmWikiService` expose only `legacy|english` as public analyzer profiles.
- Keep HTTP/MCP request schemas unchanged.
- Label SciFact public reports as English opt-in same-data comparisons.
- Require explicit analyzer profile and implementation revision metadata for
  public report generation and validation.
- Keep merge, hosted docs deployment, package release, and full release
  validation pending until the release validation package is complete.

## Non-Goals

- No benchmark certification claim.
- No qrel-identity tuning, per-query special cases, or dataset-text-derived
  rules.
- No release runtime or public Python exposure of evaluated experimental
  candidates such as `english_additive` or `english_flatlike`.
- No HTTP/MCP request schema changes or per-request analyzer selection.
- No hybrid or fusion ranking behavior; that work is deferred to a separate
  future spec.

## Requirements

- `REQ-ELA-001`: The product default must remain `legacy` unless a future spec
  reopens the OpenWiki compatibility gate.
- `REQ-ELA-002`: Public CLI entrypoints `serve`, `query`, and `search` must
  accept exactly `--analyzer-profile legacy|english`, defaulting to `legacy`.
- `REQ-ELA-003`: Public Python surfaces, including `create_app` and
  `LlmWikiService`, must expose exactly the same `legacy|english` selection,
  defaulting to `legacy`; they must not retain a broader internal profile
  allowance.
- `REQ-ELA-004`: HTTP/MCP request schemas must stay unchanged; analyzer
  selection is not a per-request field.
- `REQ-ELA-005`: Evaluated experimental candidates such as
  `english_additive` and `english_flatlike` must not be accepted by release
  runtime, public CLI, or public Python API surfaces.
- `REQ-ELA-006`: Public SciFact reports for this work must explicitly select
  `english` and be labeled English opt-in same-data comparisons.
- `REQ-ELA-007`: The `english` analyzer splits ASCII punctuation, lowercases
  English terms, and handles English possessives before stopword/stem steps.
- `REQ-ELA-008`: English stopword removal must have an explicit
  empty-after-stopwords fallback and must not accidentally route to generic
  overview/orientation behavior.
- `REQ-ELA-009`: Stemming must use a proven Snowball/Porter implementation;
  prefer the `snowballstemmer` package after license and notices are updated.
- `REQ-ELA-010`: Hangul and mixed CJK tokenization must keep current behavior.
- `REQ-ELA-011`: Numeric weighting, version-like tokens, code identifiers, and
  literal exact-substring mode must be covered by compatibility tests.
- `REQ-ELA-012`: Prior candidate experiments may remain recorded as internal
  decision evidence only, not shipped or supported runtime profiles.
- `REQ-ELA-013`: Stopword removal may change lexical evidence ranking, but
  orientation remains a separate context contract. Search indexing must not
  inject hidden synthetic terms based on page roles.
- `REQ-ELA-014`: For the `english` profile, a single-token query containing internal
  `.`, `_`, or `-` punctuation must require an exact authored compound match or
  exact metadata match. Split English components alone must not return results.
- `REQ-ELA-015`: Exact authored compound postings must be derived only from
  title, summary, body, and tags. They must avoid trailing sentence punctuation
  and must not include path or source-reference metadata.
- `REQ-ELA-016`: For the `english` profile, `source_refs` must be excluded from
  stemmed BM25 content. Path/source-reference matches are limited to exact
  metadata tokens and use a small non-BM25 channel.
- `REQ-ELA-017`: Managed-context tests that previously relied on stopword
  evidence must use the explicit contract: stopwords do not create unrelated
  evidence, and orientation remains separate from lexical evidence ranking.
- `REQ-ELA-018`: Final immutable Windows/DGX reports must be regenerated from
  a real release implementation before public evidence is claimed. As of this
  revision, the `git:8d04e8a46487827ee488a7ddab005aaab8dd885d` reports pass
  public validation; release steps remain pending.
- `REQ-ELA-019`: CLI and programmatic public report generation must require an
  explicit `analyzer_profile` and explicit `implementation_revision`.
- `REQ-ELA-020`: Public report validation must reject analyzer profiles outside
  `legacy|english` and must reject the all-zero placeholder implementation
  revision. Tests may use a nonzero deterministic fake git hash, but tracked
  final reports must use a real commit revision.
- `REQ-ELA-021`: Exact compound token extraction must use a bounded linear
  scanner rather than applying a backtracking regular expression to untrusted
  page text or query text.
- `REQ-ELA-022`: English raw-token extraction must use an explicit single-pass
  O(n) scanner over ASCII alphanumerics, Hangul syllables, and supported
  apostrophes. It must preserve existing token ordering, boundaries,
  possessive handling, and the downstream casefold, stopword, stemming, and
  mixed ASCII/Hangul expansion pipeline.

## Data Safety

Tracked docs and reports must not include private paths, endpoints, credentials,
provider settings, raw traces, raw benchmark text, or local run manifests.

## References

- BEIR repo: https://github.com/beir-cellar/beir
- BEIR dataset table: https://github.com/beir-cellar/beir/wiki/Datasets-available
- BEIR metrics: https://github.com/beir-cellar/beir/wiki/Metrics-available
- BEIR paper: https://datasets-benchmarks-proceedings.neurips.cc/paper/2021/file/65b9eea6e1cc6bb9f0cd2a47751a186f-Paper-round2.pdf
- Anserini SciFact flat BM25: https://github.com/castorini/anserini/blob/master/docs/reproduce/from-document-collection/beir-v1.0.0-scifact.flat.md
- SciFact repo: https://github.com/allenai/scifact
- SciFact license: https://github.com/allenai/scifact/blob/master/LICENSE.md
- SciFact paper: https://aclanthology.org/2020.emnlp-main.609/
- Snowball license: https://snowballstem.org/license.html
- snowballstemmer PyPI: https://pypi.org/project/snowballstemmer/
