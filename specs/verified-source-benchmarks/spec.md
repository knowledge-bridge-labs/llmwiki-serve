# Spec: Verified Source Benchmarks

## Status

Draft.

## Problem

`llmwiki-serve` has useful compatibility probes for local generated shapes and
pinned public upstream snapshots. Those probes are intentionally smoke tests:
they prove that a source folder can be projected, queried, searched, read, and
graphed without source mutation. They do not prove retrieval quality, answer
support, context-token efficiency, agent success, or cross-machine performance.

Public docs need two clearly separated evidence tracks:

- product compatibility smoke: "this pinned public source shape can be served"
- quality benchmark: "this corpus and query set returns the expected evidence
  with reasonable recall, citations, token use, latency, and agent behavior"

Without that split, a `PASS` row can be mistaken for a retrieval-quality or
producer-certification claim.

## Goals

- Define a reproducible benchmark spec that separates compatibility smoke from
  retrieval and agent quality.
- Start from the existing 10 pinned upstream smoke cases, the actual pinned
  OpenWiki static-output smoke case, and 11 generated candidate samples.
- Add actual pinned OpenWiki static-output smoke coverage without
  confusing it with synthetic OpenWiki-style fixtures or provider-backed
  generation.
- Record public product names, official links, pinned commits, license evidence,
  and evidence type in a public-safe compatibility inventory.
- Define qrels, corpus, run, and report formats for deterministic quality
  benchmarking.
- Measure recall@5, hit@5, MRR, nDCG@10, citation precision/recall, context tokens,
  payload size, and p50/p95 latency.
- Use a Qwen tokenizer for token accounting and a vLLM-served Qwen model for
  the optional agent tier on DGX.
- Report agent tool calls, input/output/total tokens, success, citation support,
  unsupported claims, latency, and variance.
- Bucket results by Windows local machine and DGX Spark/Ubuntu hardware class.
- Define run counts, paired bootstrap confidence intervals, hard-fail gates, and
  reasonable threshold gates before public benchmark claims.

## Non-Goals

- Do not certify every upstream producer release, plugin setting, desktop app
  runtime, or provider-backed generation path.
- Do not publish private source content, private local paths, private endpoint
  URLs, model credentials, token caches, or raw private query logs.
- Do not replace the existing release smoke or upstream smoke harness in this
  spec slice.
- Do not treat the byte/4 token proxy from earlier exploratory notes as a public
  token metric.
- Do not compare named OSS projects as competitors; benchmark access patterns
  and source shapes.

## Evidence Tracks

### Product Compatibility Smoke

Purpose: prove that a pinned public product sample or generated shape can be
served through the current projection contract.

Required checks:

- source checkout or generated fixture is public and non-sensitive
- ref is a full 40-character commit SHA when using an upstream repository
- source path is relative and inside the checkout or scratch fixture root
- adapter and implementation match the expected catalog entry
- manifest, context, search, read, graph, HTTP, MCP-style, MCP Streamable HTTP,
  and opt-in A2A-style checks pass when covered by the harness
- draft/private/internal folders are not served by default
- source tree hash and checkout status do not change during validation
- result rows say `compatibility smoke`, not `quality pass`

Smoke output must include at least:

| Field | Meaning |
| --- | --- |
| `case_id` | Stable smoke id or generated sample directory name. |
| `product` | Public product/project or local shape label. |
| `official_link` | Public upstream or product URL. |
| `source_kind` | `actual-pinned`, `synthetic-generated`, `materialized-from-contract`, or `operator-smoke`. |
| `commit` | Full pinned commit SHA for actual upstream snapshots. Empty only for synthetic fixtures. |
| `license_evidence` | SPDX id from upstream license metadata or `needs-review` with reason. |
| `source_path` | Public repo-relative path or synthetic shape name, never a local machine path. |
| `adapter` | Observed adapter. |
| `pages` | Projected page count and approved page count. |
| `graph` | Projected node and edge counts. |
| `mutation_check` | Source hash and checkout-clean result. |
| `evidence_type` | What the row proves and what it does not prove. |

### Quality Benchmark

Purpose: measure whether served retrieval returns expected evidence with
reasonable cost and latency.

Negative-query false positive rate is a retrieval stress metric for
retrieval-evaluated serve surfaces. It measures whether a recall-oriented
surface returns rows for unanswerable or hard-negative queries; it is not a
serve-owned final answerability, abstention, model-verification, or final
citation-selection contract.

Required checks:

- corpus manifest is deterministic and public-safe
- qrels identify expected evidence pages and, when possible, supporting spans
- runs compare raw and served access patterns on the same corpus and query set
- Qwen tokenizer token counts replace byte/4 public token proxies
- retrieval-evaluated report surfaces include recall@5, hit@5, MRR, nDCG@10,
  citation precision/recall, context tokens, p50/p95 latency, payload bytes,
  and run counts; telemetry-only surfaces report only their telemetry fields
- negative-query FPR is a retrieval stress metric for retrieval-evaluated serve
  surfaces, not a claim that `llmwiki-serve` owns final answerability,
  abstention, model-backed verification, or final citation selection
- paired bootstrap confidence intervals accompany benchmark deltas
- source mutation, draft leak, private path leak, or unsupported citation claims
  are hard failures regardless of average metric scores

## Initial Compatibility Inventory

The actual repository currently starts from 11 pinned public upstream smoke
cases in `scripts/upstream_candidate_smoke.py`: the initial 10 public upstream
snapshots plus the actual pinned OpenWiki static self-docs case. The generated
candidate suite remains 11 synthetic samples in
`scripts/candidate_sample_artifacts.py`.

Pinned product rows must use the official repository link shown here. SPDX
values below come from GitHub repository license metadata checked during spec
drafting; `NOASSERTION` means GitHub did not identify a license and a manual
license review is required before public docs reuse source content or claim
license compatibility.

| Case | Product | Official link | Planned commit / current commit | License evidence | Evidence type |
| --- | --- | --- | --- | --- | --- |
| `atomic-compiler-basic` | `atomicstrata/llm-wiki-compiler` | https://github.com/atomicstrata/llm-wiki-compiler | `69701f609ae166e9da194c2d340699eb43abf77e` | `MIT` | Actual pinned static LLMWiki Markdown sample. |
| `samuraigpt-agent` | `SamurAIGPT/llm-wiki-agent` | https://github.com/SamurAIGPT/llm-wiki-agent | `11f66f1166994b35de2d7d3d0b246cb28847bbf2` | `MIT` | Actual pinned static agent-maintained Markdown snapshot. |
| `pratiyush-llm-wiki` | `Pratiyush/llm-wiki` | https://github.com/Pratiyush/llm-wiki | `b1088890ee0743810a92577aecad946c6b3eb2d2` | `MIT` | Actual pinned static Markdown knowledge-base snapshot. |
| `logseq-exporter-test-graph` | `logseq/logseq` | https://github.com/logseq/logseq | `a9a67f61ab29972d2e2b6c7a5864e6e3306c0d9a` | `AGPL-3.0` | Actual pinned static Logseq graph-parser fixture. |
| `foam-template` | `foambubble/foam-template` | https://github.com/foambubble/foam-template | `84fa1844270d214520aca32c01d4e27c6728d12e` | `NOASSERTION` | Actual pinned static Foam template workspace; manual license review required before public reuse. |
| `dendron-test-workspace` | `dendronhq/dendron` | https://github.com/dendronhq/dendron | `4420715a421756518863c47005c8c49a38e37621` | `Apache-2.0` | Actual pinned static Dendron test workspace. |
| `karpathy-llm-wiki-vault` | `jason-effi-lab/karpathy-llm-wiki-vault` | https://github.com/jason-effi-lab/karpathy-llm-wiki-vault | `18f4e71518af7d0c51a2fc65f5e3ec3043668e54` | `NOASSERTION` | Actual pinned static LLMWiki Markdown vault; manual license review required before public reuse. |
| `luotwo-llm-wiki` | `luotwo/llm-wiki` | https://github.com/luotwo/llm-wiki | `9ab20ee0e9db3ca0bc7998b1b4a97ba7c821279f` | `NOASSERTION` | Actual pinned static nested `wiki/` source root; manual license review required before public reuse. |
| `nishio-llm-wiki-about-delite` | `nishio/llm-wiki-about-delite` | https://github.com/nishio/llm-wiki-about-delite | `4181dd42ff78d72a5e5a05512a59dc37d7ef97a2` | `NOASSERTION` | Actual pinned static Quartz source tree; manual license review required before public reuse. |
| `iblinkq-llm-wiki-obsidian-blink` | `iBlinkQ/llm-wiki-obsidian-blink` | https://github.com/iBlinkQ/llm-wiki-obsidian-blink | `a9e8399cc29dbcce75fb47f61f1f2034a9dfc199` | `NOASSERTION` | Actual pinned static Obsidian vault; manual license review required before public reuse. |
| `langchain-openwiki-self-docs` | `langchain-ai/openwiki` | https://github.com/langchain-ai/openwiki | `9c253af17f264ac2589ab6781e79e9bb5b5d1238` | `MIT` | Actual pinned OpenWiki static self-docs plus setup contract checks; not synthetic fixture evidence or provider-backed generation. |

The 11 generated candidate samples are synthetic coverage for output shapes, not
actual upstream runtime output:

| Shape | Catalog label | Evidence type |
| --- | --- | --- |
| `atomicstrata-llm-wiki-compiler` | `atomicstrata/llm-wiki-compiler` | Synthetic generated native LLMWiki Markdown shape. |
| `nashsu-llm-wiki` | `nashsu/llm_wiki` | Synthetic generated native LLMWiki Markdown shape. |
| `samuraigpt-llm-wiki-agent` | `SamurAIGPT/llm-wiki-agent` | Synthetic generated native LLMWiki Markdown shape. |
| `lucasastorian-llmwiki` | `lucasastorian/llmwiki` | Synthetic generated native LLMWiki Markdown shape. |
| `pratiyush-llm-wiki` | `Pratiyush/llm-wiki` | Synthetic generated native LLMWiki Markdown shape. |
| `langchain-deepagents-llm-wiki` | `langchain-ai/deepagents examples/llm-wiki` | Synthetic/materialized workspace-layout shape; not provider-backed DeepAgents runtime output. |
| `obsidian-vault` | Obsidian vault | Synthetic format-adapter shape. |
| `logseq-graph` | `logseq/logseq` | Synthetic format-adapter shape. |
| `foam-workspace` | `foambubble/foam` | Synthetic format-adapter shape. |
| `dendron-workspace` | `dendronhq/dendron` | Synthetic format-adapter shape. |
| `quartz-content` | `jackyzha0/quartz` | Synthetic format-adapter shape. |

## Quality Data Formats

### Corpus Manifest

`corpus.jsonl` contains one record per served page:

```json
{"doc_id":"concepts/release","path":"concepts/release.md","title":"Release","adapter":"llmwiki-markdown","role":"page","approved":true,"sha256":"...","source_ref_ids":["SRC-1"],"license":"MIT","public_source":"https://example.invalid/repo/tree/<commit>/wiki"}
```

Rules:

- `doc_id` is the service page id.
- `path` is source-root-relative and public-safe.
- `sha256` is the SHA-256 digest over canonical UTF-8 Markdown bytes: remove
  one leading UTF-8 BOM when present, decode as UTF-8, normalize CRLF and lone
  CR newlines to LF, then re-encode as UTF-8 before hashing. This makes corpus
  hashes independent of Git checkout newline policy while preserving actual
  content changes.
- `license` comes from source-level metadata and may be `synthetic-fixture` or
  `needs-review`.
- draft pages may exist in private local checks, but public benchmark corpora
  must not publish private draft content.

### Public Artifact Digests

Checked-in benchmark case manifests may include `benchmark_artifacts` entries
for public-safe JSON, JSONL, and Markdown artifacts. Each entry stores:

- `path`: a case-directory-relative public artifact path
- `record_count`: the number of non-empty records for line-oriented JSONL files
- `sha256`: the SHA-256 digest over canonical UTF-8 text bytes

Artifact digests use the same canonical text algorithm as corpus Markdown
hashes: remove one leading UTF-8 BOM when present, decode as UTF-8, normalize
CRLF and lone CR newlines to LF, then re-encode as UTF-8 before hashing. The
shared algorithm keeps corpus and artifact hashes independent of Git checkout
newline policy. Checked-in benchmark artifacts themselves must still be stored
with LF line endings so public fixtures remain portable and reviewable.

### Queries

`queries.jsonl` contains deterministic benchmark questions:

```json
{"query_id":"q-known-001","text":"Which page explains release readiness?","class":"known-item","locale":"en","expected_behavior":"answerable"}
```

Required query classes:

- `global-map` for whole-corpus orientation and map-style queries
- `known-item` for local known-item lookup
- `topical` for broad topic search that is not a whole-corpus map query
- `multi-hop` for relationship queries
- `negative` for unanswerable queries
- `korean-numeric` for Korean and numeric search
- `citation` for source-reference lookup
- `plain-markdown` for plain Markdown without native `hot.md` or `index.md`
- `native-llmwiki` for native LLMWiki with `hot.md` and `index.md`

Reports keep these canonical class values in `metrics.<run>.query_classes`.
Global-map, broad-topic, and local comparisons are expressed by comparing
`global-map`, `topical`, and `known-item` breakdowns rather than overloading a
single topical bucket.

### Qrels

`qrels.jsonl` stores judged relevance:

```json
{"query_id":"q-known-001","doc_id":"concepts/release","relevance":3,"support_spans":[{"start":120,"end":220}],"citation_required":true}
```

Relevance scale:

- `3`: direct answer evidence
- `2`: useful supporting context
- `1`: weak orientation only
- `0`: not relevant

### Runs

`runs.jsonl` stores ranked retrieval output. `context_tokens` is the
per-result token contribution for that ranked row, counted with the recorded
Qwen tokenizer. Reports aggregate those per-result contributions by query
before computing context-token distributions.

```json
{"run_id":"serve_context_orientation","query_id":"q-known-001","rank":1,"doc_id":"concepts/release","score":12.4,"citation_ids":["SRC-1"],"context_tokens":832,"payload_tokens":1104,"payload_bytes":4210,"latency_ms":18.5,"surface":"service-context-orientation"}
```

Rules:

- ranks are explicit and must be contiguous from `1` within each
  `run_id`/`query_id`; top-k metrics use `rank <= k`, not row order.
- for hard negative stress queries, any returned row on a retrieval-evaluated
  surface is a false positive for the benchmark metric. This does not require a
  serve runtime abstention API. Nearby pages that explain adjacent technology or
  refusal context are intentionally not relevant evidence for those negatives.
- `citation_ids` must be known public corpus source refs and must be attached
  to the returned row's `doc_id`; citing a different served document is a hard
  failure even when that source ref exists elsewhere in the corpus.
- Collector case manifests that need deterministic fallback citations because
  served pages have no authored `source_refs` must use the canonical
  `citation_mode` value `deterministic-public-path-id`. Other non-empty
  citation-mode strings are rejected by the collector.
- `context_tokens` is per result row. `payload_tokens` is the configured Qwen
  tokenizer count over the serialized full payload for that query, not a sum of
  result rows. `payload_tokens`, `payload_bytes`, `latency_ms`, `surface`, and
  `source_bytes_scanned` are query-level values and must be identical across
  rows for the same `run_id`/`query_id`.
- raw surfaces require `source_bytes_scanned`; served surfaces must use `null`.
- `service-context` remains the evidence-only service context retrieval
  surface.
- `service-context-orientation` is an orientation-only retrieval surface. Rows
  must preserve `ContextPack.orientation` order and normal doc id, token, and
  citation validation.
- `service-context-bundle` is telemetry-only. It records full context payload
  telemetry with orientation rows emitted before evidence rows and deduped by
  `doc_id`, but the evaluator does not treat those linear rows as retrieval,
  citation, or negative-query quality evidence.
- The row-based schema can only record `payload_tokens` when a run emits at
  least one row for a query. For zero-row evidence-only queries, evaluator
  telemetry maps use `0` for that query; bundle/orientation context surfaces
  normally preserve telemetry because orientation rows are present.

Comparable run ids:

- `raw_full`
- `raw_hot_index_search_read`
- `serve_query`
- `serve_search_read`
- `serve_context_orientation`
- `serve_context_bundle`
- `serve_context_read`
- `serve_warm_interval`
- `serve_redis_projection_store`, when applicable
- future managed context variants, when implemented

### Report

`report.json` contains environment, aggregate metrics, confidence intervals,
hard-fail results, threshold gates, tokenizer evidence level, input artifact
digests, and redaction status:

```json
{"schema":"llmwiki-serve-verified-source-benchmark-v1","evidence_track":"quality-benchmark","hardware_bucket":"windows-local","tokenizer":{"id":"Qwen/Qwen3 tokenizer id","revision":"pinned revision","policy":"qwen-tokenizer-required-no-byte-proxy","evidence_level":"local-qwen-tokenizer-load-verified","verified_by_harness":true},"input_artifacts":{"corpus.jsonl":"..."},"metrics":{"serve_query":{"recall_at_5":0.94}},"quality_gates":{"overall_status":"pass","public_quality_claim":true},"hard_failures":[]}
```

Rules:

- `evidence_track` must be `quality-benchmark`; compatibility smoke uses its
  own report shape.
- `input_artifacts` stores public-safe SHA-256 digests of `corpus.jsonl`,
  `queries.jsonl`, `qrels.jsonl`, and `runs.jsonl`, not private input or output
  paths. These digests use the canonical UTF-8 text artifact algorithm rather
  than platform working-tree raw bytes.
- `tokenizer.evidence_level` must distinguish `local-qwen-tokenizer-load-verified`
  from `declared-qwen-provenance`. A report produced in an environment without
  a locally verified Qwen tokenizer must not be presented as a public quality
  claim.
- `quality_gates.public_quality_claim` is true only when hard failures are
  absent, public minimum evidence is met, tokenizer evidence is sufficient, and
  every retrieval-evaluated run passes the retrieval/citation/negative-query
  thresholds. Telemetry-only `service-context-bundle` runs are excluded from
  public retrieval gates and expose `gate_scope: telemetry-only`.
- Passing or failing the negative-query FPR threshold affects retrieval-quality
  reporting only. It must not be presented as proof that `llmwiki-serve` owns
  final answer abstention or model-backed answer verification.
- `metrics.<run>.query_classes` contains per-class metrics keyed by the
  canonical query class values listed above. Retrieval surfaces include quality
  metrics and telemetry distributions per class. Telemetry-only bundle runs
  include only `payload_tokens`, `payload_bytes`, and `latency_ms` distributions
  plus run/query counts.

## Metrics

- `recall@5`: number of distinct relevance >=2 documents recovered in the top 5
  divided by the total number of relevance >=2 documents for that query, then
  averaged over judged queries.
- `hit@5`: fraction of judged queries with at least one relevance >=2 result in
  the top 5.
- `MRR`: reciprocal rank of the first relevance >=2 result.
- `nDCG@10`: graded ranking quality using qrel relevance.
- `citation_precision`: citations attached to returned evidence that point to a
  qrel-supported source or span.
- `citation_recall`: required qrel-supported citations surfaced by the run.
- `context_tokens`: per-result token contribution using a Qwen tokenizer, not a
  byte proxy. Reports sum per-result contributions by query before p50/p95
  aggregation.
- `payload_tokens`: full serialized payload token count using the configured
  Qwen tokenizer, recorded once per query and shared across rows.
- `payload_bytes`: serialized bytes delivered to the caller.
- `latency_ms`: measured p50/p95 per query and per run mode, with cold/warm
  mode labeled.
- `source_bytes_scanned`: only for raw baselines where file scan bytes are
  meaningful; served rows use `null`.

## vLLM Qwen Agent Tier

The optional agent tier runs on DGX Spark/Ubuntu against a vLLM-served Qwen
model. It evaluates end-to-end behavior through `llmwiki-agent-bridge` or an
equivalent controlled agent harness after deterministic retrieval metrics pass.

Required pinned environment fields:

- Qwen model id and checkpoint or quantization label
- vLLM version
- tokenizer id and revision
- chat template
- temperature, top-p, seed, max output tokens
- tool schema and system prompt revision
- served source bundle ids and projection signatures

Agent metrics:

- task success
- citation support rate
- unsupported claim rate
- tool call count
- input tokens
- output tokens
- total tokens
- wall time p50/p95
- variance across repeated runs
- pass@1 and pass over repeated trials when applicable

## Hardware Buckets

Use public-safe hardware bucket labels instead of raw hostnames or local paths.

| Bucket | Purpose | Minimum evidence |
| --- | --- | --- |
| `windows-local` | Local Windows install and CLI/API behavior. | OS version family, Python version, package version, CPU class, memory class, no private paths. |
| `dgx-spark-ubuntu` | vLLM Qwen and higher-load benchmark behavior. | Ubuntu version family, GPU class, vLLM/Qwen versions, package version, no private endpoint URL. |
| `macos-planned` | Future portability smoke. | Mark as planned until actually run. |

## Run Counts And Confidence

Deterministic retrieval benchmark:

- smoke: at least 8 queries per source family, 5 warm runs per query
- public minimum: at least 50 total judged queries and at least 10 judged
  queries per source family reported
- latency: discard 3 warmup runs; collect at least 30 timed runs per query for
  p50/p95 on performance rows
- confidence: report paired bootstrap 95% confidence intervals for recall@5,
  hit@5, MRR, nDCG@10, context tokens, and latency deltas
- randomization: query order is shuffled with a recorded seed

Agent benchmark:

- smoke: at least 10 tasks, 3 runs each
- public minimum: at least 20 tasks, 5 runs each
- variance: report standard deviation or bootstrap interval for success, token
  totals, calls, and wall time
- model nondeterminism: keep temperature `0` and record the seed; still report
  variance rather than a single run

## Acceptance Gates

Hard failures:

- any source tree mutation during read-only serve validation
- any private path, private endpoint URL, credential, token, or raw private
  content in a public report
- any draft/private page served without explicit draft permission
- any public benchmark row that mixes synthetic fixture evidence with actual
  pinned upstream evidence
- any row that labels smoke compatibility as retrieval quality
- any critical citation to a page outside the served source contract
- any benchmark run with missing qrels or undocumented corpus/query provenance

Reasonable retrieval thresholds for public quality claims:

- recall@5 >= 0.90
- MRR >= 0.75
- nDCG@10 >= 0.85
- citation precision >= 0.95
- citation recall >= 0.85 for citation-required queries
- negative-query false positive rate <= 0.05 as a retrieval stress threshold,
  not a final-answer abstention API
- context token p95 must not exceed raw selected-document baseline by more than
  20% unless recall, citation support, or agent success improves materially
- served warm p95 latency must not regress by more than 25% from the comparable
  previous benchmark without an explicit correctness or freshness reason

Reasonable agent thresholds for public agent claims:

- task success >= 0.80 on answerable tasks
- unsupported claim rate <= 0.05
- citation support >= 0.90 on citation-required tasks
- average tool calls, total tokens, or wall time may rise by at most 20% unless
  success or citation support improves by at least 5 percentage points
- no native `hot.md`/`index.md` source may regress when a future managed
  context module is enabled

## Data Safety

Benchmark artifacts may include public repo URLs, public commit SHAs, public
source-root-relative paths, SPDX license ids, metrics, hashes, and generated
synthetic fixture identifiers.

Benchmark artifacts must not include private local paths, private wiki text,
private endpoint URLs, credentials, API key names with values, raw provider
requests, raw chat logs, tailnet hostnames, unredacted Redis keys, cached Redis
payloads, or machine-specific scratch directories.

## References

- Existing compatibility docs: `docs/architecture.md`
- Release gate separation: `docs/release.md`
- Current actual upstream smoke: `scripts/upstream_candidate_smoke.py`
- Current generated candidate suite: `scripts/candidate_sample_artifacts.py`,
  `tests/test_candidate_samples.py`
- Historical benchmark note from the project knowledge store:
  `docs/research/2026-07-17-raw-markdown-vs-served-projection-benchmark.md`
