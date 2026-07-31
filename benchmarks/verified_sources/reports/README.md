# Verified Source Reports

These tracked JSON files are public-safe aggregate reports. They do not include
raw query text, raw document text, local run manifests, private endpoints,
credentials, or absolute local paths.

## SciFact Retrieval Benchmark

The `beir-scifact-*` reports are the first public retrieval benchmark artifacts
for this repo. They run the official BEIR SciFact `test` split projected to
Markdown through `LlmWikiService.search(query, limit=100)`.

| Report | OS | Analyzer | nDCG@10 | Recall@100 | Recall@5 | Hit@5 | MRR@10 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| [`beir-scifact-windows-2026-08-01.json`](beir-scifact-windows-2026-08-01.json) | Windows | `english` | `0.6905159872` | `0.9286666667` | `0.7459444444` | `0.7666666667` | `0.656265873` |
| [`beir-scifact-dgx-spark-ubuntu-2026-08-01.json`](beir-scifact-dgx-spark-ubuntu-2026-08-01.json) | Linux | `english` | `0.6905159872` | `0.9286666667` | `0.7459444444` | `0.7666666667` | `0.656265873` |

Both reports use:

- package version `0.2.8`
- implementation revision `git:9f03f39666edf0d2516cf1f6d9c7171802eabd2c`
- report schema `llmwiki-beir-scifact-retrieval-report-v1` version `1.1.0`
- runner `beir-scifact-retrieval-runner` version `0.2.0`
- official BEIR SciFact archive URL
  `https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip`
- published archive MD5 `5f7d1de60b170fc8027bb7898e2efca1`
- archive SHA-256
  `536e14446a0ba56ed1398ab1055f39fe852686ecad24a6306c80c490fa8e0165`
- normalized `corpus.jsonl` SHA-256
  `6292ff95f78e1d603bcf4829890cf6ae880d4c12a2cfeaaebc22ae057287a697`
- normalized `queries.jsonl` SHA-256
  `ed78ebc0659aea2dbd6b426f57b1f1e1ae729d6804585b1efde654d72ac505fd`
- normalized `qrels.jsonl` SHA-256
  `df7f6f54a33f3c45a347e7f1085bf91913d5b6289b7319fb2dbb483d751ecd9e`
- normalized `evidence.jsonl` SHA-256
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

The canonical data shape is `5,183` corpus documents, `300` official test
queries, and `339` qrels. The `english` analyzer is explicit opt-in; the
product default remains `legacy`.

## External References

The SciFact reports include fixed contextual reference rows that were not run
by `llmwiki-serve`:

| Reference | nDCG@10 | Recall@100 | Source |
| --- | ---: | ---: | --- |
| BEIR paper BM25 | `0.665` | `0.908` | https://arxiv.org/abs/2104.08663 |
| Anserini/Pyserini flat BM25 | `0.6789` | `0.9253` | https://github.com/castorini/anserini/blob/master/docs/reproduce/from-document-collection/beir-v1.0.0-scifact.flat.md |

The reports record signed product-minus-reference deltas for those primary
metrics. Treat the numbers as a same-data Markdown projection comparison, not
as an official BEIR result.

## Report Fields

Key fields in the SciFact aggregate reports:

| Field | Meaning |
| --- | --- |
| `primary_metrics` | BEIR-comparable retrieval metrics: nDCG@10 and Recall@100. |
| `product_secondary_metrics` | Product diagnostics: Recall@5, Hit@5, and MRR@10. |
| `external_reference_rows` | Fixed published reference rows and signed product-minus-reference deltas. |
| `normalized_bundle` | Path-free source URL, archive content revision, adapter version, and normalized artifact checksums. |
| `source_archive` | Official archive URL, computed SHA-256, and published MD5. |
| `public_report_gate` | Repository publication-policy result for sanitized aggregate reporting. |
| `public_safety` | Confirms that the report excludes per-query rows and raw query/document text. |
| `freshness_policy` | States that per-query timings use warm fixed-index retrieval. |
| `search_latency_ms_top100_result_payloads` | Per-query service.search latency percentiles for the top-100 payload. |
| `serialized_search_payload_bytes_top100_result_payloads` | Serialized top-100 response payload size percentiles. |

## Validation

The public report validator checks schema version, implementation revision,
public analyzer profile, fixed external reference rows, signed deltas, and
public-safety constraints.

```powershell
uv run python -c "import json; from pathlib import Path; from scripts.benchmark_adapters.scifact_runner import validate_public_report; files=[Path('benchmarks/verified_sources/reports/beir-scifact-windows-2026-08-01.json'), Path('benchmarks/verified_sources/reports/beir-scifact-dgx-spark-ubuntu-2026-08-01.json')]; [validate_public_report(json.loads(p.read_text(encoding='utf-8'))) for p in files]; print('validated')"
```

The final reports above passed that validator on this branch.

## Rerun Notes

Benchmark adapters are repo-level reproducibility tooling, not installed
`llmwiki-serve` console commands. Run them from a source checkout with the dev
environment installed. The scripts require local cache/output paths under the
repo's ignored benchmark workspace and keep raw BEIR data out of tracked files.

```powershell
$env:SCIFACT_ROOT=".llmwiki-work/benchmark-adapters/scifact"
$env:SCIFACT_REVISION="git:$(git rev-parse HEAD)"

uv run python -m scripts.benchmark_adapters.beir_scifact_acquire `
  --cache-dir "$env:SCIFACT_ROOT/cache" `
  --extract-dir "$env:SCIFACT_ROOT/extract" `
  --run-manifest "$env:SCIFACT_ROOT/run-manifest.json"

# Use the local dataset_root recorded by acquisition as --input-dir.
uv run python -m scripts.benchmark_adapters.beir_scifact `
  --input-dir "<local-acquired-scifact-dataset-root>" `
  --output-dir "$env:SCIFACT_ROOT/materialized" `
  --archive-sha256 "536e14446a0ba56ed1398ab1055f39fe852686ecad24a6306c80c490fa8e0165"

uv run python -m scripts.benchmark_adapters.scifact_runner `
  --wiki-dir "$env:SCIFACT_ROOT/materialized/wiki" `
  --bundle-dir "$env:SCIFACT_ROOT/materialized/bundle" `
  --output-report "benchmarks/verified_sources/reports/beir-scifact-local-rerun.json" `
  --implementation-revision "$env:SCIFACT_REVISION" `
  --analyzer-profile english
```

Only sanitized aggregate reports should be considered for commits. Local
archives, materialized bundles, Markdown projections, and run manifests remain
local execution evidence.

## OS Comparison

Quality metrics, source checksums, normalized bundle checksums, package version,
runner version, schema version, and implementation revision match across the
Windows and DGX Spark Ubuntu reports. Latency and index-build timings are
reported per environment and differ by machine:

| Environment | Index build ms | Search p50 ms | Search p95 ms | Payload p50 bytes | Payload p95 bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Windows local | `65340.052` | `251.538` | `504.044` | `64537.5` | `66021.5` |
| DGX Spark Ubuntu | `2311.269` | `55.054` | `113.213` | `64537.5` | `66021.5` |

The latency rows are telemetry for these runs only. They are not a cross-device
performance claim or a release gate.

## Compatibility-Smoke Reports

- `openwiki-native-windows-retrieval-quality-2026-07-30.json` covers the
  OpenWiki native source benchmark on Windows.
- `openwiki-shadow-windows-retrieval-quality-2026-07-30.json` covers the
  OpenWiki generic shadow-source benchmark on Windows.
- `pratiyush-native-windows-retrieval-quality-2026-07-30.json` covers the
  Pratiyush llm-wiki native source benchmark on Windows.
- `pratiyush-shadow-windows-retrieval-quality-2026-07-30.json` covers the
  Pratiyush llm-wiki generic shadow-source benchmark on Windows.
- `upstream-candidate-smoke-windows-2026-07-30.json` is the existing Windows
  upstream compatibility-smoke report, not a retrieval-quality claim.

The compatibility-smoke reports document retrieval and telemetry for public
source variants. Their quality gates remain separate from the SciFact public
retrieval benchmark.
