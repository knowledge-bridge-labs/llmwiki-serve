# Tests: Benchmark Adapter Layer

## Acceptance Criteria

- `REQ-001`: Tests validate a complete `llmwiki-benchmark-bundle-v1` containing
  `corpus.jsonl`, `queries.jsonl`, `qrels.jsonl`, `evidence.jsonl`, and
  path-free `provenance.json`; tests also verify bundle JSONL is treated as
  local unless redistribution policy permits distribution.
- `REQ-002`: Tests require `corpus_id`, `text`, `title`, and `metadata` in
  corpus rows, and `query_id`, `query`, `answerability`, `label_source`,
  `answers`, `source_split`, `evaluation_split`, and `tags` in query rows.
  Tests also accept corpus-only normalization only when `queries.jsonl`,
  `qrels.jsonl`, and `evidence.jsonl` are all empty.
- `REQ-003`: Tests require qrels to map `query_id` to `corpus_id` with numeric
  `relevance`, and evidence rows to include `evidence_id`, `query_id`,
  `corpus_id`, `locator`, `required_group`, `hop_index`, `depends_on`, and
  `supports_claim_ids`.
- `REQ-004`: Tests reject duplicate ids, qrels that reference missing queries or
  corpus rows, evidence that references missing queries or corpus rows, and
  `depends_on` references outside the bundle, across queries, or in dependency
  cycles.
- `REQ-005`: Tests prove qrels alone do not create span, paragraph, section, or
  citation evidence. Evidence-set metrics are unavailable when explicit
  evidence labels are absent.
- `REQ-006`: Tests reject public provenance with local paths, private URLs,
  mutable source revisions, missing adapter version, missing checksums, missing
  component licenses, missing attribution, missing redistribution policy, or
  missing public-report policy.
- `REQ-007`: Tests allow cache/output paths only in local `run-manifest.json`
  and verify that the file is excluded from committed/public artifacts.
- `REQ-008`: Tests block CC-BY-NC, non-commercial, unknown-license, and
  unclear-license components from release automation and marketing reports by
  default.
- `REQ-009`: Adapter tests assert dataset roles: BEIR SciFact official test
  split is the first public recognized retrieval baseline, BRIGHT is the next
  reasoning-intensive retrieval benchmark, ALCE citation follows, RAGTruth is
  hallucination-only, CRAG is non-commercial answer-quality research,
  MuSiQue/2WikiMultiHopQA are multi-hop, curated unanswerable has a dedicated
  track, and local LLMWiki variants are compatibility inputs.
- `REQ-010`: Smoke tests use a deterministic stratified 50-query subset with a
  recorded seed, predeclared task-class labels, and no use of qrel identities,
  relevance counts, retrieved ranks, model outputs, or hand-selection after
  seeing outcomes. Tests and docs label this smoke as internal regression
  evidence only.
- `REQ-011`: Serve metric tests cover retrieval/projection metrics only. Bridge
  metric tests live in `llmwiki-agent-bridge` or consume exported fixtures
  without running model-backed synthesis here.
- `REQ-012`: Migration/report tests sanitize
  `llmwiki-serve-raw-vs-serve-benchmark-v3` output by removing absolute
  `wiki_root`, local paths, private query text, private URLs, credentials,
  provider endpoints, and raw traces.
- `REQ-013`: Release-gate tests compare against an accepted baseline before
  claiming pass/fail thresholds, macro Recall@5 regression,
  required-document-group coverage, citation precision, unsupported
  citation/claim, negative final-answer false-positive, canonical stability,
  token, source-call, or latency budgets.
- `REQ-014`: SciFact adapter tests validate the official BEIR `test` split
  materialization and, after actual data validation, require 5,183 corpus
  documents, 300 test queries, 339 qrels, and binary relevance.
- `REQ-015`: Public report tests require nDCG@10 and Recall@100 as primary
  BEIR-comparable metrics; Recall@5, Hit@5, and MRR@10 must be labeled
  product-secondary. Reports also include dataset/version/source URL, archive
  checksums, adapter/report schema versions, counts, package version, metric
  definitions, immutable implementation revision, environment class, index
  build time, latency p50/p95, and payload bytes p50/p95. CLI tests require
  `--implementation-revision git:<40 lowercase hex chars>` and reject mutable
  or malformed identities; programmatic fixture tests may use a deterministic
  synthetic revision.
- `REQ-016`: Cross-environment checks require Windows local and DGX Spark
  Ubuntu runs to reproduce source/artifact checksums and deterministic quality
  metrics exactly, while allowing latency differences.
- `REQ-017`: Public table tests distinguish BEIR paper BM25 (`0.665`
  nDCG@10, `0.908` Recall@100) from Pyserini/Anserini flat BM25 (`0.6789`
  nDCG@10, `0.9253` Recall@100) as external reference rows with exact source
  URLs and a status saying they were not run by `llmwiki-serve`. Tests also
  validate signed product-minus-reference deltas and reject tampered reference
  values, URLs, labels/status, or deltas. They assert that `llmwiki-serve`
  Markdown projection is labeled as an informal same-data comparison, not BEIR
  certification or an official leaderboard result.

## Unit Tests

- Test: schema validator accepts a minimal answerable bundle with one corpus
  row, one query row, one qrel row, explicit evidence, and path-free provenance.
- Test: schema validator accepts an unanswerable query with empty `answers`,
  empty qrels, and empty evidence.
- Test: schema validator accepts a corpus-only bundle only when query, qrel, and
  evidence files are all empty, yielding empty metric eligibility.
- Test: schema validator accepts `answerability: "unknown"` only when answers
  and metric eligibility follow the documented rules: excluded from
  answerability/abstention/FPR denominators but eligible for retrieval metrics
  only when positive-relevance qrels exist.
- Test: schema validator rejects missing required fields and invalid
  `answerability` values.
- Test: qrel validator rejects missing query or corpus references.
- Test: evidence validator rejects missing query/corpus references, invalid
  locators, invalid span bounds, invalid section/paragraph locator fields, and
  missing dependency evidence ids.
- Test: evidence validator rejects cross-query dependencies, self-dependencies,
  and dependency cycles.
- Test: evidence validator rejects `char_span.end` values beyond referenced
  corpus text, negative integer paragraph locators, and passage locators without
  a non-empty `passage` field.
- Test: evidence validator does not synthesize evidence rows from qrels.
- Test: provenance validator rejects local absolute paths, private URLs, mutable
  source revisions, unknown checksums, missing adapter version, and unsafe
  public-report policy.
- Test: provenance validator rejects common local Linux/DGX absolute paths such
  as `/mnt/...` and `/workspace/...` without treating ordinary URL path
  components as local filesystem paths.
- Test: provenance checksum validation uses canonical UTF-8 text bytes after
  optional BOM removal and CRLF/CR-to-LF normalization.
- Test: provenance validator rejects tag/release labels without a resolved
  immutable commit/content revision and prefers SPDX id, license URL, and
  verification date where available.
- Test: license guard blocks CC-BY-NC, non-commercial, unknown-license, and
  unclear-license components and license expressions containing NOASSERTION,
  UNKNOWN, UNCLEAR, or `LicenseRef-*` tokens from release/marketing mode.
- Test: run-manifest validator permits cache/output paths only for local,
  uncommitted run evidence and requires a repository root for placement safety.
- Test: SciFact local `run-manifest.json` records the same immutable
  `implementation_revision` used by the sanitized public report.
- Test: minimal local Markdown/LLMWiki corpus materializer emits a
  corpus-only bundle with deterministic corpus ids/order, path-free public
  provenance, empty query/qrel/evidence files, and no source tree mutation.

## Integration / Contract Tests

- Test: local LLMWiki/OpenWiki fixture emits bundle files without copying
  absolute paths, private query text, or local Markdown text into public
  provenance or reports.
- Test: BEIR SciFact-shaped fixture emits official-test-split retrieval qrels
  suitable for top-100 retrieval, nDCG@10, Recall@100, product-secondary
  Recall@5, Hit@5, and MRR@10 with cutoff.
- Test: BEIR SciFact full-run materialization validates 5,183 corpus documents,
  300 test queries, 339 qrels, binary relevance, official archive MD5, computed
  SHA-256, and path-free public provenance.
- Test: BRIGHT-shaped fixture emits retrieval qrels suitable for the next
  reasoning-intensive retrieval benchmark without being treated as the first
  public launch baseline.
- Test: ALCE-shaped fixture emits explicit citation evidence and claim support
  ids for bridge consumption.
- Test: MuSiQue or 2WikiMultiHopQA fixture emits `required_group`, `hop_index`,
  and `depends_on` values suitable for complete required-document-group
  coverage, with groups conjunctive and rows within one group treated as
  alternatives.
- Test: curated hard-negative/unanswerable fixture keeps retrieval negative
  exposure separate from final-answer false-positive rate.
- Test: `llmwiki-serve-benchmark-corpus-v1` normalization reads the existing
  Wiki-CS materialized-corpus manifest/schema plus materialized Markdown where
  applicable, emits corpus rows and path-free provenance, and leaves
  query/qrel/evidence files empty unless separate label sources are supplied.
- Test: `llmwiki-serve-raw-vs-serve-benchmark-v3` sanitizer emits a public
  aggregate report without `wiki_root`, local paths, private query text,
  private URLs, credentials, provider endpoints, or raw traces.

## Metric Tests

- Test: Hit@k / Success@k is the fraction of queries with at least one
  qrel-relevant corpus id in top k.
- Test: standard Recall@k is per-query retrieved relevant documents in top k
  divided by total relevant documents, macro averaged over eligible queries.
- Test: nDCG@10 and Recall@100 are emitted as primary BEIR-comparable metrics
  for the SciFact public table.
- Test: SciFact evaluation retrieves top 100 before computing Recall@100.
- Test: Recall@5, Hit@5, and MRR@10 are emitted only as product-secondary
  metrics in public reports.
- Test: no metric test fails release mode on arbitrary pass/fail thresholds
  before an accepted baseline exists.
- Test: SciFact public report validation recomputes signed deltas from product
  primary metrics to the fixed BEIR paper BM25 and Anserini/Pyserini flat BM25
  reference rows.
- Test: SciFact public report validation rejects tampering of fixed reference
  metric values, source URLs, status/labels, or product-minus-reference deltas.
- Test: MRR@k uses reciprocal rank of the first qrel-relevant result within top
  k.
- Test: nDCG@k uses numeric qrel relevance and normalizes by ideal DCG at k.
- Test: complete required-document-group coverage passes when every
  `required_group` has at least one retrieved evidence row, but does not claim
  a threshold before a baseline is accepted.
- Test: bridge exact evidence-span/claim coverage is measured after bridge
  reads and is separate from serve retrieval group coverage.
- Test: citation precision is recorded where bridge labels exist but does not
  claim a threshold before a baseline is accepted.
- Test: unsupported citation/claim counts are recorded where bridge labels
  exist but do not claim a gate before a baseline is accepted.
- Test: negative final-answer false-positive rate is recorded for
  hard-negative or unanswerable queries but does not claim a gate before a
  baseline is accepted.
- Test: known, multi-hop, and negative canonical class stability can be
  recorded over repeat runs but does not claim a gate before a baseline is
  accepted.
- Test: token, source-call, and latency budgets are recorded but cannot be
  claimed until a baseline is set.

## E2E / Smoke Tests

- Manual check: materialize synthetic fixtures and run validators without any
  network calls.
- Manual check: materialize a deterministic stratified 50-query internal smoke
  subset with a recorded seed and no qrel-aware sampling.
- Manual check: run serve retrieval metrics on the internal smoke subset and
  keep the report out of public benchmark claims.
- Manual check: materialize and run the full BEIR SciFact official test split
  on Windows local and DGX Spark Ubuntu.
- Manual check: verify full SciFact reports include primary nDCG@10 and
  Recall@100, product-secondary Recall@5/Hit@5/MRR@10, index build time,
  latency p50/p95, payload bytes p50/p95, and no arbitrary pass/fail threshold.
- Manual check: export bridge input artifacts from the same smoke subset
  without invoking model-backed synthesis in this repo.
- Manual check: verify public reports include source, resolved source/content
  revision, adapter version, artifact checksums, component licenses,
  attribution, redistribution policy, public-report policy, metric definitions,
  package version, environment class, corpus/query/qrel counts, and whether
  bridge metrics were run.

## Manual Checks

- Check: `git status --short` after a run shows only intended schema/spec/report
  files, never downloaded archives, generated corpora, local run manifests, or
  local cache/output directories.
- Check: public reports contain no absolute `wiki_root`, local paths, private
  query text, private URLs, credentials, provider endpoints, raw traces, or raw
  local Markdown text.
- Check: normalized bundle JSONL is not treated as public-safe unless
  component licenses, redistribution policy, and public-report policy permit a
  distributable bundle.
- Check: retrieval negative exposure is reported separately from final-answer
  false-positive rate.
- Check: BEIR paper BM25 and Pyserini/Anserini flat BM25 references are
  presented as separate baselines.
- Check: `llmwiki-serve` Markdown projection results are labeled as informal
  same-data comparison, not BEIR certification or an official leaderboard
  result.
- Check: CC-BY-NC, non-commercial, unknown-license, and unclear-license
  datasets are marked local research only unless a separate release/legal policy
  permits the specific public use.

## Skipped Or Deferred

- Item: Runtime API, MCP, A2A-style, OpenAPI, or source adapter changes.
  Reason: benchmark adapters are offline evaluation tooling.

- Item: Model-backed bridge synthesis runs.
  Reason: bridge answerability/citation evaluation belongs in
  `llmwiki-agent-bridge`.

- Item: Full public benchmark download in CI.
  Reason: CI must remain legally safe, deterministic, and lightweight.

- Item: RAGTruth, CRAG, other BEIR subsets, MIRACL, LoTTE, and broader public
  adapters.
  Reason: launch order is schema/provenance, local LLMWiki/OpenWiki,
  SciFact-first public baseline, BRIGHT, ALCE, one multi-hop adapter, curated
  unanswerable data, and metrics/export first.

## Validation Commands

Spec-only validation:

```powershell
git diff --check -- specs/benchmark-adapter-layer/spec.md specs/benchmark-adapter-layer/plan.md specs/benchmark-adapter-layer/tasks.md specs/benchmark-adapter-layer/tests.md docs/decisions/2026-07-31-cross-repo-benchmark-artifact-contract.md
```

Future implementation validation:

```powershell
uv run pytest -q tests/test_benchmark_adapter_layer.py
uv run ruff check scripts tests
uv run ruff format --check scripts tests
```
