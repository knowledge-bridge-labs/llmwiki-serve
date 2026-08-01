# Tests: English Lexical Analyzer Opt-In

## Acceptance

- The product default remains `legacy` when no analyzer profile is provided.
- `serve`, `query`, and `search` accept only public
  `--analyzer-profile legacy|english` values.
- Python `create_app` and `LlmWikiService` accept only the same public
  `legacy|english` values and do not keep a broader internal allowance.
- Evaluated experimental candidates `english_additive` and `english_flatlike`
  are not accepted by release runtime, public CLI, or public Python API
  surfaces.
- HTTP and MCP request schemas stay unchanged; analyzer profile is not a
  per-request field.
- CLI and programmatic public report generation require explicit
  `analyzer_profile` and explicit `implementation_revision`.
- Public report validation rejects analyzer profiles outside `legacy|english`
  and rejects the all-zero implementation-revision placeholder.
- Tests may use a nonzero deterministic fake git hash for
  `implementation_revision`, but tracked final reports must use a real commit.
- Public SciFact reports explicitly select `english`, use the official BEIR
  SciFact materialization (`5,183` docs, `300` test queries, `339` qrels), and
  are labeled English opt-in same-data comparisons.
- The decision docs keep the reproducible `legacy` Windows/DGX metrics as a
  separate baseline; one English opt-in public report is not required to include
  a legacy result row.
- SciFact report validation records the `english` opt-in metrics: nDCG@10
  `0.6905159872`, Recall@100 `0.9286666667`, Recall@5 `0.7459444444`, Hit@5
  `0.7666666667`, MRR@10 `0.656265873`.
- Published BEIR BM25 and Anserini/Pyserini flat BM25 rows are contextual
  same-data references only, not certification claims.
- OpenWiki citation fix validation remains recorded as succeeded.
- OpenWiki default-switch compatibility remains failed until generic-shadow
  regressions are resolved. Current remaining regressions are global-map
  Recall@5 `-0.08`, citation recall `-0.029412`, and known-item MRR
  `-0.051282`.
- Korean, mixed CJK, code identifier, version-token, numeric weighting, and
  literal-mode cases do not materially regress.
- Single-token compound identifier/version queries require an exact authored or
  exact metadata match; split English components alone do not return results.
- Exact compound extraction handles long adversarial page/query text with
  bounded output and without regex backtracking risk.
- English token extraction preserves existing ASCII/Hangul, underscore,
  apostrophe, Unicode-boundary, ordering, casefold, stopword, and stemming
  behavior through an explicit linear scanner.
- Repeated-zero and long-separator adversarial inputs complete with bounded
  behavior and preserve the prior token sequence.
- Source-reference metadata is not stemmed into English BM25 content, while an
  exact source-reference or path token can still retrieve the owning page.
- Empty-after-stopwords queries use an explicit fallback and do not become
  generic overview queries accidentally.
- Stopwords do not make unrelated managed-context evidence answerable or boost
  nonmatching read-prior pages.
- Hybrid/fusion ranking is not implemented or accepted by this spec.
- Stale reports have been replaced by final immutable Windows/DGX reports from
  `git:0f38fcbdf0c5a90c07a5f23e057df48e0bc3ef08`.
- Final immutable Windows/DGX reports pass public report validation; release
  steps remain pending.
- Linux and Windows CI jobs pass at the report revision. Local scanner security
  regressions pass; the PR's separate CodeQL security result is pending rerun.

## Validation Commands

Focused implementation validation for this opt-in change:

- `uv run pytest tests/test_english_lexical_analyzer.py tests/test_search_postings.py tests/test_service.py tests/test_managed_context.py tests/test_beir_scifact.py`
- `uv run ruff format --check src/llmwiki_serve/search.py src/llmwiki_serve/service.py tests/test_english_lexical_analyzer.py tests/test_search_postings.py tests/test_service.py tests/test_managed_context.py tests/test_beir_scifact.py`
- `uv run ruff check src/llmwiki_serve/search.py src/llmwiki_serve/service.py tests/test_english_lexical_analyzer.py tests/test_search_postings.py tests/test_service.py tests/test_managed_context.py tests/test_beir_scifact.py`
- `uv run mypy src/llmwiki_serve/search.py src/llmwiki_serve/service.py tests/test_english_lexical_analyzer.py tests/test_search_postings.py tests/test_service.py tests/test_managed_context.py tests/test_beir_scifact.py`

Focused implementation tests and checks have passed for the `legacy|english`
runtime boundary, opt-in CLI/Python wiring, unchanged HTTP/MCP request schemas,
and public report generation/validation hardening. Final Windows/DGX sanitized
public reports now pass public report validation. Release validation still
requires the full suite, merge, hosted docs deployment, and package release
steps.
