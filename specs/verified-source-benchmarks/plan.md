# Plan: Verified Source Benchmarks

## Approach

Add a benchmark layer beside the existing smoke harnesses. Keep the current
release smoke and upstream smoke as compatibility gates, then introduce a
separate quality benchmark runner and report format that consumes public-safe
corpus, qrels, query, and run files.

The first implementation should produce reports that can support docs tables
without overstating the evidence. A compatibility row may say that a pinned
public source shape projects cleanly. A quality row may say that a judged corpus
and query set met recall, citation, token, and latency thresholds.

## Workstreams

### 1. Compatibility Inventory

- Extend the upstream smoke metadata model to record official link, source kind,
  license evidence, and evidence type.
- Keep the current 10 actual pinned upstream rows as the initial base.
- Keep the actual pinned `langchain-ai/openwiki` static-output case separate
  from synthetic OpenWiki-style fixtures and provider-backed generation.
- Keep the 11 generated candidate samples in a separate synthetic table.
- Update architecture/docs only after smoke rows are rerun and license evidence
  is verified.

### 2. Deterministic Quality Harness

- Add public-safe `corpus.jsonl`, `queries.jsonl`, `qrels.jsonl`, `runs.jsonl`,
  and `report.json` schemas.
- Reuse existing raw-vs-serve benchmark ideas, but replace byte/4 token proxy
  with Qwen tokenizer counts.
- Compare raw and served modes on the same page ids and qrels.
- Measure recall@5, hit@5, MRR, nDCG@10, citation precision/recall, context tokens,
  payload bytes, and p50/p95 latency.
- Track cold, warm strict, warm interval, and Redis projection-store modes
  separately when available.

### 3. Public Corpus Selection

- Use generated candidate samples for contract regression, not public quality
  claims.
- Use actual pinned public products for compatibility evidence.
- Use a judged public corpus for quality evidence. Wiki-CS materialized to
  Markdown remains the current best starting corpus for graph-shaped scale, but
  it needs qrels suitable for retrieval rather than only answerable smoke.
- Add plain Markdown folders without native `hot.md`/`index.md` and native
  LLMWiki folders with `hot.md`/`index.md` as paired comparison cases.

### 4. DGX vLLM Qwen Agent Tier

- Run only after deterministic retrieval gates pass.
- Use DGX Spark/Ubuntu with the user's vLLM-served Qwen endpoint.
- Record model id, tokenizer id, vLLM version, prompt/tool schema revision,
  source bundle ids, seeds, and run counts.
- Report task success, citation support, unsupported claims, calls, input and
  output tokens, total tokens, wall time, and variance.

### 5. Public Reporting

- Produce two public tables:
  - compatibility table: product, official link, commit, license, adapter,
    pages, graph, evidence type
  - quality table: corpus, query count, recall@5, hit@5, MRR, nDCG@10,
    citation P/R, context-token p95, latency p95, agent success where available
- Add hard-fail status separately from metric averages.
- Include confidence intervals for public metric deltas.

## Affected Areas

- Future scripts:
  - `scripts/upstream_candidate_smoke.py`
  - a new or extended deterministic benchmark runner
  - optional agent benchmark runner under a public-safe script name
- Future tests:
  - smoke metadata schema tests
  - qrels and run-format tests
  - metric computation tests
  - redaction/data-safety tests
- Future docs:
  - `docs/architecture.md`
  - `docs/release.md`
  - public docs repository compatibility and benchmark pages

This spec slice only creates planning documents and does not change runtime
contracts.

## Risks

- Risk: synthetic and actual evidence are mixed in public tables.
  Mitigation: require `source_kind` and `evidence_type` fields and hard-fail
  mixed rows.

- Risk: public benchmark claims are based on too few queries.
  Mitigation: require minimum query counts and bootstrap confidence intervals.

- Risk: token counts are model-mismatched.
  Mitigation: record the exact Qwen tokenizer id/revision and use it for both
  deterministic and agent-tier reports.

- Risk: benchmark artifacts leak private paths or source content.
  Mitigation: add report redaction checks and permit only public links,
  source-root-relative paths, hashes, and aggregate metrics.

- Risk: vLLM agent runs are noisy.
  Mitigation: temperature `0`, fixed seed, repeated runs, and variance reporting.

- Risk: quality benchmark optimizes for native `hot.md`/`index.md` sources and
  hides plain Markdown weaknesses.
  Mitigation: include paired native-hub and plain-Markdown corpora and report
  calls/tokens/recall separately.

## Rollout

1. Land this spec.
2. Add schema and metric unit tests before implementing benchmark collection.
3. Add compatibility metadata fields and verify the existing 10 pinned upstream
   rows still pass.
4. Rerun the actual pinned OpenWiki smoke case and keep it separate from
   synthetic OpenWiki-style results.
5. Add deterministic quality runner and run a small smoke report on Windows.
6. Run public-minimum deterministic quality reports on Windows and DGX Spark.
7. Run the vLLM Qwen agent tier on DGX.
8. Publish docs only after hard-fail checks and reasonable thresholds pass.
