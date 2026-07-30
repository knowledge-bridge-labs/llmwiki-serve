# Tasks: Verified Source Benchmarks

- [x] Create this benchmark spec set.
- [x] Separate product compatibility smoke from quality benchmark gates.
- [x] Capture the existing 10 actual pinned upstream smoke cases as the initial
  actual-source inventory.
- [x] Capture the existing 11 generated candidate samples as synthetic shape
  evidence.
- [x] Keep actual pinned OpenWiki static-output coverage separate
  from synthetic OpenWiki-style fixtures.
- [x] Define public-safe corpus, queries, qrels, runs, and report formats.
- [x] Define deterministic retrieval metrics and Qwen tokenizer token counting.
- [x] Define DGX vLLM Qwen agent-tier metrics.
- [x] Define Windows and DGX hardware buckets.
- [x] Define run counts, bootstrap confidence intervals, hard failures, and
  reasonable thresholds.
- [x] Implement compatibility metadata fields in the upstream smoke harness.
- [x] Verify license evidence for each public product row from upstream
  metadata and record SPDX ids or `needs-review`.
- [x] Port/add the actual pinned OpenWiki static-output smoke case.
- [x] Add metric computation tests for recall@5, hit@5, MRR, nDCG@10, citation
  precision/recall, context tokens, payload bytes, and latency aggregation.
- [x] Add qrels/run/report schema validation tests.
- [x] Add redaction tests for benchmark reports.
- [x] Add checked-in manifest regression tests for canonical deterministic
  public path-ID citation mode.
- [x] Add explicit `global-map` query class support and regression coverage.
- [x] Add portable canonical Markdown corpus hashing and regression coverage
  for LF/CRLF equivalence, OpenWiki git blob compatibility, and real content
  changes.
- [x] Add portable canonical benchmark artifact hashing and LF-only checked-in
  artifact regression coverage for verified-source case JSON, JSONL, and
  Markdown files.
- [x] Run deterministic quality smoke on Windows.
- [ ] Run deterministic quality benchmark on DGX Spark/Ubuntu.
- [ ] Run vLLM Qwen agent-tier smoke on DGX.
- [ ] Publish docs tables only after hard-fail checks and reasonable thresholds
  pass.

## Deterministic Harness Slice

- Added `scripts/verified_source_benchmark.py` as an independent JSONL/JSON
  validation, metric, redaction, digest, and report runner.
- Added tiny public-safe fixture data under
  `tests/fixtures/verified_source_benchmarks/tiny/`.
- Added `tests/test_verified_source_benchmarks.py` for schema validation,
  standard recall@5 vs hit@5 metric computation, Qwen tokenizer provenance
  enforcement, redaction hard-fails, source mutation digest checks, hardware
  bucket validation, CLI reproducibility, and seeded paired bootstrap confidence
  intervals.

## Validation For This Implementation Slice

- Review that code changes are scoped to `scripts/upstream_candidate_smoke.py`,
  `tests/test_upstream_candidate_smoke.py`, and this spec set.
- Review that reports, specs, and test fixtures contain no private local paths,
  private endpoints, credentials, or raw private content.
- Run `uv run pytest -q tests/test_upstream_candidate_smoke.py`.
- Run `uv run python scripts/upstream_candidate_smoke.py --dry-run --case openwiki`.
- Optionally run the network smoke:
  `uv run python scripts/upstream_candidate_smoke.py --case openwiki --timeout 180`.

## Future LLMWiki Ingestion Candidates

- `specs/verified-source-benchmarks/`
- Future benchmark reports after they pass data-safety review
- Future architecture/docs updates after compatibility and quality evidence is
  actually collected
