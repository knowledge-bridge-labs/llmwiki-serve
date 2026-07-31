# Spec: Benchmark Adapter Layer

## Status

Implementation contract accepted for this feature branch. The bundle schema,
official BEIR SciFact acquisition/materialization path, retrieval runner, and
schema `1.1.0` public-report validator are implemented locally. Final Windows
and DGX Spark Ubuntu reports from immutable implementation revision
`git:9f03f39666edf0d2516cf1f6d9c7171802eabd2c` now pass the public report
validator with matching source/artifact checksums and deterministic quality
metrics. Accepted baseline publication, legacy migrations, bridge consumer
changes, merge, package publish, hosted docs deployment, and release validation
remain pending.

This revision replaces the earlier unimplemented mixed JSONL proposal with a
versioned benchmark bundle contract because `llmwiki-agent-bridge` consumes the
same normalized artifact for model-backed evaluation.

Launch-priority revision: the first public recognized retrieval baseline is a
BEIR SciFact full official test split run. Public comparison uses the BEIR
standard primary metrics, nDCG@10 and Recall@100 over top-100 retrieval.
Recall@5, Hit@5, and MRR@10 remain product-secondary diagnostics. The previous
50-query smoke set remains internal regression evidence only and must not be
marketed as recognized benchmark evidence.

Report schema revision `llmwiki-beir-scifact-retrieval-report-v1` version
`1.1.0` hardens public SciFact aggregate reports with a strict implementation
revision, fixed external reference rows, and signed product-minus-reference
deltas. This is a report contract change only; the committed Windows and DGX
Spark Ubuntu full-run reports are accepted for branch-level release evidence
review, not yet merged, published to package registries, or deployed to the
hosted docs site.

## Problem

`llmwiki-serve` has local projection and raw-vs-serve benchmark scripts, but it
does not yet have a legally safe, cross-repo artifact contract for comparing
retrieval quality across public RAG, IR, citation, hallucination, multi-hop,
hard-negative, and local LLMWiki variant corpora.

Public benchmark datasets are distributed under different terms and source
formats. Some include reusable retrieval qrels, some include answer/citation
labels, some include hallucination labels only, and some carry non-commercial
or unclear component restrictions. The repo needs a common offline adapter
contract before adding dataset-specific loaders or publishing quality tables.

## Goals

- Define a versioned normalized benchmark bundle made of `corpus.jsonl`,
  `queries.jsonl`, `qrels.jsonl`, `evidence.jsonl`, and `provenance.json`.
- Keep optional local run state in `run-manifest.json`, outside commits and
  public reports.
- Separate `llmwiki-serve` retrieval/projection metrics from bridge-owned
  model answerability, citation, abstention, token, source-call, and latency
  metrics.
- Establish the first public recognized retrieval baseline on BEIR SciFact full
  official test split using all official test queries, official qrels, top-100
  retrieval, and BEIR-comparable primary metrics.
- Support BRIGHT retrieval, ALCE citation, RAGTruth hallucination-only labels,
  CRAG non-commercial answer-quality research, MuSiQue and 2WikiMultiHopQA
  multi-hop labels, curated hard-negative/unanswerable labels, and local
  LLMWiki/OpenWiki-compatible variants.
- Require resolved immutable source revisions, adapter versions, checksums,
  component licenses, attribution, redistribution policy, and public-report
  policy before any release or marketing claim.
- Provide migration rules for existing `llmwiki-serve-benchmark-corpus-v1` and
  `llmwiki-serve-raw-vs-serve-benchmark-v3` artifacts.

## Non-Goals

- Do not implement runtime behavior in `src/llmwiki_serve`.
- Do not change HTTP, MCP, MCP Streamable HTTP, A2A-style, OpenAPI, or source
  folder adapter contracts.
- Do not vendor public benchmark dataset text, private local wiki content,
  downloaded archives, or generated benchmark corpora.
- Do not claim benchmark certification for upstream datasets or producers.
- Do not run model-backed answer synthesis from this repo.
- Do not treat document qrels as span evidence.
- Do not publish arbitrary pass/fail thresholds before an accepted measured
  baseline exists.
- Do not call the Markdown projection run BEIR certification, an official
  leaderboard result, or a strict BM25 reproduction.

## Requirements

- `REQ-001`: Every adapter materializes `llmwiki-benchmark-bundle-v1` with
  `corpus.jsonl`, `queries.jsonl`, `qrels.jsonl`, `evidence.jsonl`, and
  path-free `provenance.json`. Normalized bundle components may contain dataset
  text and remain local depending on redistribution policy. Only provenance and
  sanitized aggregate reports are public-safe by default; a distributable bundle
  is allowed only when component licenses and public-report policy permit it.
  `run-manifest.json` is optional, local-only, and never committed.
- `REQ-002`: `corpus.jsonl` rows have `corpus_id`, `text`, `title`, and
  `metadata`. `queries.jsonl` rows have `query_id`, `query`,
  `answerability` enum `answerable|unanswerable|unknown`, `label_source`,
  `answers`, `source_split`, `evaluation_split`, and `tags`. `queries.jsonl`
  may be empty only for corpus-only normalization, and only when `qrels.jsonl`
  and `evidence.jsonl` are also empty.
- `REQ-003`: `qrels.jsonl` maps `query_id` to `corpus_id` with `relevance`.
  `evidence.jsonl` rows have `evidence_id`, `query_id`, `corpus_id`,
  `locator`, `required_group`, `hop_index`, `depends_on`, and
  `supports_claim_ids`.
- `REQ-004`: Bundle validation enforces referential integrity: unique
  `query_id`, `corpus_id`, and `evidence_id`; qrels reference existing queries
  and corpus rows; evidence references existing queries and corpus rows; and
  `depends_on` references evidence rows in the same bundle for the same query.
  Evidence dependency graphs must be acyclic.
- `REQ-005`: Evidence labels are explicit. Document-level qrels may drive
  retrieval metrics, but adapters must never infer span, paragraph, section, or
  citation evidence from document qrels.
- `REQ-006`: `provenance.json` is path-free and public-safe by default. It
  records a resolved immutable source commit/content revision, optional
  `source_release` display metadata, adapter version, component checksums,
  SPDX license id plus license URL and verification date where available,
  attribution, redistribution policy, and public-report policy.
- `REQ-007`: Optional local `run-manifest.json` may record cache/output paths,
  seed, adapter hash, source hash, artifact hash, environment, and bridge model,
  provider, prompt, token, source-call, and latency parameters. It is local
  execution evidence and must not be committed or used as public provenance.
- `REQ-008`: CC-BY-NC, non-commercial, unknown-license, or unclear component
  datasets are blocked from release automation and marketing tables. A simple
  maintainer approval flag is not enough to enable public release or marketing
  use.
- `REQ-009`: Dataset roles are fixed: BEIR SciFact full official test split is
  the first public recognized retrieval baseline; BRIGHT is the next
  reasoning-intensive retrieval benchmark; ALCE is citation; RAGTruth is
  hallucination/unsupported-claim analysis only; CRAG is non-commercial
  answer-quality research; MuSiQue and 2WikiMultiHopQA provide multi-hop
  labels; curated hard-negative/unanswerable data has a dedicated track; local
  LLMWiki variants are compatibility/local-evaluation inputs.
- `REQ-010`: Evaluation uses calibration and holdout splits. The deterministic
  50-query smoke set is internal regression evidence only. It is selected from
  predeclared task-class labels, never qrel identities, relevance counts,
  retrieved ranks, model outputs, or hand-selection after seeing outcomes.
  Every run records seed plus adapter, source, and artifact hashes.
- `REQ-011`: `llmwiki-serve` owns retrieval/projection metrics over the bundle.
  `llmwiki-agent-bridge` owns model answerability, citation, abstention,
  unsupported-claim, token, source-call, and latency metrics.
- `REQ-012`: Public reports derived from existing
  `llmwiki-serve-raw-vs-serve-benchmark-v3` output must be sanitized. Existing
  absolute `wiki_root` values, local paths, private query text, private URLs,
  credentials, provider endpoints, and raw traces must be removed before any
  public report is committed.
- `REQ-013`: Release gates are not claimed before a baseline exists. The first
  public report publishes measured numbers with limitations. Later regression
  gates compare against the accepted baseline for the same bundle version,
  resolved source/content revision, adapter version, environment class, package
  version, implementation revision, and metric definition.
- `REQ-014`: The SciFact launch baseline uses the official BEIR `scifact.zip`
  source, the official `test` split, all official test queries, and official
  qrels with top-100 retrieval. Official BEIR documentation lists SciFact as
  300 test queries, about 5K corpus documents, average 1.1 relevant documents
  per query, and MD5 `5f7d1de60b170fc8027bb7898e2efca1`. When actual data
  validation confirms the full split, the canonical invariants are 5,183 corpus
  documents, 300 test queries, 339 qrels, and binary relevance.
- `REQ-015`: Public retrieval reports include dataset name/version/source URL,
  archive MD5 and SHA-256 checksums, adapter and report schema versions,
  corpus/query/qrel counts, package version, immutable implementation revision,
  metric definitions, environment class, index build time, search latency
  p50/p95, and payload bytes p50/p95. The implementation revision must be a
  public-safe immutable identity, currently `git:<40 lowercase hex chars>`.
  The public CLI must require it instead of silently publishing an unresolved
  identity; tests and programmatic fixture calls may use a deterministic
  synthetic revision. The public table must make nDCG@10 and Recall@100 the
  primary BEIR-comparable metrics, clearly label Recall@5, Hit@5, and MRR@10
  as product-secondary diagnostics, and distinguish BEIR paper BM25 from
  Pyserini/Anserini flat BM25 references. Reports must exclude raw query text,
  raw document text, private paths, private hosts, provider endpoints,
  credentials, and local run manifests.
- `REQ-016`: Windows local and DGX Spark Ubuntu full runs must reproduce the
  same artifact/source checksums and deterministic quality metrics exactly.
  Latency and payload timing are reported separately per environment class and
  may differ by machine.
- `REQ-017`: Public copy must describe the `llmwiki-serve` Markdown projection
  result as an informal reproducible same-data comparison. The public report
  must include machine-readable external reference rows for BEIR paper BM25
  nDCG@10 `0.665` and Recall@100 `0.908`, sourced to
  `https://arxiv.org/abs/2104.08663`, and Anserini/Pyserini flat BM25 nDCG@10
  `0.6789` and Recall@100 `0.9253`, sourced to
  `https://github.com/castorini/anserini/blob/master/docs/reproduce/from-document-collection/beir-v1.0.0-scifact.flat.md`.
  Each external row must be labeled as not run by `llmwiki-serve` and include
  signed `llmwiki-serve` product-minus-reference deltas for the primary
  metrics. Public validation must reject tampered reference values, URLs,
  labels/status, or deltas. Public copy must not imply BEIR certification or
  leaderboard submission.

## Bundle Schema

The bundle schema id is `llmwiki-benchmark-bundle-v1`. A normalized bundle has
these files:

```text
corpus.jsonl
queries.jsonl
qrels.jsonl
evidence.jsonl
provenance.json
```

The JSONL files may contain dataset text and are local unless redistribution
terms permit a distributable bundle. An implementation may also write
`run-manifest.json` beside the bundle for a local run. That file is never part
of a distributable bundle.

### `corpus.jsonl`

Each row is one retrievable corpus item.

```json
{
  "corpus_id": "dataset-split-doc-0001",
  "text": "Document, passage, or synthetic fixture text.",
  "title": "Optional public title",
  "metadata": {
    "dataset": "BRIGHT",
    "source_component": "examples",
    "language": "en"
  }
}
```

`corpus_id` is unique within the bundle. `metadata` must be path-free and safe
for the artifact's redistribution policy.

### `queries.jsonl`

Each row is one evaluation query.

```json
{
  "query_id": "dataset-split-query-0001",
  "query": "User-facing retrieval or answer question.",
  "answerability": "answerable",
  "label_source": "dataset-gold",
  "answers": [
    {
      "answer": "Gold answer text",
      "aliases": ["Accepted alias"],
      "claim_ids": ["claim-0001"]
    }
  ],
  "source_split": "dev",
  "evaluation_split": "calibration",
  "tags": ["retrieval", "single-hop"]
}
```

`answerability` is exactly one of `answerable`, `unanswerable`, or `unknown`.
`answers` is empty for unanswerable or unknown queries unless the native dataset
provides explicit answer labels for a diagnostic purpose.
Unknown-answerability queries are excluded from answerability, abstention, and
negative final-answer false-positive denominators. They remain eligible for
retrieval metrics when positive-relevance qrels exist. Queries with only
zero-grade qrels are not retrieval-metric eligible. `source_split` preserves
the native dataset split; `evaluation_split` is `calibration`, `holdout`, or
`smoke`.

### `qrels.jsonl`

Each row is one retrieval relevance judgment.

```json
{
  "query_id": "dataset-split-query-0001",
  "corpus_id": "dataset-split-doc-0001",
  "relevance": 1
}
```

`relevance` is numeric. Values greater than zero are relevant for Recall@k,
MRR@k, and nDCG@k unless an adapter documents a stricter threshold.

### `evidence.jsonl`

Each row is one explicit evidence label.

```json
{
  "evidence_id": "dataset-split-evidence-0001",
  "query_id": "dataset-split-query-0001",
  "corpus_id": "dataset-split-doc-0001",
  "locator": {
    "granularity": "char_span",
    "start": 120,
    "end": 260,
    "section": null,
    "paragraph": null
  },
  "required_group": "hop-1",
  "hop_index": 0,
  "depends_on": [],
  "supports_claim_ids": ["claim-0001"]
}
```

`locator.granularity` is `document`, `section`, `paragraph`, `char_span`,
`token_span`, or `passage`. Span locators require non-negative `start` and
`end` with `start < end`. `char_span.end` must be within the referenced corpus
text length. `token_span` bounds are checked only for non-negative
`start < end` because tokenizer details are external to the bundle validator.
Section locators require `section`; paragraph locators require `paragraph`,
with integer paragraph locators required to be non-negative. Passage locators
require a non-empty `passage` field. Document-level qrels do not create
evidence rows unless the source dataset independently labels document-level
evidence.

`required_group` identifies a required evidence group. Groups are conjunctive:
every required group for a query must be covered. Rows within one group are
acceptable alternatives unless a future schema explicitly marks the group as
requiring all rows. `hop_index` orders multi-hop evidence when the native
dataset provides that order. `depends_on` references other evidence rows for
the same query needed before this evidence can support the query; dependency
graphs must be acyclic. `supports_claim_ids` references claim ids emitted in
`queries.jsonl` answer labels when claim-level labels exist; otherwise it is
empty.

### `provenance.json`

`provenance.json` is public, path-free metadata.

```json
{
  "schema_id": "llmwiki-benchmark-bundle-v1",
  "bundle_id": "beir-scifact-test-2026-08-01",
  "dataset": "BEIR SciFact",
  "source_url": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip",
  "source_revision": "sha256:<validated archive sha256>",
  "source_release": "BEIR scifact.zip; official md5 5f7d1de60b170fc8027bb7898e2efca1",
  "adapter": {
    "name": "beir-scifact",
    "version": "0.1.0"
  },
  "checksums": {
    "corpus.jsonl": "sha256:...",
    "queries.jsonl": "sha256:...",
    "qrels.jsonl": "sha256:...",
    "evidence.jsonl": "sha256:..."
  },
  "component_licenses": [
    {
      "component": "beir-format-and-code",
      "license_spdx": "Apache-2.0",
      "license_url": "https://github.com/beir-cellar/beir/blob/main/LICENSE",
      "license_verified_date": "2026-08-01",
      "attribution": "BEIR benchmark, Thakur et al.",
      "redistribution_policy": "derived-metrics-only",
      "public_report_policy": "allowed-with-attribution"
    },
    {
      "component": "scifact-claims-and-evidence-annotations",
      "license_spdx": "CC-BY-4.0",
      "license_url": "https://github.com/allenai/scifact/blob/master/LICENSE.md",
      "license_verified_date": "2026-08-01",
      "attribution": "SciFact, Wadden et al. 2020",
      "redistribution_policy": "derived-metrics-only",
      "public_report_policy": "allowed-with-attribution"
    },
    {
      "component": "scifact-corpus-abstracts",
      "license_spdx": "ODC-By-1.0",
      "license_url": "https://github.com/allenai/scifact/blob/master/LICENSE.md",
      "license_verified_date": "2026-08-01",
      "attribution": "SciFact corpus abstracts from S2ORC",
      "redistribution_policy": "derived-metrics-only",
      "public_report_policy": "allowed-with-attribution"
    }
  ]
}
```

`source_revision` must be a resolved immutable commit or content revision. A
tag, release label, branch, "latest" pointer, or timestamp-only description is
not enough. `source_release` may store a display label after
`source_revision` is resolved. Component checksums are SHA-256 digests over
canonical UTF-8 text bytes: an optional UTF-8 BOM is removed, and CRLF or CR
line endings are normalized to LF before hashing.

### Local `run-manifest.json`

`run-manifest.json` is optional and local-only. It may include absolute
cache/output paths, source checkout locations, command line, environment,
hardware class, seed, source hash, adapter hash, artifact hash, and bridge run
configuration. When bridge runs are executed, it records provider, model, prompt
template/version, decoding parameters, token counts, source-call counts, and
latency measurements. SciFact retrieval runs also store the same
`implementation_revision` used in the public report so local evidence can be
matched to the immutable code identity. It must be ignored by git and excluded
from public reports.

## Dataset Adapter Scope

| Adapter | Primary role | Initial status | Safety posture |
| --- | --- | --- | --- |
| BEIR SciFact official test split | First public recognized retrieval baseline | Launch priority | Use the official BEIR `scifact.zip`, official test queries and qrels, top-100 retrieval, archive MD5 plus computed SHA-256, per-component license metadata, and sanitized aggregate reports only. Treat the Markdown projection result as an informal same-data comparison, not BEIR certification. |
| Local LLMWiki/OpenWiki variants | Compatibility and local retrieval/projection checks | MVP | Local-only by default; publish aggregate metrics only after private text/path review. |
| BRIGHT | Next reasoning-intensive retrieval benchmark | Next | Use pinned Hugging Face/GitHub revisions; require attribution, component license, and checksum metadata. |
| ALCE | Citation evaluation labels | MVP | Bridge consumes citation artifacts; serve does not claim answer synthesis quality. |
| MuSiQue | Multi-hop answer/evidence labels | MVP candidate | Preserve hop/evidence grouping and split labels. |
| 2WikiMultiHopQA | Multi-hop answer/evidence labels | MVP candidate | Preserve hop/evidence grouping and source attribution. |
| Curated hard-negative/unanswerable track | Abstention and negative-answer evaluation | MVP | Must be independently curated or generated without qrel-aware ranking/sampling leakage. |
| RAGTruth | Hallucination and unsupported-claim labels only | Deferred | Bridge-only diagnostic; not a retrieval or answer-quality benchmark for serve. |
| CRAG | Non-commercial answer-quality research | Deferred | Treat as local research only; no release automation or marketing use by simple maintainer approval. |
| Other BEIR subsets, MIRACL, LoTTE | Broader IR coverage | Deferred | Store per-subset license metadata; do not assume one license covers all constituent datasets. |

The launch implementation order is schema/provenance validators with synthetic
fixtures, minimal local LLMWiki/OpenWiki corpus materialization, BEIR SciFact
official full test baseline, BRIGHT retrieval, ALCE citation, one multi-hop
adapter plus curated unanswerable set, and metrics/export. Broader adapters
come later.

## Evaluation Design

- Split roles: calibration is for adapter debugging, thresholds, and prompt or
  configuration tuning. Holdout is for reporting.
- First public recognized baseline: BEIR SciFact full official test split with
  all official test queries, official qrels, and top-100 retrieval. The primary
  public metrics are nDCG@10 and Recall@100. The official BEIR dataset table
  lists 300 test queries and about 5K corpus documents; full-run data
  validation must confirm the expected launch invariants of 5,183 corpus
  documents, 300 test queries, 339 qrels, and binary relevance.
- Internal smoke subset: exactly 50 deterministic, stratified queries covering
  answerable, unanswerable, unknown where applicable, single-hop, multi-hop,
  citation-labeled, and local-fixture classes. This smoke is not public
  recognized benchmark evidence.
- Sampling rule: stratification may use predeclared task-class labels such as
  answerability, multi-hop, citation, and local-fixture status. It must not use
  qrel identities, relevance counts, retrieved ranks, model outputs, or
  hand-selection after seeing outcomes.
- Reproducibility: every run records seed, adapter hash, resolved
  source/content revision or hash, artifact checksums, environment, and bridge
  provider/model/prompt parameters when bridge metrics are run.

## Metrics And Baseline Policy

Serve-owned metrics:

- nDCG@10: primary BEIR-comparable metric for the SciFact public table.
- Recall@100: primary BEIR-comparable metric for the SciFact public table,
  computed over top-100 retrieval.
- Hit@k / Success@k: product-secondary metric; fraction of queries with at least one qrel-relevant
  `corpus_id` in the top k retrieved results.
- Recall@k: for each query, retrieved relevant documents in top k divided by
  total relevant documents for that query, macro averaged over eligible
  queries. Recall@5 is product-secondary for launch reporting.
- MRR@k: product-secondary mean reciprocal rank of the first qrel-relevant
  result within top k.
- nDCG@k: discounted cumulative gain at k divided by ideal discounted gain at k
  using numeric `relevance`. nDCG@10 is primary for BEIR comparison.
- Index build time: elapsed time to materialize and index the corpus for a
  measured run, reported with environment class.
- Search latency p50/p95: per-query retrieval latency percentiles for the same
  query set, reported separately per environment class.
- Payload bytes p50/p95: response payload size percentiles for search results,
  reported separately per environment class.
- Complete required-document-group coverage@k: for queries with document-level
  evidence labels, fraction of queries where retrieved results cover at least
  one evidence row from every `required_group`.
- Retrieval negative exposure@k: diagnostic rate at which hard-negative or
  unanswerable queries retrieve negative or decoy documents. This is separate
  from final-answer false-positive rate.

The first public SciFact report must publish measured nDCG@10 and Recall@100
as the primary BEIR-comparable metrics. It may also publish product-secondary
macro Recall@5, Hit@5, MRR@10 with cutoff, index build time, search latency
p50/p95, and payload bytes p50/p95 with limitations. It must distinguish the
BEIR paper BM25 reference (`0.665` nDCG@10, `0.908` Recall@100) from the
Pyserini/Anserini flat BM25 reference (`0.6789` nDCG@10, `0.9253` Recall@100)
as external reference rows that were not run by `llmwiki-serve`. The report
must compute signed product-minus-reference deltas for nDCG@10 and Recall@100,
and label the `llmwiki-serve` Markdown projection as an informal reproducible
same-data comparison, not BEIR certification or an official leaderboard result.
It must not present an arbitrary pass/fail threshold. Later release gates
compare deterministic quality metrics against the accepted baseline for the
same source artifact, adapter/report schema versions, implementation revision,
package version, and metric definitions.

Bridge-owned metrics:

- Citation precision: supported citations divided by all emitted citations.
- Exact evidence-span/claim coverage: after bridge reads, fraction of labeled
  spans or claims covered by cited support where explicit labels exist.
- Unsupported citation/claim count: emitted citations or claims with no
  supporting evidence.
- Negative answer false-positive rate: unanswerable or hard-negative queries
  that receive a concrete unsupported final answer instead of abstention.
- Canonical class stability: known, multi-hop, and negative canonical classes
  measured over repeated bridge runs after a baseline is accepted.
- Token, source-call, and latency budgets: record for every bridge run. They
  may be claimed only after an accepted baseline exists.

## User / Agent Flow

1. Maintainer selects an adapter and resolved immutable source/content
   revision. The launch public path starts with BEIR SciFact official test
   split.
2. Maintainer runs an explicit download/materialize command with cache and
   output directories.
3. Adapter writes normalized bundle files plus path-free `provenance.json`.
4. Optional local execution writes `run-manifest.json`, which remains
   uncommitted.
5. Serve retrieval runner evaluates retrieval/projection metrics against qrels
   and explicit evidence labels. For the launch SciFact report, it uses all
   official test queries and official qrels and requires a public-safe
   immutable `implementation_revision` before CLI report generation.
6. Bridge runner in `llmwiki-agent-bridge` consumes the bundle read-only for
   model answerability, citation, abstention, unsupported-claim, token,
   source-call, and latency metrics.
7. Public docs publish only sanitized aggregate metrics whose provenance passes
   license and data-safety checks. Bundle files are redistributed only when
   component policy permits. Windows local and DGX Spark Ubuntu runs must match
   deterministic quality metrics and checksums before publication.

## Compatibility And Migration

- `llmwiki-serve-benchmark-corpus-v1` is the existing Wiki-CS
  materialized-corpus manifest/schema, not a mixed query/answer record. A
  compatibility normalizer reads the existing manifest plus materialized
  Markdown where applicable, emits corpus rows and path-free provenance, and
  leaves `queries.jsonl`, `qrels.jsonl`, and `evidence.jsonl` empty unless a
  separate query/qrel/evidence source is explicitly supplied.
- `llmwiki-serve-raw-vs-serve-benchmark-v3` remains a legacy result/report
  shape. A sanitizer must remove absolute `wiki_root`, local paths, private
  query text, private URLs, credentials, provider endpoints, and raw traces
  before any derived public report is committed.
- Legacy normalization/report migration must not invent span evidence from
  qrels. When a legacy artifact lacks explicit evidence labels,
  `evidence.jsonl` is empty and evidence metrics are marked unavailable.
- HTTP: No change.
- MCP: No change.
- MCP Streamable HTTP: No change.
- A2A-style: No change.
- Source-folder adapters: No runtime change. Offline materializers may emit
  ordinary Markdown folders that existing adapters can serve in local tests.
- Existing clients: No change.

## Data Safety

- No benchmark dataset text, downloaded archives, generated Markdown corpora,
  local wiki text, private paths, private URLs, credentials, provider endpoints,
  raw query text from private corpora, or raw model traces may be committed.
- The repo may commit tiny synthetic fixtures, schemas, validators, path-free
  provenance examples, and sanitized aggregate metric reports after review.
- Normalized bundle JSONL can include dataset text. It is local-only unless
  component licenses, redistribution policy, and public-report policy permit a
  distributable bundle.
- Public reports must include dataset source, resolved source/content revision,
  adapter version, artifact checksums, component licenses, attribution,
  redistribution policy, public-report policy, metric definitions, package
  version, immutable implementation revision, environment class,
  corpus/query/qrel counts, index build time, search latency p50/p95, payload
  bytes p50/p95, external reference rows with signed primary-metric deltas,
  whether bridge metrics were run, and a clear note that the Markdown
  projection is an informal same-data comparison.
- Non-commercial or unclear-license datasets remain local research only until a
  separate release/legal policy permits the specific public use.

## ADR Assessment

ADR required:
`docs/decisions/2026-07-31-cross-repo-benchmark-artifact-contract.md`.

The ADR is required because the bundle is a cross-repo artifact consumed by
`llmwiki-agent-bridge`. It records that `llmwiki-serve` offline tooling owns the
bundle schema, validators, adapters, and migrations, while the bridge owns
model answerability/citation metrics, version negotiation behavior, and
read-only consumption safety.

## Open Questions

- Whether the first implementation uses `datasets`/`ir_datasets` as optional
  dev dependencies or keeps loader integration in standalone scripts.
- Which multi-hop source follows SciFact, BRIGHT, and ALCE first: MuSiQue or
  2WikiMultiHopQA.
- What external review process will govern public reporting for
  non-commercial, unknown-license, or mixed-license component datasets.

## References

- Existing benchmark materializer spec: `specs/authoritative-benchmark-materializer/`
- Existing benchmark research note: `docs/research/2026-07-17-thousand-page-benchmark-corpus-options.md`
- ADR: `docs/decisions/2026-07-31-cross-repo-benchmark-artifact-contract.md`
- BEIR repo: https://github.com/beir-cellar/beir
- BEIR dataset table: https://github.com/beir-cellar/beir/wiki/Datasets-available
- BEIR metrics: https://github.com/beir-cellar/beir/wiki/Metrics-available
- BEIR license: https://github.com/beir-cellar/beir/blob/main/LICENSE
- BEIR paper: https://datasets-benchmarks-proceedings.neurips.cc/paper/2021/file/65b9eea6e1cc6bb9f0cd2a47751a186f-Paper-round2.pdf
- Resources for Brewing BEIR: https://arxiv.org/abs/2306.07471
- Anserini BEIR SciFact flat BM25 regression: https://github.com/castorini/anserini/blob/master/docs/reproduce/from-document-collection/beir-v1.0.0-scifact.flat.md
- BEIR SciFact archive: https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip
- SciFact repo: https://github.com/allenai/scifact
- SciFact license: https://github.com/allenai/scifact/blob/master/LICENSE.md
- SciFact paper: https://aclanthology.org/2020.emnlp-main.609/
- BRIGHT: https://github.com/xlang-ai/BRIGHT
- BRIGHT dataset: https://huggingface.co/datasets/xlangai/BRIGHT
- ALCE: https://github.com/princeton-nlp/ALCE
- ALCE data: https://huggingface.co/datasets/princeton-nlp/ALCE-data
- RAGTruth: https://github.com/ParticleMedia/RAGTruth
- CRAG: https://github.com/facebookresearch/CRAG
- MuSiQue: https://github.com/StonyBrookNLP/musique
- 2WikiMultiHopQA: https://github.com/Alab-NII/2wikimultihop
- MIRACL: https://github.com/project-miracl/miracl
- LoTTE: https://github.com/stanford-futuredata/ColBERT/blob/main/LoTTE.md
