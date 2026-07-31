# Tasks: Benchmark Adapter Layer

- [x] Replace the unimplemented single mixed JSONL proposal with
  `llmwiki-benchmark-bundle-v1`.
- [x] Define `corpus.jsonl`, `queries.jsonl`, `qrels.jsonl`, `evidence.jsonl`,
  and `provenance.json` as the normalized bundle components.
- [x] Define optional local `run-manifest.json` and mark it never-committed.
- [x] Define required fields and referential integrity rules for bundle rows.
- [x] State that span, section, paragraph, and citation evidence must never be
  inferred from document qrels.
- [x] Require path-free public provenance with immutable revision, adapter
  version, checksums, component licenses, attribution, redistribution policy,
  and public-report policy.
- [x] Block CC-BY-NC, non-commercial, unknown-license, and unclear-license data
  from release automation or marketing use by simple maintainer approval.
- [x] Correct dataset roles for BEIR SciFact, BRIGHT, ALCE, RAGTruth, CRAG,
  MuSiQue, 2WikiMultiHopQA, curated unanswerable data, and local LLMWiki
  variants.
- [x] Add cross-repo ADR for the bundle contract consumed by
  `llmwiki-agent-bridge`.
- [x] Define migration requirements for `llmwiki-serve-benchmark-corpus-v1` and
  `llmwiki-serve-raw-vs-serve-benchmark-v3`.
- [x] Correct `llmwiki-serve-benchmark-corpus-v1` as the existing Wiki-CS
  materialized-corpus manifest/schema, not a mixed query/answer record.
- [x] Define local-vs-distributable bundle policy; only provenance and
  sanitized aggregate reports are public-safe by default.
- [x] Distinguish BEIR-comparable primary metrics from product-secondary
  diagnostics: nDCG@10 and Recall@100 are primary for the public SciFact table;
  Recall@5, Hit@5, and MRR@10 are product-secondary.
- [x] Define `required_group` as conjunctive across groups with alternatives
  within each group.
- [x] Define `answerability: "unknown"` metric exclusions and split
  `source_split` from `evaluation_split`.
- [x] Require resolved immutable source/content revisions, optional
  `source_release`, and SPDX/license URL/verification metadata where available.
- [x] Clarify 50-query smoke stratification uses predeclared task-class labels,
  not qrel identities, relevance counts, ranks, model outputs, or outcomes.
- [x] Define canonical repeat gates for known, multi-hop, and negative classes.
- [x] Require sanitized public reports without absolute `wiki_root`, local
  paths, private query text, private URLs, credentials, provider endpoints, or
  raw traces.
- [x] Define calibration, holdout, deterministic stratified 50-query internal
  smoke, no qrel-aware sampling, and run-hash recording requirements.
- [x] Define metric definitions and baseline policy without arbitrary
  pre-baseline pass/fail thresholds.
- [x] Record the launch-priority change: first public recognized retrieval
  baseline is BEIR SciFact full official test split with all official test
  queries, official qrels, top-100 retrieval, and sanitized aggregate reporting.
- [x] Record expected/canonical SciFact launch invariants to be enforced after
  actual data confirmation: 5,183 corpus documents, 300 test queries, 339
  qrels, and binary relevance.
- [x] Require the public table to distinguish BEIR paper BM25 from
  Pyserini/Anserini flat BM25, and to label `llmwiki-serve` Markdown projection
  as an informal same-data comparison rather than BEIR certification.
- [x] Confirm this step has no runtime code change.
- [x] Run scoped trailing-whitespace validation with `git diff --check` on the
  owned files.

## Completed Implementation Tasks

- [x] Add schema and provenance validators for all normalized bundle
  components.
- [x] Add tiny synthetic fixtures for valid and invalid bundles.
- [x] Add referential integrity tests for qrels, evidence, dependency, and claim
  id handling.
- [x] Add public provenance tests that reject local paths, private URLs, missing
  immutable revisions, missing checksums, missing attribution, and unsafe
  public-report policy.
- [x] Add local-only run manifest tests that allow cache/output paths only in
  uncommitted run artifacts.
- [x] Add a minimal public-provenance local Markdown/LLMWiki corpus-only
  materializer that emits valid empty query/qrel/evidence files.

## Launch Implementation Tasks

- [x] Add or finish the BEIR SciFact adapter for official BEIR input files:
  `corpus.jsonl`, `queries.jsonl`, and `qrels/test.tsv`.
- [x] Add a safe SciFact acquisition helper that downloads only from the
  official BEIR archive URL, verifies MD5
  `5f7d1de60b170fc8027bb7898e2efca1`, computes archive SHA-256, and prevents
  zip-slip or symlink extraction.
- [x] Add SciFact adapter validation for 5,183 corpus documents, 300 official
  test queries, 339 qrels, and binary relevance after actual data validation.
- [x] Add public provenance that preserves BEIR, SciFact, and S2ORC component
  licenses and attribution without committing archive/data text.
- [x] Add SciFact retrieval metric runner using top-100 retrieval with primary
  nDCG@10 and Recall@100.
- [x] Add product-secondary SciFact metrics: macro Recall@5, Hit@5, and
  MRR@10 with cutoff.
- [x] Add index build time, search latency p50/p95, and payload bytes p50/p95
  reporting by environment class.
- [x] Add sanitized aggregate report export with dataset/version/source URL,
  archive checksums, adapter/report schema versions, corpus/query/qrel counts,
  package version, metric definitions, environment class, limitations, and no
  raw query/doc text or private path/host data.
- [x] Add public table fields for BEIR paper BM25 (`0.665` nDCG@10, `0.908`
  Recall@100) and Pyserini/Anserini flat BM25 (`0.6789` nDCG@10, `0.9253`
  Recall@100), clearly separated from `llmwiki-serve` Markdown projection
  results.
- [x] Require `git:<40 lowercase hex chars>` implementation revision for the
  public SciFact runner CLI, store it in the public report and local
  `run-manifest.json`, and keep deterministic synthetic revision defaults for
  programmatic tests.
- [x] Validate external reference source URLs, fixed values, labels/status, and
  signed product-minus-reference deltas in public report validation.
- [ ] Run final full SciFact baselines on Windows local and DGX Spark Ubuntu
  from a real immutable implementation revision, requiring identical
  source/artifact checksums and deterministic quality metrics while reporting
  latency separately.
- [ ] Publish measured numbers with limitations only after the baseline is
  accepted; do not publish arbitrary pass/fail thresholds before then.

## MVP Follow-Up Tasks

- [ ] Add local LLMWiki/OpenWiki adapter fixture.
- [ ] Add BRIGHT retrieval fixture adapter as the next reasoning-intensive
  retrieval benchmark after SciFact.
- [ ] Add ALCE citation fixture adapter.
- [ ] Add either MuSiQue or 2WikiMultiHopQA multi-hop fixture adapter.
- [ ] Add independently curated hard-negative/unanswerable fixture track.
- [ ] Add compatibility normalizer for the existing
  `llmwiki-serve-benchmark-corpus-v1` Wiki-CS materialized-corpus
  manifest/schema.
- [ ] Add sanitizer/export path for `llmwiki-serve-raw-vs-serve-benchmark-v3`.
- [ ] Add retrieval metric runner for Hit@k, standard macro Recall@k, MRR@k,
  nDCG@k, complete required-document-group coverage@k, and retrieval negative
  exposure.
- [ ] Add sanitized aggregate export with baseline comparison and metric
  definitions.
- [ ] Add bridge-input artifact export for model-backed metrics in
  `llmwiki-agent-bridge`.
- [ ] Establish baseline reports before claiming token, source-call, latency,
  or release-gate budgets.

## Deferred Tasks

- [ ] Add RAGTruth hallucination-only diagnostic adapter.
- [ ] Add CRAG non-commercial answer-quality adapter for local research only.
- [ ] Add other BEIR subsets, MIRACL, LoTTE, and broader public retrieval
  adapters after the SciFact baseline path is validated.
- [ ] Update benchmark research notes and public docs after validated local
  runs.
- [ ] Mark files that should be ingested into project LLMWiki.

## Current Step Rationale

The implemented local state includes compact offline validation tooling,
synthetic fixture tests, a minimal corpus-only local Markdown/LLMWiki
materializer, and the official SciFact acquisition/materialization/runner/report
path. Runtime endpoint contracts, generated public corpora, legacy
migration/sanitizer work, broad case adapters, final immutable-revision public
reports, and model-backed bridge execution remain intentionally outside the
accepted release evidence until their pending tasks are complete.
