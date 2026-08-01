# 2026-07-31 Decision Record: Cross-Repo Benchmark Artifact Contract

## Status

Accepted for the feature branch. The benchmark bundle contract, official BEIR
SciFact acquisition/materialization path, retrieval runner, and schema `1.1.0`
sanitized report validator are implemented locally. Final immutable-revision
Windows and DGX Spark Ubuntu public reports, accepted baseline publication,
legacy migrations, bridge consumer changes, and release validation remain
pending.

## Context

The benchmark adapter layer is offline tooling in `llmwiki-serve`, but the
resulting normalized artifact is consumed by `llmwiki-agent-bridge` for
model-backed answerability and citation evaluation. The earlier single mixed
JSONL proposal was never implemented and did not cleanly separate corpus rows,
query labels, qrels, evidence labels, provenance, and local run state.

Existing benchmark artifacts include `llmwiki-serve-benchmark-corpus-v1`, the
Wiki-CS materialized-corpus manifest/schema, and
`llmwiki-serve-raw-vs-serve-benchmark-v3`. The raw-vs-serve report can contain
absolute `wiki_root` values and query text, so any public report derived from it
needs an explicit sanitizer.

The launch priority changed after benchmark research. The earlier direction
favored broad adapter coverage and BRIGHT as the first prominent retrieval
target. For public launch trust, that is too diffuse and too easy to dismiss as
project-local evidence. The first public recognized retrieval baseline is now
BEIR SciFact full official test split because it is small enough for
reproducible Windows and DGX Spark Ubuntu runs, has official BEIR qrels and
published reference metrics, and is already familiar to information retrieval
practitioners. BRIGHT remains the next reasoning-intensive retrieval benchmark;
ALCE and bridge citation evaluation follow after the retrieval baseline path is
stable.

## Decision

`llmwiki-serve` offline tooling owns the
`llmwiki-benchmark-bundle-v1` artifact schema and validators. A normalized
bundle contains:

- `corpus.jsonl`
- `queries.jsonl`
- `qrels.jsonl`
- `evidence.jsonl`
- `provenance.json`

Bundle JSONL may contain dataset text and remains local unless component
licenses and redistribution policy permit distribution. `provenance.json` is
path-free and public-safe by default. It records resolved immutable source
commit/content revision, optional `source_release` display metadata, adapter
version, component checksums, SPDX license id plus license URL and verification
date where available, attribution, redistribution policy, and public-report
policy. Optional `run-manifest.json` is local-only, may include cache/output
paths and run environment details, and must never be committed or used as
public provenance.

`llmwiki-serve` owns:

- schema and provenance validation
- offline adapter materialization
- normalization from the existing `llmwiki-serve-benchmark-corpus-v1` Wiki-CS
  materialized-corpus manifest/schema plus materialized Markdown where
  applicable
- sanitized aggregate report export from
  `llmwiki-serve-raw-vs-serve-benchmark-v3`
- serve-owned nDCG@10 and Recall@100 as BEIR-comparable primary public metrics
  for SciFact
- serve-owned product-secondary Hit@5, standard macro Recall@5, MRR@10,
  complete required-document-group coverage@k, latency, payload-size, and
  projection metrics

`llmwiki-agent-bridge` consumes the bundle read-only and owns:

- model answerability metrics
- citation precision/recall and unsupported citation/claim metrics
- exact evidence-span/claim coverage after reads
- abstention and negative final-answer false-positive metrics
- token, source-call, latency, model, provider, and prompt-parameter recording
- bridge-side version negotiation and migration behavior

Version negotiation is explicit. Producers write a schema id and version.
Consumers declare supported schema versions. Additive fields may be accepted
within `llmwiki-benchmark-bundle-v1` only when existing required fields and
semantics remain unchanged. Breaking changes require a new bundle schema id and
a migration path owned by offline tooling.

Source safety is read-only across repos. Bridge consumers may read normalized
bundle files and write separate local run outputs, but they must not mutate
corpus sources, adapter outputs, provenance, or source roots. Public reports
must be generated from sanitized aggregate exports, not raw local run manifests.
Distributable bundles are allowed only when component licenses and policy
permit them.

Public SciFact reporting uses the official BEIR `scifact.zip` archive, the
official `test` split, all official test queries, official qrels, top-100
retrieval, and sanitized aggregate output only. Official BEIR documentation
lists SciFact as 300 test queries, about 5K corpus documents, average 1.1
relevant documents per query, and MD5
`5f7d1de60b170fc8027bb7898e2efca1`. After actual data validation, the launch
invariants are 5,183 corpus documents, 300 test queries, 339 qrels, and binary
relevance.

The public table distinguishes reference rows from product rows:

- BEIR paper BM25: nDCG@10 `0.665`, Recall@100 `0.908`, sourced to
  `https://arxiv.org/abs/2104.08663`.
- Pyserini/Anserini flat BM25: nDCG@10 `0.6789`, Recall@100 `0.9253`,
  sourced to
  `https://github.com/castorini/anserini/blob/master/docs/reproduce/from-document-collection/beir-v1.0.0-scifact.flat.md`.
- `llmwiki-serve` Markdown projection: informal reproducible same-data
  comparison, not BEIR certification or an official leaderboard submission.

The SciFact report schema requires a public-safe immutable implementation
revision, currently `git:<40 lowercase hex chars>`. The public runner CLI must
receive that identity explicitly. Programmatic tests may use a deterministic
synthetic revision, but public reports must not silently publish an unresolved
code identity. Public report validation also recomputes signed
product-minus-reference deltas for nDCG@10 and Recall@100 and rejects tampered
reference values, URLs, labels/status, or deltas.

The first public report publishes measured numbers and limitations. Arbitrary
pass/fail thresholds are not published before an accepted baseline exists.
Later regression gates compare against the accepted baseline for the same
source artifact, adapter/report schema versions, implementation revision,
package version, environment class, and metric definitions. Windows local and
DGX Spark Ubuntu full runs must reproduce source/artifact checksums and
deterministic quality metrics exactly; latency is reported separately by
environment class.

## Consequences

- Positive consequences:
  Shared benchmark artifacts become reproducible, schema-validatable, and safe
  for bridge consumption without changing runtime service contracts.
  The launch benchmark story becomes easier to trust because it starts with a
  recognized full public dataset and standard BEIR metrics instead of a
  self-authored smoke set.
- Tradeoffs:
  Initial adapter work is slightly slower because validators, provenance, and
  migration checks come before broad dataset coverage.
  BRIGHT-first exploration moves one step later so the first public table can
  anchor on a widely recognizable baseline.
- Compatibility or migration impact:
  The existing Wiki-CS materialized-corpus manifest/schema requires
  normalization into corpus rows and provenance, with query/qrel/evidence files
  empty unless separate labels are supplied. Raw-vs-serve reports require
  sanitization before public use. Missing explicit evidence labels make
  evidence metrics unavailable rather than inferred.
- Security or data-handling impact:
  Provenance excludes paths, private URLs, local query text, credentials,
  provider endpoints, and raw traces. `run-manifest.json` stays local-only.
  Bundle JSONL may contain dataset text and is not public-safe by default.
  CC-BY-NC, non-commercial, unknown-license, or unclear-license datasets remain
  blocked from release automation and marketing tables unless a separate
  release/legal policy permits the specific public use.

## Alternatives Considered

- Option: Keep one mixed JSONL record.
  Why not: It was only a proposal, and it conflates corpus, queries, qrels,
  evidence, provenance, and local run state while inviting span evidence to be
  inferred from document qrels.

- Option: Let `llmwiki-agent-bridge` own the shared schema.
  Why not: Dataset materialization, retrieval qrels, local LLMWiki adapters, and
  legacy benchmark migration originate in `llmwiki-serve` offline tooling.

- Option: Publish raw benchmark reports or normalized bundles directly.
  Why not: Existing reports may contain absolute `wiki_root`, local paths, and
  private query text. Normalized bundle JSONL may contain dataset text and is
  distributable only when component licenses and policy permit.

- Option: Lead public launch with a 50-query smoke set or local LLMWiki variant
  coverage table.
  Why not: Those are useful internal regression and compatibility checks, but
  they are not recognized benchmark evidence and would not provide the same
  trust signal as a full BEIR SciFact run.

## Follow-Up Work

- [x] Add schema/provenance validators and synthetic fixtures in
  `llmwiki-serve`.
- [x] Add a minimal local Markdown/LLMWiki corpus-only materializer for
  public-provenance bundles.
- [x] Add or finish the BEIR SciFact adapter and safe acquisition helper.
- [x] Add SciFact retrieval runner with primary nDCG@10 and Recall@100 plus
  product-secondary Recall@5, Hit@5, and MRR@10.
- [x] Add sanitized SciFact aggregate report export with reference-baseline
  rows, signed deltas, strict implementation revision, and explicit
  non-certification wording.
- [ ] Run final full SciFact validation on Windows local and DGX Spark Ubuntu
  from a real immutable implementation revision.
- [ ] Add normalization from `llmwiki-serve-benchmark-corpus-v1`.
- [ ] Add sanitizer/export for `llmwiki-serve-raw-vs-serve-benchmark-v3`.
- [ ] Add bridge-side supported-version declaration and read-only consumer
  checks in `llmwiki-agent-bridge`.
- [ ] Establish accepted baseline reports before enforcing release gates or
  claiming token, source-call, latency, pass/fail, or regression budgets.

## References

- Spec: `specs/benchmark-adapter-layer/spec.md`
- Plan: `specs/benchmark-adapter-layer/plan.md`
- Tests: `specs/benchmark-adapter-layer/tests.md`
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
