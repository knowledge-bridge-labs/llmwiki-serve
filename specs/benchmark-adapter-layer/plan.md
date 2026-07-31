# Plan: Benchmark Adapter Layer

## Approach

Build the benchmark adapter layer as offline tooling around a versioned
`llmwiki-benchmark-bundle-v1` artifact. Runtime service code remains untouched.

The first implementation should add schema/provenance validation and tiny
synthetic fixtures before any real downloader is added. Dataset-specific
adapters translate pinned external formats into bundle components:
`corpus.jsonl`, `queries.jsonl`, `qrels.jsonl`, `evidence.jsonl`, and
path-free `provenance.json`. The JSONL components may contain dataset text and
stay local unless redistribution policy permits. Optional `run-manifest.json`
records local paths and run details but is never committed.

Metric ownership remains explicit:

- `llmwiki-serve` owns bundle schema, validators, offline adapters, migration
  from the existing Wiki-CS materialized-corpus manifest/schema, retrieval and
  projection metrics, sanitized report export, and path-free provenance.
- `llmwiki-agent-bridge` consumes bundles read-only and owns model-backed
  answerability, citation precision, unsupported-claim, abstention, token,
  source-call, latency, version negotiation, and bridge migration behavior.

Launch priority is BEIR SciFact full official test split first. The public
baseline uses all official test queries and qrels from the official BEIR
`scifact.zip` source with top-100 retrieval. Public BEIR-comparable primary
metrics are nDCG@10 and Recall@100. Recall@5, Hit@5, and MRR@10 remain
product-secondary diagnostics. BRIGHT remains the next reasoning-intensive
retrieval benchmark, and ALCE/bridge citation evaluation follows. The 50-query
smoke set is internal regression evidence only.

## Affected Areas

- Source module: no runtime service contract change.
- Scripts: offline `scripts/benchmark_adapters/` tooling for acquisition,
  materialization, validation, and retrieval reports.
- Tests: fixture-only schema, provenance, adapter, migration, metrics, and
  data-safety tests.
- Docs: this spec set and the cross-repo ADR now; benchmark research notes and
  public docs after final immutable-revision validated runs.
- Contracts: no HTTP, MCP, MCP Streamable HTTP, A2A-style, OpenAPI, or runtime
  source adapter changes.
- ADR: required for the cross-repo benchmark artifact contract.

## Launch Implementation Order

1. Schema/provenance validators and synthetic fixtures
   - Validate all five normalized bundle files.
   - Validate referential integrity across queries, corpus, qrels, and
     evidence.
   - Reject path-bearing provenance and unsafe public reports.
   - Ensure `run-manifest.json` is local-only and ignored by git.

2. Minimal local LLMWiki/OpenWiki materializer
   - Phase 2 starts with a minimal corpus-only materializer for
     operator-supplied public Markdown/LLMWiki roots so public retrieval
     benchmarks can reuse the normalized corpus contract.
   - Materialize operator-provided local roots into temporary benchmark
     artifacts without committing corpus text or paths.
   - Leave query, qrel, answer, evidence, and local-private policy
     materialization to later adapter work.
   - Preserve compatibility with local LLMWiki variants, OpenWiki-like output,
     compiler output, and Obsidian-like source shapes.

3. BEIR SciFact official full test baseline
   - Acquire the official BEIR SciFact archive from
     `https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip`.
   - Verify the official MD5 `5f7d1de60b170fc8027bb7898e2efca1` and compute
     archive SHA-256 for provenance.
   - Materialize the official `test` split with all official test queries and
     official qrels, and run top-100 retrieval.
   - Treat BEIR's documented 300 test queries and approximately 5K corpus
     documents as scale checks. After actual data validation, enforce 5,183
     corpus documents, 300 test queries, 339 qrels, and binary relevance as
     launch invariants.
   - Preserve SciFact component licenses and attribution: claims/evidence
     annotations under CC-BY-4.0, corpus abstracts under ODC-By-1.0 via S2ORC,
     and BEIR format/code under Apache-2.0.

4. SciFact retrieval runner and sanitized report export
   - Implement nDCG@10 and Recall@100 as the public BEIR-comparable primary
     metrics, using top-100 retrieval.
   - Implement product-secondary macro Recall@5, Hit@5, and MRR@10 with
     cutoff, plus index build time, search latency p50/p95, and payload bytes
     p50/p95.
   - Export a sanitized aggregate report with dataset/version/source URL,
     archive checksums, adapter/report schema versions, corpus/query/qrel
     counts, package version, metric definitions, environment class, and
     limitations.
   - Require a public-safe immutable implementation revision in the form
     `git:<40 lowercase hex chars>` for public CLI report generation, and
     store the same identity in local `run-manifest.json`.
   - Distinguish the BEIR paper BM25 reference (`0.665` nDCG@10, `0.908`
     Recall@100, source `https://arxiv.org/abs/2104.08663`) from the
     Pyserini/Anserini flat BM25 reference (`0.6789` nDCG@10, `0.9253`
     Recall@100, source
     `https://github.com/castorini/anserini/blob/master/docs/reproduce/from-document-collection/beir-v1.0.0-scifact.flat.md`)
     as fixed external reference rows that were not run by `llmwiki-serve`.
   - Compute signed product-minus-reference deltas for nDCG@10 and Recall@100
     and reject public reports whose reference values, URLs, status labels, or
     deltas are tampered.
   - Label the `llmwiki-serve` Markdown projection result as an informal
     reproducible same-data comparison, not BEIR certification or an official
     leaderboard result.
   - Exclude raw query text, raw document text, private paths, private hosts,
     provider endpoints, credentials, and local run manifests.
   - Reproduce source/artifact checksums and deterministic quality metrics on
     Windows local and DGX Spark Ubuntu. Report latency separately by
     environment class.
   - Publish measured numbers without arbitrary pass/fail thresholds. Later
     regression gates compare against the accepted baseline.

5. BRIGHT retrieval adapter
   - Pin resolved immutable source/content revisions.
   - Emit retrieval qrels without evidence-span inference.
   - Treat BRIGHT as the next reasoning-intensive retrieval benchmark after the
     SciFact launch baseline.

6. ALCE citation adapter
   - Emit explicit citation evidence labels for bridge consumption.
   - Keep serve metrics limited to retrieval/evidence coverage.

7. One multi-hop adapter plus curated unanswerable set
   - Add MuSiQue or 2WikiMultiHopQA first.
   - Preserve `required_group`, `hop_index`, and `depends_on`.
   - Add a dedicated hard-negative/unanswerable track curated without
     qrel-aware ranking or sampling leakage.

8. Broader metrics and export
   - Implement Hit@k, standard macro Recall@k, MRR@k, nDCG@k, complete
     required-document-group coverage@k, and retrieval negative exposure
     separately from bridge final-answer metrics.
   - Export sanitized aggregate reports with metric definitions, hashes,
     environment class, license/provenance status, and baseline comparison.

9. Broader adapters later
   - Defer RAGTruth hallucination-only diagnostics, CRAG non-commercial
     answer-quality research, other BEIR subsets, MIRACL, LoTTE, and other
     public adapters until the SciFact baseline path and reporting gates are
     validated.

## Migration Plan

- Add a compatibility normalizer for `llmwiki-serve-benchmark-corpus-v1`, the
  existing Wiki-CS materialized-corpus manifest/schema. It reads the manifest
  plus materialized Markdown where applicable, emits corpus rows and path-free
  provenance, and leaves query/qrel/evidence files empty unless separate label
  sources are supplied.
- Add a sanitizer for `llmwiki-serve-raw-vs-serve-benchmark-v3` reports that
  removes absolute `wiki_root`, local paths, private query text, private URLs,
  credentials, provider endpoints, and raw traces.
- Mark evidence metrics unavailable when legacy artifacts lack explicit evidence
  labels.
- Record migration source id, adapter version, artifact checksum, and migration
  checksum in `provenance.json` and local run details in `run-manifest.json`.

## Evaluation Plan

- Use calibration splits for adapter debugging and threshold tuning.
- Use holdout splits for release and marketing claims.
- Use BEIR SciFact full official test split for the first public recognized
  retrieval baseline. The report uses all official test queries and qrels with
  top-100 retrieval, not a sampled smoke subset.
- Treat nDCG@10 and Recall@100 as the primary BEIR-comparable metrics.
  Recall@5, Hit@5, and MRR@10 are product-secondary diagnostics.
- Build the internal 50-query smoke set deterministically with a recorded seed and
  predeclared task-class stratification labels. Do not sample using qrel
  identities, relevance counts, retrieved ranks, model outputs, or
  hand-selection after seeing outcomes. Do not market this smoke as recognized
  benchmark evidence.
- Record adapter hash, source hash/revision, artifact checksums, environment,
  and bridge provider/model/prompt parameters when bridge runs are executed.
- Establish measured baselines before claiming token, source-call, latency,
  pass/fail, or regression budgets.

## Risks

- Risk: Dataset licenses differ by subset or upstream component.
  Mitigation: Store per-component license metadata and block unknown,
  CC-BY-NC, non-commercial, or unclear components from release automation and
  marketing tables unless a separate release/legal policy permits the specific
  use.

- Risk: Downloaders accidentally write large or restricted data into the repo.
  Mitigation: Require explicit cache/output paths, keep `run-manifest.json`
  local-only, add gitignore/data-safety checks, and keep CI fixture-only.

- Risk: Retrieval metrics and model-backed answer quality become mixed.
  Mitigation: Use separate metric namespaces, reports, and ownership boundaries
  for serve and bridge.

- Risk: Qrels are mistaken for evidence spans.
  Mitigation: Require explicit `evidence.jsonl` labels and mark evidence-set
  metrics unavailable when span/section/paragraph evidence is absent.

- Risk: Local LLMWiki variant metrics expose private content.
  Mitigation: Default to aggregate-only reports, sanitize legacy raw-vs-serve
  output, and block raw query/path/text export from public reports.

## Rollout

- Local validation: run schema/provenance validators and synthetic fixtures.
- CI validation: run fixture-only tests with no network calls and no downloaded
  benchmark text.
- Internal smoke: run deterministic stratified 50-query smoke locally after
  adapters exist, but keep it as regression evidence only.
- Public baseline: materialize and run BEIR SciFact full official test split on
  Windows local and DGX Spark Ubuntu, then publish a sanitized aggregate report
  only after checksums and deterministic quality metrics match. The public
  table must include the BEIR paper BM25 and Pyserini/Anserini flat BM25
  references separately, signed product-minus-reference deltas, a strict
  implementation revision, and a statement that the Markdown projection is an
  informal same-data comparison.
- Baseline policy: create accepted baseline reports before enforcing release
  gates or using pass/fail language.
- Docs / LLMWiki ingestion: ingest this spec and ADR after review; ingest later
  benchmark reports only after data-safety review.
