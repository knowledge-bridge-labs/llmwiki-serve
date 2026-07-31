"""Run BEIR SciFact retrieval through LlmWikiService and emit public aggregates."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import platform
import re
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from llmwiki_serve import LlmWikiService  # noqa: E402
from llmwiki_serve import __version__ as LLMWIKI_SERVE_VERSION  # noqa: E402
from llmwiki_serve.models import WikiIndex  # noqa: E402
from llmwiki_serve.search import (  # noqa: E402
    DEFAULT_ANALYZER_PROFILE,
    PUBLIC_ANALYZER_PROFILES,
    AnalyzerProfile,
)
from scripts.benchmark_adapters import (  # noqa: E402
    beir_scifact,
    beir_scifact_acquire,
)
from scripts.benchmark_adapters import bundle_validator as validator  # noqa: E402

REPORT_SCHEMA_ID = "llmwiki-beir-scifact-retrieval-report-v1"
REPORT_SCHEMA_VERSION = "1.1.0"
RUN_MANIFEST_SCHEMA_ID = "llmwiki-beir-scifact-retrieval-run-v1"
RUNNER_NAME = "beir-scifact-retrieval-runner"
RUNNER_VERSION = "0.2.0"
RETRIEVAL_LIMIT = 100
BENCHMARK_FIXED_INDEX_REFRESH_INTERVAL_SECONDS = 86_400.0
REPORT_REPO_ROOT = Path("benchmarks") / "verified_sources" / "reports"
SURFACE = "LlmWikiService.search(query, limit=100)"
PRIMARY_METRICS = ("nDCG@10", "Recall@100")
PRODUCT_SECONDARY_METRICS = ("Recall@5", "Hit@5", "MRR@10")
EXTERNAL_REFERENCE_STATUS = "external-reference-not-run-by-llmwiki-serve"
BEIR_PAPER_BM25_SOURCE_URL = "https://arxiv.org/abs/2104.08663"
ANSERINI_SCIFACT_FLAT_BM25_SOURCE_URL = (
    "https://github.com/castorini/anserini/blob/master/docs/reproduce/"
    "from-document-collection/beir-v1.0.0-scifact.flat.md"
)
REPORT_TOP_LEVEL_FIELDS = {
    "analyzer_profile",
    "component_licenses",
    "corpus_count",
    "dataset",
    "external_reference_rows",
    "freshness_policy",
    "implementation_revision",
    "index_build_ms",
    "judged_query_count",
    "limitations",
    "machine_class",
    "metric_definitions",
    "metric_groups",
    "normalized_bundle",
    "os_family",
    "package_version",
    "primary_metrics",
    "product_secondary_metrics",
    "python_version",
    "public_report_gate",
    "public_safety",
    "qrel_count",
    "query_count",
    "retrieval_limit",
    "runner",
    "schema_id",
    "schema_version",
    "search_latency_ms_top100_result_payloads",
    "serialized_search_payload_bytes_top100_result_payloads",
    "source_archive",
    "surface",
}
PRIVATE_PUBLIC_REPORT_KEYS = {
    "bundle_dir",
    "bundle_root",
    "cache_dir",
    "doc_text",
    "document_text",
    "host",
    "hostname",
    "local_paths",
    "output_path",
    "payloads",
    "per_query",
    "per_query_metrics",
    "query",
    "query_text",
    "rows",
    "run_manifest",
    "run_manifest_path",
    "search_payloads",
    "trace",
    "traces",
    "user",
    "username",
    "wiki_dir",
    "wiki_root",
}

ClockNs = Callable[[], int]
QrelsByQuery = dict[str, dict[str, float]]


class SearchService(Protocol):
    def index(self) -> WikiIndex: ...

    def search(self, query: str, *, limit: int) -> list[dict[str, Any]]: ...


ServiceFactory = Callable[[Path, AnalyzerProfile], SearchService]


class ScifactRunnerError(RuntimeError):
    """Raised when the SciFact retrieval runner cannot proceed safely."""


def default_service_factory(
    wiki_dir: Path,
    analyzer_profile: AnalyzerProfile = DEFAULT_ANALYZER_PROFILE,
) -> SearchService:
    """Create the benchmark default service with a fixed index for warm retrieval timing."""
    return LlmWikiService(
        wiki_dir,
        refresh_interval_seconds=BENCHMARK_FIXED_INDEX_REFRESH_INTERVAL_SECONDS,
        analyzer_profile=analyzer_profile,
    )


@dataclass(frozen=True)
class QueryRow:
    query_id: str
    query: str


@dataclass(frozen=True)
class ScifactBundle:
    corpus_ids: frozenset[str]
    queries: tuple[QueryRow, ...]
    qrel_count: int
    qrels_by_query: QrelsByQuery
    provenance: Mapping[str, Any]
    public_gate: validator.PublicReleaseGateResult


@dataclass(frozen=True)
class QueryMetrics:
    ndcg_at_10: float
    recall_at_100: float
    recall_at_5: float
    hit_at_5: float
    mrr_at_10: float

    def value(self, name: str) -> float:
        if name == "nDCG@10":
            return self.ndcg_at_10
        if name == "Recall@100":
            return self.recall_at_100
        if name == "Recall@5":
            return self.recall_at_5
        if name == "Hit@5":
            return self.hit_at_5
        if name == "MRR@10":
            return self.mrr_at_10
        raise ScifactRunnerError(f"unknown metric name: {name}")


@dataclass(frozen=True)
class RunTelemetry:
    index_build_ms: float
    search_latency_ms: tuple[float, ...]
    serialized_search_payload_bytes: tuple[int, ...]


@dataclass(frozen=True)
class ScifactBenchmarkResult:
    report: dict[str, object]
    wiki_before_sha256: str
    wiki_after_sha256: str
    bundle_before_sha256: str
    bundle_after_sha256: str


def run_scifact_benchmark(
    *,
    wiki_dir: Path,
    bundle_dir: Path,
    output_report: Path,
    analyzer_profile: AnalyzerProfile,
    implementation_revision: str,
    run_manifest: Path | None = None,
    repo_root: Path = ROOT,
    service_factory: ServiceFactory = default_service_factory,
    clock_ns: ClockNs = time.perf_counter_ns,
) -> ScifactBenchmarkResult:
    """Run every normalized SciFact query once and write a sanitized aggregate report."""
    profile = validate_public_analyzer_profile(analyzer_profile)
    revision = validate_implementation_revision(implementation_revision)
    resolved_wiki = _require_existing_dir(wiki_dir, "wiki_dir")
    resolved_bundle = _require_existing_dir(bundle_dir, "bundle_dir")
    _require_separate_inputs(resolved_wiki, resolved_bundle)
    resolved_report = _resolve_public_report_path(
        output_report,
        repo_root=repo_root,
        wiki_dir=resolved_wiki,
        bundle_dir=resolved_bundle,
    )
    resolved_manifest = (
        _resolve_run_manifest_path(
            run_manifest,
            repo_root=repo_root,
            wiki_dir=resolved_wiki,
            bundle_dir=resolved_bundle,
        )
        if run_manifest is not None
        else None
    )
    if resolved_manifest is not None and resolved_manifest == resolved_report:
        raise ScifactRunnerError("output_report and run_manifest must be separate files")

    wiki_before = compute_tree_digest(resolved_wiki)
    bundle_before = compute_tree_digest(resolved_bundle)

    bundle = load_scifact_bundle(resolved_bundle)
    service = service_factory(resolved_wiki, profile)
    index_start_ns = clock_ns()
    index = service.index()
    index_build_ms = _elapsed_ms(index_start_ns, clock_ns())
    path_to_original_id = build_path_to_original_id_map(index, corpus_ids=bundle.corpus_ids)

    query_metrics: list[QueryMetrics] = []
    search_latencies_ms: list[float] = []
    payload_sizes: list[int] = []
    for query in bundle.queries:
        search_start_ns = clock_ns()
        results = service.search(query.query, limit=RETRIEVAL_LIMIT)
        search_latencies_ms.append(_elapsed_ms(search_start_ns, clock_ns()))
        payload_sizes.append(serialized_search_payload_bytes(results))
        ranked_corpus_ids = map_search_results_to_corpus_ids(results, path_to_original_id)
        query_metrics.append(
            compute_query_metrics(ranked_corpus_ids, bundle.qrels_by_query[query.query_id])
        )

    wiki_after = compute_tree_digest(resolved_wiki)
    bundle_after = compute_tree_digest(resolved_bundle)
    if wiki_after != wiki_before:
        raise ScifactRunnerError("wiki tree mutated during SciFact retrieval run")
    if bundle_after != bundle_before:
        raise ScifactRunnerError("bundle tree mutated during SciFact retrieval run")

    telemetry = RunTelemetry(
        index_build_ms=index_build_ms,
        search_latency_ms=tuple(search_latencies_ms),
        serialized_search_payload_bytes=tuple(payload_sizes),
    )
    report = build_aggregate_report(
        bundle,
        query_metrics=query_metrics,
        telemetry=telemetry,
        analyzer_profile=profile,
        implementation_revision=revision,
    )
    validate_public_report(report)
    _atomic_write_json(resolved_report, report)
    if resolved_manifest is not None:
        _atomic_write_json(
            resolved_manifest,
            build_run_manifest(
                wiki_dir=resolved_wiki,
                bundle_dir=resolved_bundle,
                output_report=resolved_report,
                report=report,
                wiki_before_sha256=wiki_before,
                wiki_after_sha256=wiki_after,
                bundle_before_sha256=bundle_before,
                bundle_after_sha256=bundle_after,
                analyzer_profile=profile,
                implementation_revision=revision,
            ),
        )

    return ScifactBenchmarkResult(
        report=report,
        wiki_before_sha256=wiki_before,
        wiki_after_sha256=wiki_after,
        bundle_before_sha256=bundle_before,
        bundle_after_sha256=bundle_after,
    )


def load_scifact_bundle(bundle_dir: Path) -> ScifactBundle:
    validation = validator.validate_bundle(bundle_dir)
    provenance = validation.provenance
    dataset = _require_mapping_string(provenance, "dataset", "provenance.json")
    if dataset != beir_scifact.DATASET_NAME:
        raise ScifactRunnerError(f"bundle provenance dataset must be {beir_scifact.DATASET_NAME!r}")
    adapter = validator.require_mapping(provenance, "adapter", "provenance.json")
    adapter_name = _require_mapping_string(adapter, "name", "provenance.json.adapter")
    if adapter_name != beir_scifact.ADAPTER_NAME:
        raise ScifactRunnerError(
            f"bundle provenance adapter.name must be {beir_scifact.ADAPTER_NAME!r}"
        )

    queries = tuple(load_query_rows(bundle_dir / "queries.jsonl"))
    qrels_by_query = load_qrels_by_query(bundle_dir / "qrels.jsonl")
    if not queries:
        raise ScifactRunnerError("SciFact bundle must contain query rows")
    missing_qrels = sorted(
        query.query_id
        for query in queries
        if not any(relevance > 0 for relevance in qrels_by_query.get(query.query_id, {}).values())
    )
    if missing_qrels:
        raise ScifactRunnerError(
            "every SciFact query must have at least one qrel with relevance > 0"
        )

    public_gate = validator.evaluate_public_release_gate(bundle_dir, mode="public-report")
    if not public_gate.passed:
        raise ScifactRunnerError("bundle public-report gate failed")

    return ScifactBundle(
        corpus_ids=validation.corpus_ids,
        queries=queries,
        qrel_count=validation.qrel_count,
        qrels_by_query=qrels_by_query,
        provenance=provenance,
        public_gate=public_gate,
    )


def load_query_rows(path: Path) -> list[QueryRow]:
    rows: list[QueryRow] = []
    for line_number, record in validator.load_jsonl(path):
        label = f"{path.name}:{line_number}"
        rows.append(
            QueryRow(
                query_id=validator.require_string(record, "query_id", label),
                query=validator.require_string(record, "query", label),
            )
        )
    return rows


def load_qrels_by_query(path: Path) -> QrelsByQuery:
    qrels: QrelsByQuery = {}
    for line_number, record in validator.load_jsonl(path):
        label = f"{path.name}:{line_number}"
        query_id = validator.require_string(record, "query_id", label)
        corpus_id = validator.require_string(record, "corpus_id", label)
        relevance = validator.require_number(record, "relevance", label)
        qrels.setdefault(query_id, {})[corpus_id] = relevance
    return qrels


def build_path_to_original_id_map(
    index: WikiIndex,
    *,
    corpus_ids: frozenset[str],
) -> dict[str, str]:
    path_to_original_id: dict[str, str] = {}
    original_id_to_path: dict[str, str] = {}
    missing_paths: list[str] = []
    for page in sorted(index.pages, key=lambda item: item.path):
        original_id = page.frontmatter.get("original_id")
        if not isinstance(original_id, str) or not original_id:
            missing_paths.append(page.path)
            continue
        if original_id not in corpus_ids:
            raise ScifactRunnerError("indexed page original_id is not present in bundle corpus")
        if page.path in path_to_original_id:
            raise ScifactRunnerError("duplicate indexed page path mapping")
        existing_path = original_id_to_path.get(original_id)
        if existing_path is not None:
            raise ScifactRunnerError("duplicate indexed page original_id mapping")
        path_to_original_id[page.path] = original_id
        original_id_to_path[original_id] = page.path
    if missing_paths:
        raise ScifactRunnerError("indexed SciFact page is missing frontmatter original_id")
    missing_corpus_ids = corpus_ids - frozenset(original_id_to_path)
    if missing_corpus_ids:
        raise ScifactRunnerError("bundle corpus_id is missing an indexed original_id mapping")
    return path_to_original_id


def map_search_results_to_corpus_ids(
    results: Sequence[Mapping[str, Any]],
    path_to_original_id: Mapping[str, str],
) -> list[str]:
    ranked_corpus_ids: list[str] = []
    seen_paths: set[str] = set()
    seen_corpus_ids: set[str] = set()
    for result in results:
        raw_path = result.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ScifactRunnerError("search result is missing a path")
        if raw_path in seen_paths:
            raise ScifactRunnerError("duplicate search result path mapping")
        seen_paths.add(raw_path)
        corpus_id = path_to_original_id.get(raw_path)
        if corpus_id is None:
            raise ScifactRunnerError("search result path has no original_id mapping")
        if corpus_id in seen_corpus_ids:
            raise ScifactRunnerError("duplicate search result original_id mapping")
        seen_corpus_ids.add(corpus_id)
        ranked_corpus_ids.append(corpus_id)
    return ranked_corpus_ids


def compute_query_metrics(
    ranked_corpus_ids: Sequence[str],
    qrels: Mapping[str, float],
) -> QueryMetrics:
    if len(set(ranked_corpus_ids)) != len(ranked_corpus_ids):
        raise ScifactRunnerError("ranked corpus ids contain duplicate mappings")
    positive_ids = {corpus_id for corpus_id, relevance in qrels.items() if relevance > 0}
    if not positive_ids:
        raise ScifactRunnerError("query metrics require at least one positive qrel")
    return QueryMetrics(
        ndcg_at_10=ndcg_at_k(ranked_corpus_ids, qrels, 10),
        recall_at_100=recall_at_k(ranked_corpus_ids, positive_ids, 100),
        recall_at_5=recall_at_k(ranked_corpus_ids, positive_ids, 5),
        hit_at_5=hit_at_k(ranked_corpus_ids, positive_ids, 5),
        mrr_at_10=mrr_at_k(ranked_corpus_ids, positive_ids, 10),
    )


def ndcg_at_k(
    ranked_corpus_ids: Sequence[str],
    qrels: Mapping[str, float],
    cutoff: int,
) -> float:
    ideal_relevances = sorted(
        (relevance for relevance in qrels.values() if relevance > 0),
        reverse=True,
    )[:cutoff]
    ideal = dcg_from_relevances(ideal_relevances)
    if ideal <= 0:
        return 0.0
    observed_relevances = [qrels.get(corpus_id, 0.0) for corpus_id in ranked_corpus_ids[:cutoff]]
    return dcg_from_relevances(observed_relevances) / ideal


def dcg_from_relevances(relevances: Sequence[float]) -> float:
    total = 0.0
    for rank, relevance in enumerate(relevances, start=1):
        if relevance <= 0:
            continue
        gain = math.pow(2.0, relevance) - 1.0
        total += gain / math.log2(rank + 1)
    return total


def recall_at_k(
    ranked_corpus_ids: Sequence[str],
    positive_ids: set[str],
    cutoff: int,
) -> float:
    if not positive_ids:
        return 0.0
    return len(set(ranked_corpus_ids[:cutoff]) & positive_ids) / len(positive_ids)


def hit_at_k(
    ranked_corpus_ids: Sequence[str],
    positive_ids: set[str],
    cutoff: int,
) -> float:
    return 1.0 if set(ranked_corpus_ids[:cutoff]) & positive_ids else 0.0


def mrr_at_k(
    ranked_corpus_ids: Sequence[str],
    positive_ids: set[str],
    cutoff: int,
) -> float:
    for rank, corpus_id in enumerate(ranked_corpus_ids[:cutoff], start=1):
        if corpus_id in positive_ids:
            return 1.0 / rank
    return 0.0


def build_aggregate_report(
    bundle: ScifactBundle,
    *,
    query_metrics: Sequence[QueryMetrics],
    telemetry: RunTelemetry,
    analyzer_profile: AnalyzerProfile,
    implementation_revision: str,
) -> dict[str, object]:
    if not query_metrics:
        raise ScifactRunnerError("aggregate report requires at least one judged query")
    profile = validate_public_analyzer_profile(analyzer_profile)
    revision = validate_implementation_revision(implementation_revision)
    provenance = bundle.provenance
    checksums = validator.require_mapping(provenance, "checksums", "provenance.json")
    adapter = validator.require_mapping(provenance, "adapter", "provenance.json")
    source_revision = _require_mapping_string(
        provenance,
        "source_revision",
        "provenance.json",
    )
    primary_metrics = macro_metrics(query_metrics, PRIMARY_METRICS)
    product_secondary_metrics = macro_metrics(query_metrics, PRODUCT_SECONDARY_METRICS)
    return {
        "analyzer_profile": profile,
        "component_licenses": component_licenses_metadata(provenance),
        "corpus_count": len(bundle.corpus_ids),
        "dataset": _require_mapping_string(provenance, "dataset", "provenance.json"),
        "external_reference_rows": external_reference_rows(primary_metrics),
        "freshness_policy": freshness_policy_metadata(),
        "implementation_revision": revision,
        "index_build_ms": _round_measurement(telemetry.index_build_ms),
        "judged_query_count": len(query_metrics),
        "limitations": report_limitations(),
        "machine_class": machine_class(),
        "metric_definitions": metric_definitions(),
        "metric_groups": {
            "primary": list(PRIMARY_METRICS),
            "product_secondary": list(PRODUCT_SECONDARY_METRICS),
        },
        "normalized_bundle": {
            "adapter": {
                "name": _require_mapping_string(adapter, "name", "provenance.json.adapter"),
                "version": _require_mapping_string(adapter, "version", "provenance.json.adapter"),
            },
            "checksums": {
                file_name: _require_mapping_string(
                    checksums,
                    file_name,
                    "provenance.json.checksums",
                )
                for file_name in validator.BUNDLE_JSONL_FILES
            },
            "schema_id": validator.SCHEMA_ID,
            "source_revision": source_revision,
            "source_url": _require_mapping_string(provenance, "source_url", "provenance.json"),
        },
        "os_family": platform.system() or "unknown",
        "package_version": LLMWIKI_SERVE_VERSION,
        "primary_metrics": primary_metrics,
        "product_secondary_metrics": product_secondary_metrics,
        "python_version": platform.python_version(),
        "public_report_gate": bundle.public_gate.as_json(),
        "public_safety": {
            "contains_per_query_rows": False,
            "contains_query_or_document_text": False,
            "report_class": "sanitized-aggregate-only",
        },
        "qrel_count": bundle.qrel_count,
        "query_count": len(bundle.queries),
        "retrieval_limit": RETRIEVAL_LIMIT,
        "runner": {
            "name": RUNNER_NAME,
            "version": RUNNER_VERSION,
        },
        "schema_id": REPORT_SCHEMA_ID,
        "schema_version": REPORT_SCHEMA_VERSION,
        "search_latency_ms_top100_result_payloads": percentile_distribution(
            telemetry.search_latency_ms,
            unit="ms",
            label="per-query service.search top-100 result payload latency",
        ),
        "serialized_search_payload_bytes_top100_result_payloads": percentile_distribution(
            tuple(float(value) for value in telemetry.serialized_search_payload_bytes),
            unit="bytes",
            label="serialized per-query service.search top-100 result payload size",
        ),
        "source_archive": source_archive_metadata(source_revision),
        "surface": SURFACE,
    }


def external_reference_rows(
    product_primary_metrics: Mapping[str, float],
) -> list[dict[str, object]]:
    """Return fixed external reference rows plus product-minus-reference deltas."""
    rows: list[dict[str, object]] = []
    for reference in external_reference_definitions():
        reference_metrics = cast(Mapping[str, float], reference["primary_metrics"])
        rows.append(
            {
                **reference,
                "delta_product_minus_reference": {
                    metric: _round_metric(
                        product_primary_metrics[metric] - reference_metrics[metric]
                    )
                    for metric in PRIMARY_METRICS
                },
                "primary_metrics": dict(reference_metrics),
            }
        )
    return rows


def external_reference_definitions() -> tuple[dict[str, object], ...]:
    return (
        {
            "label": "BEIR paper BM25",
            "primary_metrics": {
                "nDCG@10": 0.665,
                "Recall@100": 0.908,
            },
            "reference_id": "beir-paper-bm25",
            "run_by_llmwiki_serve": False,
            "source_detail": "BEIR paper SciFact BM25 reference metrics.",
            "source_url": BEIR_PAPER_BM25_SOURCE_URL,
            "status": EXTERNAL_REFERENCE_STATUS,
        },
        {
            "label": "Anserini/Pyserini flat BM25",
            "primary_metrics": {
                "nDCG@10": 0.6789,
                "Recall@100": 0.9253,
            },
            "reference_id": "anserini-pyserini-flat-bm25",
            "run_by_llmwiki_serve": False,
            "source_detail": ("Anserini BEIR v1.0.0 SciFact flat BM25 regression metrics."),
            "source_url": ANSERINI_SCIFACT_FLAT_BM25_SOURCE_URL,
            "status": EXTERNAL_REFERENCE_STATUS,
        },
    )


def component_licenses_metadata(provenance: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(component)
        for component in validator.require_object_list(
            provenance.get("component_licenses"),
            "provenance.json.component_licenses",
        )
    ]


def source_archive_metadata(source_revision: str) -> dict[str, object]:
    return {
        "content_sha256": source_revision_content_sha256(source_revision),
        "content_sha256_source": "provenance.source_revision",
        "published_md5": {
            "reference_status": "published-reference",
            "value": beir_scifact_acquire.PUBLISHED_MD5,
        },
        "source_url": beir_scifact_acquire.SOURCE_URL,
    }


def source_revision_content_sha256(source_revision: str) -> str:
    normalized = source_revision.strip().lower()
    _, separator, digest = normalized.partition(":")
    if separator:
        if normalized[: len("sha256:")] != "sha256:":
            raise ScifactRunnerError("provenance source_revision must be a SHA-256 content hash")
    else:
        digest = normalized
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ScifactRunnerError("provenance source_revision must contain a 64-hex SHA-256 digest")
    return f"sha256:{digest}"


def report_limitations() -> list[str]:
    return [
        (
            "Markdown projection and llmwiki-serve tokenization differ from "
            "BEIR/Anserini retrieval pipelines."
        ),
        (
            "This same-data result is not BEIR certification and is not an official "
            "leaderboard submission."
        ),
        (
            "Latency is warm fixed-index retrieval excluding source re-scan; "
            "index_build_ms reports initial projection/index build separately."
        ),
        "Latency measurements are hardware- and runtime-specific.",
    ]


def freshness_policy_metadata() -> dict[str, object]:
    return {
        "mode": "warm-fixed-index-retrieval",
        "mutation_detection": "pre/post wiki and bundle tree SHA-256 digests",
        "refresh_interval_seconds": BENCHMARK_FIXED_INDEX_REFRESH_INTERVAL_SECONDS,
        "service_configuration": (
            "LlmWikiService refresh_interval_seconds is fixed for the benchmark default "
            "factory so per-query service.search timing excludes source freshness scans."
        ),
    }


def metric_definitions() -> dict[str, dict[str, object]]:
    return {
        "Hit@5": {
            "cutoff": 5,
            "formula": (
                "1 if any qrel relevance > 0 corpus_id is retrieved in top 5 else 0; macro mean"
            ),
            "group": "product_secondary",
            "positive_relevance": "qrel relevance > 0",
        },
        "MRR@10": {
            "cutoff": 10,
            "formula": (
                "reciprocal rank of first qrel relevance > 0 corpus_id within top 10; "
                "0 if absent; macro mean"
            ),
            "group": "product_secondary",
            "positive_relevance": "qrel relevance > 0",
        },
        "Recall@100": {
            "cutoff": 100,
            "formula": (
                "per query count of positive relevant corpus_ids recovered in top 100 "
                "divided by count of positive relevant corpus_ids; macro mean"
            ),
            "group": "primary",
            "positive_relevance": "qrel relevance > 0",
        },
        "Recall@5": {
            "cutoff": 5,
            "formula": (
                "per query count of positive relevant corpus_ids recovered in top 5 "
                "divided by count of positive relevant corpus_ids; macro mean"
            ),
            "group": "product_secondary",
            "positive_relevance": "qrel relevance > 0",
        },
        "nDCG@10": {
            "cutoff": 10,
            "formula": (
                "DCG@10 divided by ideal DCG@10 using gain 2**rel - 1 and discount log2(rank + 1)"
            ),
            "group": "primary",
            "qrel_relevance": "graded numeric qrel relevance",
        },
    }


def macro_metrics(
    query_metrics: Sequence[QueryMetrics],
    names: Sequence[str],
) -> dict[str, float]:
    return {
        name: _round_metric(
            sum(metrics.value(name) for metrics in query_metrics) / len(query_metrics)
        )
        for name in names
    }


def percentile_distribution(
    values: Sequence[float],
    *,
    unit: str,
    label: str,
) -> dict[str, object]:
    if not values:
        raise ScifactRunnerError("percentile distribution requires at least one value")
    return {
        "label": label,
        "method": "linear interpolation over per-query values",
        "p50": _round_measurement(percentile(values, 50.0)),
        "p95": _round_measurement(percentile(values, 95.0)),
        "unit": unit,
    }


def percentile(values: Sequence[float], percentile_value: float) -> float:
    if not values:
        raise ScifactRunnerError("percentile requires at least one value")
    if percentile_value < 0 or percentile_value > 100:
        raise ScifactRunnerError("percentile must be between 0 and 100")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (percentile_value / 100.0)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def serialized_search_payload_bytes(results: Sequence[Mapping[str, Any]]) -> int:
    payload = json.dumps(
        list(results),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return len(payload.encode("utf-8"))


def validate_public_report(report: Mapping[str, object]) -> None:
    disallowed_key = first_disallowed_public_key(report)
    if disallowed_key is not None:
        raise ScifactRunnerError(f"public report contains private field {disallowed_key!r}")
    unknown = sorted(set(report) - REPORT_TOP_LEVEL_FIELDS)
    if unknown:
        raise ScifactRunnerError(f"unknown public report fields: {unknown}")
    missing = sorted(REPORT_TOP_LEVEL_FIELDS - set(report))
    if missing:
        raise ScifactRunnerError(f"missing public report fields: {missing}")
    schema_id = _require_mapping_string(report, "schema_id", "scifact aggregate report")
    if schema_id != REPORT_SCHEMA_ID:
        raise ScifactRunnerError(f"public report schema_id must be {REPORT_SCHEMA_ID!r}")
    schema_version = _require_mapping_string(
        report,
        "schema_version",
        "scifact aggregate report",
    )
    if schema_version != REPORT_SCHEMA_VERSION:
        raise ScifactRunnerError(f"public report schema_version must be {REPORT_SCHEMA_VERSION!r}")
    validate_implementation_revision(
        _require_mapping_string(report, "implementation_revision", "scifact aggregate report")
    )
    validate_public_analyzer_profile(
        _require_mapping_string(report, "analyzer_profile", "scifact aggregate report")
    )
    validate_external_reference_rows(report)
    validator.assert_public_safe_value(dict(report), "scifact aggregate report")


def validate_public_analyzer_profile(value: str) -> AnalyzerProfile:
    normalized = value.strip()
    if normalized not in PUBLIC_ANALYZER_PROFILES:
        expected = "|".join(PUBLIC_ANALYZER_PROFILES)
        raise ScifactRunnerError(f"analyzer_profile must be {expected}")
    return cast(AnalyzerProfile, normalized)


def validate_implementation_revision(value: str) -> str:
    normalized = value.strip()
    if not re.fullmatch(r"git:[a-f0-9]{40}", normalized):
        raise ScifactRunnerError("implementation_revision must be git:<40 lowercase hex chars>")
    if normalized == f"git:{'0' * 40}":
        raise ScifactRunnerError("implementation_revision must not be the all-zero placeholder")
    return normalized


def validate_external_reference_rows(report: Mapping[str, object]) -> None:
    primary_metrics = _require_float_metric_mapping(
        report.get("primary_metrics"),
        "scifact aggregate report.primary_metrics",
        PRIMARY_METRICS,
    )
    observed_rows = validator.require_object_list(
        report.get("external_reference_rows"),
        "scifact aggregate report.external_reference_rows",
    )
    expected_rows = external_reference_rows(primary_metrics)
    if observed_rows != expected_rows:
        raise ScifactRunnerError(
            "external_reference_rows must match fixed public references and "
            "product-minus-reference deltas"
        )


def _require_float_metric_mapping(
    value: object,
    label: str,
    metric_names: Sequence[str],
) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ScifactRunnerError(f"{label} must be an object")
    missing = sorted(set(metric_names) - set(value))
    unknown = sorted(set(value) - set(metric_names))
    if missing:
        raise ScifactRunnerError(f"{label} missing metrics: {missing}")
    if unknown:
        raise ScifactRunnerError(f"{label} unknown metrics: {unknown}")
    metrics: dict[str, float] = {}
    for metric_name in metric_names:
        raw_value = value[metric_name]
        if isinstance(raw_value, bool) or not isinstance(raw_value, int | float):
            raise ScifactRunnerError(f"{label}.{metric_name} must be numeric")
        metric_value = float(raw_value)
        if not math.isfinite(metric_value):
            raise ScifactRunnerError(f"{label}.{metric_name} must be finite")
        metrics[metric_name] = _round_metric(metric_value)
    return metrics


def first_disallowed_public_key(value: object) -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in PRIVATE_PUBLIC_REPORT_KEYS:
                return str(key)
            nested = first_disallowed_public_key(item)
            if nested is not None:
                return nested
    if isinstance(value, list | tuple):
        for item in value:
            nested = first_disallowed_public_key(item)
            if nested is not None:
                return nested
    return None


def build_run_manifest(
    *,
    wiki_dir: Path,
    bundle_dir: Path,
    output_report: Path,
    report: Mapping[str, object],
    wiki_before_sha256: str,
    wiki_after_sha256: str,
    bundle_before_sha256: str,
    bundle_after_sha256: str,
    analyzer_profile: AnalyzerProfile,
    implementation_revision: str,
) -> dict[str, object]:
    profile = validate_public_analyzer_profile(analyzer_profile)
    revision = validate_implementation_revision(implementation_revision)
    return {
        "benchmark_configuration": {
            "analyzer_profile": profile,
            "implementation_revision": revision,
        },
        "environment": {
            "machine": platform.machine() or "unknown",
            "os_family": platform.system() or "unknown",
            "python_version": platform.python_version(),
        },
        "local_paths": {
            "bundle_dir": str(bundle_dir),
            "output_report": str(output_report),
            "wiki_dir": str(wiki_dir),
        },
        "public_report_summary": {
            "analyzer_profile": profile,
            "dataset": report["dataset"],
            "implementation_revision": report["implementation_revision"],
            "primary_metrics": report["primary_metrics"],
            "product_secondary_metrics": report["product_secondary_metrics"],
            "retrieval_limit": report["retrieval_limit"],
            "schema_id": report["schema_id"],
            "schema_version": report["schema_version"],
        },
        "schema_id": RUN_MANIFEST_SCHEMA_ID,
        "tree_immutability": {
            "bundle_after_sha256": bundle_after_sha256,
            "bundle_before_sha256": bundle_before_sha256,
            "bundle_mutated": bundle_before_sha256 != bundle_after_sha256,
            "wiki_after_sha256": wiki_after_sha256,
            "wiki_before_sha256": wiki_before_sha256,
            "wiki_mutated": wiki_before_sha256 != wiki_after_sha256,
        },
    }


def machine_class() -> str:
    machine = platform.machine().strip().lower()
    if machine in {"amd64", "x86_64"}:
        return "x86_64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return machine or "unknown"


def compute_tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root)
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run LlmWikiService retrieval over a materialized BEIR SciFact bundle."
    )
    parser.add_argument("--wiki-dir", type=Path, required=True)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    parser.add_argument(
        "--implementation-revision",
        required=True,
        help="Immutable public implementation identity in the form git:<40 lowercase hex chars>.",
    )
    parser.add_argument("--run-manifest", type=Path)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument(
        "--analyzer-profile",
        choices=PUBLIC_ANALYZER_PROFILES,
        required=True,
        help="Public analyzer profile for this report. Final English opt-in reports use english.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run_scifact_benchmark(
            wiki_dir=cast(Path, args.wiki_dir),
            bundle_dir=cast(Path, args.bundle_dir),
            output_report=cast(Path, args.output_report),
            implementation_revision=cast(str, args.implementation_revision),
            run_manifest=cast(Path | None, args.run_manifest),
            repo_root=cast(Path, args.repo_root),
            analyzer_profile=cast(AnalyzerProfile, args.analyzer_profile),
        )
    except (
        OSError,
        ScifactRunnerError,
        ValueError,
        validator.BundleValidationError,
    ) as error:
        print(f"beir scifact retrieval run failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result.report, indent=2, sort_keys=True))
    return 0


def _elapsed_ms(start_ns: int, end_ns: int) -> float:
    return max(0.0, (end_ns - start_ns) / 1_000_000.0)


def _round_metric(value: float) -> float:
    return round(value, 10)


def _round_measurement(value: float) -> float:
    return round(value, 3)


def _require_existing_dir(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise ScifactRunnerError(f"{label} must exist and be a directory")
    return resolved


def _require_separate_inputs(wiki_dir: Path, bundle_dir: Path) -> None:
    if _is_same_or_nested(wiki_dir, bundle_dir) or _is_same_or_nested(bundle_dir, wiki_dir):
        raise ScifactRunnerError("wiki_dir and bundle_dir must be separate non-nested directories")


def _resolve_public_report_path(
    path: Path,
    *,
    repo_root: Path,
    wiki_dir: Path,
    bundle_dir: Path,
) -> Path:
    resolved = path.expanduser().resolve()
    if resolved.name == "run-manifest.json":
        raise ScifactRunnerError("output_report must not be named run-manifest.json")
    if resolved.exists() and resolved.is_dir():
        raise ScifactRunnerError("output_report must be a file path")
    if _is_same_or_nested(resolved, wiki_dir) or _is_same_or_nested(resolved, bundle_dir):
        raise ScifactRunnerError("output_report must be outside wiki_dir and bundle_dir")
    resolved_repo = repo_root.expanduser().resolve()
    try:
        relative_to_repo = resolved.relative_to(resolved_repo)
    except ValueError:
        relative_to_repo = None
    if relative_to_repo is not None:
        allowed_parts = REPORT_REPO_ROOT.parts
        if (
            len(relative_to_repo.parts) <= len(allowed_parts)
            or relative_to_repo.parts[: len(allowed_parts)] != allowed_parts
        ):
            raise ScifactRunnerError(
                "output_report inside the repository must stay under "
                f"{REPORT_REPO_ROOT.as_posix()}/"
            )
    return resolved


def _resolve_run_manifest_path(
    path: Path,
    *,
    repo_root: Path,
    wiki_dir: Path,
    bundle_dir: Path,
) -> Path:
    resolved = path.expanduser().resolve()
    if _is_same_or_nested(resolved, wiki_dir) or _is_same_or_nested(resolved, bundle_dir):
        raise ScifactRunnerError("run_manifest must be outside wiki_dir and bundle_dir")

    resolved_repo = repo_root.expanduser().resolve()
    try:
        relative_to_repo = resolved.relative_to(resolved_repo)
    except ValueError:
        relative_to_repo = None
    if relative_to_repo is not None and relative_to_repo.parts[:2] != (
        validator.DEFAULT_WORKSPACE_NAME,
        "benchmark-adapters",
    ):
        raise ScifactRunnerError(
            "run-manifest.json inside the repository must stay under "
            f"{validator.DEFAULT_BENCHMARK_WORKSPACE.as_posix()}/"
        )
    try:
        validator.validate_local_run_manifest(resolved, repo_root=repo_root)
    except validator.BundleValidationError as error:
        raise ScifactRunnerError(str(error)) from error
    return resolved


def _is_same_or_nested(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _require_mapping_string(record: Mapping[str, Any], field: str, label: str) -> str:
    return validator.require_string(record, field, label)


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _temporary_file_sibling(path)
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _fsync_parent_directory(path)
    except Exception:
        _remove_file(temp_path)
        raise


def _temporary_file_sibling(path: Path) -> Path:
    handle, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    os.close(handle)
    return Path(temp_name)


def _fsync_parent_directory(path: Path) -> None:
    try:
        descriptor = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _remove_file(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PRODUCT_SECONDARY_METRICS",
    "PRIMARY_METRICS",
    "REPORT_SCHEMA_ID",
    "REPORT_SCHEMA_VERSION",
    "RETRIEVAL_LIMIT",
    "RUN_MANIFEST_SCHEMA_ID",
    "BENCHMARK_FIXED_INDEX_REFRESH_INTERVAL_SECONDS",
    "AnalyzerProfile",
    "ANSERINI_SCIFACT_FLAT_BM25_SOURCE_URL",
    "BEIR_PAPER_BM25_SOURCE_URL",
    "DEFAULT_ANALYZER_PROFILE",
    "EXTERNAL_REFERENCE_STATUS",
    "QueryMetrics",
    "ScifactBenchmarkResult",
    "ScifactRunnerError",
    "build_aggregate_report",
    "build_path_to_original_id_map",
    "compute_query_metrics",
    "compute_tree_digest",
    "default_service_factory",
    "external_reference_definitions",
    "external_reference_rows",
    "freshness_policy_metadata",
    "hit_at_k",
    "load_scifact_bundle",
    "main",
    "map_search_results_to_corpus_ids",
    "mrr_at_k",
    "ndcg_at_k",
    "recall_at_k",
    "run_scifact_benchmark",
    "validate_implementation_revision",
    "validate_public_analyzer_profile",
    "validate_public_report",
]
