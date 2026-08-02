"""Run NoMIRACL-ko judged-pool retrieval smoke reports through LlmWikiService."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, TypeAlias, cast

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
    AnalyzerProfile,
    search_corpus,
)
from llmwiki_serve.vector import (  # noqa: E402
    VECTOR_CACHE_SCHEMA_VERSION,
    VECTOR_INDEX_SCHEMA_ID,
    VECTOR_TEXT_SCHEMA_ID,
    HybridDiagnostics,
    VectorConfig,
)
from scripts.benchmark_adapters import bundle_validator as validator  # noqa: E402
from scripts.benchmark_adapters import nomiracl_ko  # noqa: E402
from scripts.benchmark_adapters.scifact_runner import (  # noqa: E402
    BENCHMARK_FIXED_INDEX_REFRESH_INTERVAL_SECONDS,
    PlainRrfBenchmarkService,
    VectorRuntime,
    compute_tree_digest,
    embedding_telemetry_report,
    first_disallowed_public_key,
    observed_rss_bytes,
    optional_percentile_distribution,
    percentile,
    percentile_distribution,
    prepare_vector_runtime,
    serialized_search_payload_bytes,
    tree_size_bytes,
    validate_implementation_revision,
    vector_cache_metadata,
    vector_index_metadata,
    vector_provider_metadata,
)

REPORT_SCHEMA_ID = "llmwiki-nomiracl-ko-judged-pool-report-v1"
REPORT_SCHEMA_VERSION = "0.2.0"
RUN_MANIFEST_SCHEMA_ID = "llmwiki-nomiracl-ko-judged-pool-run-v1"
RUNNER_NAME = "nomiracl-ko-judged-pool-runner"
RUNNER_VERSION = "0.2.0"
RETRIEVAL_LIMIT = 100
BenchmarkSearchMode: TypeAlias = Literal["lexical", "vector", "plain-rrf", "hybrid"]
SEARCH_MODES: tuple[BenchmarkSearchMode, ...] = ("lexical", "vector", "plain-rrf", "hybrid")
VECTOR_REQUIRED_MODES = frozenset({"vector", "plain-rrf", "hybrid"})
PRIMARY_METRICS = ("nDCG@10", "Recall@100")
PRODUCT_SECONDARY_METRICS = ("Recall@5", "Recall@10", "MRR@10", "Precision@10", "MAP@100")
REPORT_REPO_ROOT = Path("benchmarks") / "verified_sources" / "reports"
SURFACE = "LlmWikiService.search(query, limit=100)"
PRIVATE_PUBLIC_REPORT_KEYS = {
    "bundle_dir",
    "cache_dir",
    "doc_text",
    "document_text",
    "host",
    "hostname",
    "local_paths",
    "output_path",
    "payloads",
    "per_query",
    "query",
    "query_text",
    "rows",
    "run_manifest",
    "run_manifest_path",
    "search_payloads",
    "text",
    "trace",
    "traces",
    "user",
    "username",
    "wiki_dir",
    "wiki_root",
}
REPORT_POLICY_ID = "nomiracl-ko-judged-pool-public-report-policy-v1"
EVALUATION_POOL_FIELDS = frozenset(
    {
        "document_count",
        "document_ids_sha256",
        "full_corpus",
        "non_relevant_sample_qrel_count",
        "non_relevant_sample_query_count",
        "non_relevant_sample_query_ids_sha256",
        "pool_sha256",
        "positive_qrel_count",
        "protocol",
        "qrel_count",
        "qrel_rows_sha256",
        "query_count",
        "relevant_qrel_count",
        "relevant_query_count",
        "source_splits",
    }
)
CALIBRATED_THRESHOLD_CLAIM_FIELDS = frozenset(
    {
        "abstention_threshold",
        "calibrated_abstention_threshold",
        "calibrated_score_threshold",
        "calibrated_threshold",
        "calibrated_threshold_claim",
        "calibrated_threshold_claim_allowed",
        "decision_threshold",
        "score_threshold",
        "threshold_value",
    }
)
CALIBRATED_THRESHOLD_CLAIM_PATTERN = re.compile(
    r"\bcalibrated[\s-]+(?:abstention[\s-]+|score[\s-]+)?thresholds?\b",
    re.IGNORECASE,
)
NO_CALIBRATED_THRESHOLD_CLAIM_VALUES = frozenset(
    {
        "diagnostic-only",
        "none",
        "not-allowed",
        "not-claimed",
        "not_claimed",
        "unsupported",
    }
)

ClockNs = Callable[[], int]
QrelsByQuery = dict[str, dict[str, float]]
VectorRuntimeFactory = Callable[..., VectorRuntime]
ProgressCallback = Callable[[Mapping[str, object]], None]


class SearchService(Protocol):
    def index(self) -> WikiIndex: ...

    def search(
        self,
        query: str,
        *,
        limit: int,
        mode: Any = "lexical",
    ) -> list[dict[str, Any]]: ...


class NomiraclKoRunnerError(RuntimeError):
    """Raised when the NoMIRACL-ko runner cannot proceed safely."""


@dataclass(frozen=True)
class QueryRow:
    query_id: str
    query: str
    answerability: Literal["answerable", "unanswerable"]
    source_split: Literal["dev.relevant", "dev.non_relevant"]
    evaluation_split: Literal["holdout", "smoke"]


@dataclass(frozen=True)
class NomiraclKoBundle:
    corpus_ids: frozenset[str]
    relevant_queries: tuple[QueryRow, ...]
    non_relevant_queries: tuple[QueryRow, ...]
    qrels_by_query: QrelsByQuery
    relevant_qrel_count: int
    positive_qrel_count: int
    non_relevant_qrel_count: int
    evaluation_pool: Mapping[str, Any]
    official_full_corpus: Mapping[str, Any]
    provenance: Mapping[str, Any]
    public_gate: validator.PublicReleaseGateResult

    @property
    def all_query_count(self) -> int:
        return len(self.relevant_queries) + len(self.non_relevant_queries)

    @property
    def all_qrel_count(self) -> int:
        return self.relevant_qrel_count + self.non_relevant_qrel_count


@dataclass(frozen=True)
class QueryMetrics:
    ndcg_at_10: float
    recall_at_5: float
    recall_at_10: float
    recall_at_100: float
    mrr_at_10: float
    precision_at_10: float
    map_at_100: float

    def value(self, name: str) -> float:
        if name == "nDCG@10":
            return self.ndcg_at_10
        if name == "Recall@5":
            return self.recall_at_5
        if name == "Recall@10":
            return self.recall_at_10
        if name == "Recall@100":
            return self.recall_at_100
        if name == "MRR@10":
            return self.mrr_at_10
        if name == "Precision@10":
            return self.precision_at_10
        if name == "MAP@100":
            return self.map_at_100
        raise NomiraclKoRunnerError(f"unknown metric name: {name}")


@dataclass(frozen=True)
class ModeTelemetry:
    search_latency_ms: tuple[float, ...]
    serialized_search_payload_bytes: tuple[int, ...]
    rss_observed_bytes: tuple[int, ...]
    lexical_search_latency_ms: tuple[float, ...]
    vector_search_latency_ms: tuple[float, ...]
    hybrid_fusion_latency_ms: tuple[float, ...]
    hybrid_candidate_depths: tuple[int, ...]
    hybrid_diagnostics: tuple[HybridDiagnostics, ...]


@dataclass(frozen=True)
class SharedSetupTelemetry:
    projection_index_build_ms: float
    lexical_corpus_build_or_load_ms: float | None
    vector_cold_build_or_load_ms: float | None
    rss_observed_bytes: tuple[int, ...]


@dataclass(frozen=True)
class NomiraclKoBenchmarkResult:
    report: dict[str, object]
    wiki_before_sha256: str
    wiki_after_sha256: str
    bundle_before_sha256: str
    bundle_after_sha256: str


def run_nomiracl_ko_benchmark(
    *,
    wiki_dir: Path,
    bundle_dir: Path,
    output_report: Path,
    implementation_revision: str,
    search_modes: Sequence[BenchmarkSearchMode] = SEARCH_MODES,
    relevant_query_limit: int | None = None,
    non_relevant_query_limit: int | None = None,
    vector_cache_root: Path | None = None,
    vector_model_cache_root: Path | None = None,
    vector_model_download: str = "never",
    run_manifest: Path | None = None,
    repo_root: Path = ROOT,
    vector_runtime_factory: VectorRuntimeFactory = prepare_vector_runtime,
    progress_callback: ProgressCallback | None = None,
    clock_ns: ClockNs = time.perf_counter_ns,
) -> NomiraclKoBenchmarkResult:
    """Run selected retrieval modes over NoMIRACL-ko with one warmed service."""
    modes = validate_search_modes(search_modes)
    revision = validate_implementation_revision(implementation_revision)
    emit_progress(progress_callback, "resolve_inputs")
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
        raise NomiraclKoRunnerError("output_report and run_manifest must be separate files")

    wiki_before = compute_tree_digest(resolved_wiki)
    bundle_before = compute_tree_digest(resolved_bundle)
    bundle = limit_nomiracl_bundle(
        load_nomiracl_ko_bundle(resolved_bundle),
        relevant_query_limit=relevant_query_limit,
        non_relevant_query_limit=non_relevant_query_limit,
    )
    emit_progress(
        progress_callback,
        "bundle_loaded",
        corpus_count=len(bundle.corpus_ids),
        full_corpus=False,
        non_relevant_query_count=len(bundle.non_relevant_queries),
        protocol=nomiracl_ko.EVALUATION_PROTOCOL,
        qrel_count=bundle.all_qrel_count,
        relevant_query_count=len(bundle.relevant_queries),
    )
    vector_runtime = (
        vector_runtime_factory(
            mode="vector",
            output_report=resolved_report,
            repo_root=repo_root,
            vector_cache_root=vector_cache_root,
            vector_model_cache_root=vector_model_cache_root,
            vector_model_download=vector_model_download,
        )
        if any(mode in VECTOR_REQUIRED_MODES for mode in modes)
        else None
    )
    emit_progress(
        progress_callback,
        "vector_runtime_ready" if vector_runtime is not None else "vector_runtime_not_used",
        vector_modes=any(mode in VECTOR_REQUIRED_MODES for mode in modes),
    )
    service = create_shared_service(resolved_wiki, vector_runtime=vector_runtime)
    plain_rrf_service = PlainRrfBenchmarkService(service, analyzer_profile=DEFAULT_ANALYZER_PROFILE)
    emit_progress(progress_callback, "shared_service_warmup_started")
    setup = warm_shared_service(service, modes=modes, clock_ns=clock_ns)
    emit_progress(
        progress_callback,
        "shared_service_warmup_finished",
        lexical_corpus_build_or_load_ms=setup.lexical_corpus_build_or_load_ms,
        projection_index_build_ms=setup.projection_index_build_ms,
        vector_cold_build_or_load_ms=setup.vector_cold_build_or_load_ms,
    )
    index = service.index()
    path_to_original_id = build_path_to_original_id_map(index, corpus_ids=bundle.corpus_ids)

    mode_results: dict[str, object] = {}
    rss_samples = list(setup.rss_observed_bytes)
    for mode in modes:
        runner_service: SearchService = plain_rrf_service if mode == "plain-rrf" else service
        emit_progress(
            progress_callback,
            "mode_started",
            mode=mode,
            query_count=bundle.all_query_count,
        )
        mode_report, mode_telemetry = run_mode_queries(
            runner_service,
            bundle,
            path_to_original_id=path_to_original_id,
            mode=mode,
            clock_ns=clock_ns,
        )
        mode_results[mode] = mode_report
        rss_samples.extend(mode_telemetry.rss_observed_bytes)
        mode_timings = cast(Mapping[str, object], mode_report["timings_ms"])
        warm_query = cast(Mapping[str, object], mode_timings["warm_query_end_to_end_ms"])
        emit_progress(
            progress_callback,
            "mode_finished",
            mode=mode,
            p50_ms=warm_query.get("p50"),
            p95_ms=warm_query.get("p95"),
        )

    if vector_runtime is not None:
        vector_runtime.embedding_telemetry.model_cache_bytes_after = tree_size_bytes(
            vector_runtime.model_cache_root
        )

    wiki_after = compute_tree_digest(resolved_wiki)
    bundle_after = compute_tree_digest(resolved_bundle)
    if wiki_after != wiki_before:
        raise NomiraclKoRunnerError("wiki tree mutated during NoMIRACL-ko retrieval run")
    if bundle_after != bundle_before:
        raise NomiraclKoRunnerError("bundle tree mutated during NoMIRACL-ko retrieval run")

    report = build_public_report(
        bundle,
        mode_results=mode_results,
        setup=setup,
        analyzer_profile=DEFAULT_ANALYZER_PROFILE,
        implementation_revision=revision,
        repo_root=repo_root,
        vector_runtime=vector_runtime,
        rss_peak_bytes=max(rss_samples) if rss_samples else None,
    )
    validate_public_report(report)
    _atomic_write_json(resolved_report, report)
    emit_progress(progress_callback, "report_written")
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
                implementation_revision=revision,
            ),
        )
        emit_progress(progress_callback, "run_manifest_written")

    return NomiraclKoBenchmarkResult(
        report=report,
        wiki_before_sha256=wiki_before,
        wiki_after_sha256=wiki_after,
        bundle_before_sha256=bundle_before,
        bundle_after_sha256=bundle_after,
    )


def validate_search_modes(values: Sequence[str]) -> tuple[BenchmarkSearchMode, ...]:
    if not values:
        raise NomiraclKoRunnerError("at least one search mode is required")
    modes: list[BenchmarkSearchMode] = []
    for value in values:
        normalized = value.strip().lower()
        if normalized not in SEARCH_MODES:
            raise NomiraclKoRunnerError("search mode must be lexical, vector, plain-rrf, or hybrid")
        mode = normalized
        if mode not in modes:
            modes.append(mode)
    return tuple(modes)


def emit_progress(
    callback: ProgressCallback | None,
    stage: str,
    **fields: object,
) -> None:
    if callback is None:
        return
    callback({"stage": stage, **fields})


def load_nomiracl_ko_bundle(bundle_dir: Path) -> NomiraclKoBundle:
    validation = validator.validate_bundle(bundle_dir)
    provenance = validation.provenance
    dataset = validator.require_string(provenance, "dataset", "provenance.json")
    if dataset != nomiracl_ko.DATASET_NAME:
        raise NomiraclKoRunnerError(
            f"bundle provenance dataset must be {nomiracl_ko.DATASET_NAME!r}"
        )
    adapter = validator.require_mapping(provenance, "adapter", "provenance.json")
    adapter_name = validator.require_string(adapter, "name", "provenance.json.adapter")
    if adapter_name != nomiracl_ko.ADAPTER_NAME:
        raise NomiraclKoRunnerError(
            f"bundle provenance adapter.name must be {nomiracl_ko.ADAPTER_NAME!r}"
        )
    source_revision = validator.require_string(provenance, "source_revision", "provenance.json")
    if source_revision != f"git:{nomiracl_ko.HF_REVISION}":
        raise NomiraclKoRunnerError("NoMIRACL-ko bundle must use the pinned HF revision")

    relevant_queries: list[QueryRow] = []
    non_relevant_queries: list[QueryRow] = []
    for line_number, record in validator.load_jsonl(bundle_dir / "queries.jsonl"):
        label = f"queries.jsonl:{line_number}"
        answerability = validator.require_string(record, "answerability", label)
        source_split = validator.require_string(record, "source_split", label)
        evaluation_split = validator.require_string(record, "evaluation_split", label)
        query = QueryRow(
            query_id=validator.require_string(record, "query_id", label),
            query=validator.require_string(record, "query", label),
            answerability=cast(Literal["answerable", "unanswerable"], answerability),
            source_split=cast(Literal["dev.relevant", "dev.non_relevant"], source_split),
            evaluation_split=cast(Literal["holdout", "smoke"], evaluation_split),
        )
        if answerability == "answerable":
            if query.source_split != "dev.relevant" or query.evaluation_split != "holdout":
                raise NomiraclKoRunnerError(
                    "answerable NoMIRACL-ko queries must be dev.relevant holdout rows"
                )
            relevant_queries.append(query)
        elif answerability == "unanswerable":
            if query.source_split != "dev.non_relevant" or query.evaluation_split != "smoke":
                raise NomiraclKoRunnerError(
                    "unanswerable NoMIRACL-ko diagnostics must be dev.non_relevant smoke rows"
                )
            non_relevant_queries.append(query)
        else:
            raise NomiraclKoRunnerError("NoMIRACL-ko runner expects answerable/unanswerable only")
    if not relevant_queries:
        raise NomiraclKoRunnerError("NoMIRACL-ko bundle must contain relevant queries")
    if not non_relevant_queries:
        raise NomiraclKoRunnerError("NoMIRACL-ko bundle must contain non-relevant queries")

    qrels_by_query = load_qrels_by_query(bundle_dir / "qrels.jsonl")
    relevant_qrel_count = sum(len(qrels_by_query.get(row.query_id, {})) for row in relevant_queries)
    positive_qrel_count = sum(
        1
        for row in relevant_queries
        for relevance in qrels_by_query.get(row.query_id, {}).values()
        if relevance > 0
    )
    non_relevant_qrel_count = sum(
        len(qrels_by_query.get(row.query_id, {})) for row in non_relevant_queries
    )
    missing_positive = [
        row.query_id
        for row in relevant_queries
        if not any(relevance > 0 for relevance in qrels_by_query.get(row.query_id, {}).values())
    ]
    if missing_positive:
        raise NomiraclKoRunnerError("every relevant NoMIRACL-ko query requires a positive qrel")
    if any(
        relevance > 0
        for row in non_relevant_queries
        for relevance in qrels_by_query.get(row.query_id, {}).values()
    ):
        raise NomiraclKoRunnerError("non-relevant NoMIRACL-ko diagnostic qrels must be zero")
    evaluation_pool, official_full_corpus = validate_nomiracl_evaluation_scope(
        provenance,
        bundle_dir=bundle_dir,
        corpus_ids=validation.corpus_ids,
        relevant_queries=relevant_queries,
        non_relevant_queries=non_relevant_queries,
        qrel_count=validation.qrel_count,
        relevant_qrel_count=relevant_qrel_count,
        positive_qrel_count=positive_qrel_count,
        non_relevant_qrel_count=non_relevant_qrel_count,
    )

    public_gate = validator.evaluate_public_release_gate(bundle_dir, mode="public-report")
    if not public_gate.passed:
        raise NomiraclKoRunnerError("bundle public-report gate failed")

    return NomiraclKoBundle(
        corpus_ids=validation.corpus_ids,
        relevant_queries=tuple(relevant_queries),
        non_relevant_queries=tuple(non_relevant_queries),
        qrels_by_query=qrels_by_query,
        relevant_qrel_count=relevant_qrel_count,
        positive_qrel_count=positive_qrel_count,
        non_relevant_qrel_count=non_relevant_qrel_count,
        evaluation_pool=evaluation_pool,
        official_full_corpus=official_full_corpus,
        provenance=provenance,
        public_gate=public_gate,
    )


def validate_nomiracl_evaluation_scope(
    provenance: Mapping[str, Any],
    *,
    bundle_dir: Path,
    corpus_ids: frozenset[str],
    relevant_queries: Sequence[QueryRow],
    non_relevant_queries: Sequence[QueryRow],
    qrel_count: int,
    relevant_qrel_count: int,
    positive_qrel_count: int,
    non_relevant_qrel_count: int,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    protocol = validator.require_string(provenance, "benchmark_protocol", "provenance.json")
    if protocol != nomiracl_ko.EVALUATION_PROTOCOL:
        raise NomiraclKoRunnerError("NoMIRACL-ko benchmark_protocol must be judged_pool")
    if provenance.get("full_corpus") is not False:
        raise NomiraclKoRunnerError("NoMIRACL-ko provenance.full_corpus must be false")

    official_full_corpus = validator.require_mapping(
        provenance,
        "official_full_corpus",
        "provenance.json",
    )
    require_official_full_corpus_counts(
        official_full_corpus,
        label="provenance.json.official_full_corpus",
    )

    evaluation_pool = validator.require_mapping(
        provenance,
        "evaluation_pool",
        "provenance.json",
    )
    validate_evaluation_pool_labels(
        evaluation_pool,
        label="provenance.json.evaluation_pool",
    )

    query_count = len(relevant_queries) + len(non_relevant_queries)
    non_relevant_query_count = len(non_relevant_queries)
    expected_ints = {
        "document_count": len(corpus_ids),
        "non_relevant_sample_qrel_count": non_relevant_qrel_count,
        "non_relevant_sample_query_count": non_relevant_query_count,
        "positive_qrel_count": positive_qrel_count,
        "qrel_count": qrel_count,
        "query_count": query_count,
        "relevant_qrel_count": relevant_qrel_count,
        "relevant_query_count": len(relevant_queries),
    }
    for field, expected in expected_ints.items():
        observed = validator.require_int(
            evaluation_pool,
            field,
            "provenance.json.evaluation_pool",
        )
        if observed != expected:
            raise NomiraclKoRunnerError(f"evaluation_pool.{field}={observed} expected {expected}")

    expected_docids = f"sha256:{nomiracl_ko.document_ids_sha256(tuple(corpus_ids))}"
    if (
        validator.require_string(
            evaluation_pool,
            "document_ids_sha256",
            "provenance.json.evaluation_pool",
        )
        != expected_docids
    ):
        raise NomiraclKoRunnerError("evaluation_pool.document_ids_sha256 mismatch")

    expected_qrels = f"sha256:{validator.canonical_text_file_sha256(bundle_dir / 'qrels.jsonl')}"
    if (
        validator.require_string(
            evaluation_pool,
            "qrel_rows_sha256",
            "provenance.json.evaluation_pool",
        )
        != expected_qrels
    ):
        raise NomiraclKoRunnerError("evaluation_pool.qrel_rows_sha256 mismatch")

    query_ids = tuple(row.query_id for row in (*relevant_queries, *non_relevant_queries))
    expected_pool = (
        f"sha256:{bundle_pool_sha256(corpus_ids, query_ids, bundle_dir / 'qrels.jsonl')}"
    )
    if (
        validator.require_string(
            evaluation_pool,
            "pool_sha256",
            "provenance.json.evaluation_pool",
        )
        != expected_pool
    ):
        raise NomiraclKoRunnerError("evaluation_pool.pool_sha256 mismatch")
    return evaluation_pool, official_full_corpus


def validate_evaluation_pool_labels(pool: Mapping[str, Any], *, label: str) -> None:
    require_exact_fields(pool, label, EVALUATION_POOL_FIELDS)
    if validator.require_string(pool, "protocol", label) != nomiracl_ko.EVALUATION_PROTOCOL:
        raise NomiraclKoRunnerError(f"{label}.protocol must be judged_pool")
    if pool.get("full_corpus") is not False:
        raise NomiraclKoRunnerError(f"{label}.full_corpus must be false")
    if pool.get("source_splits") != ["dev.relevant", "dev.non_relevant"]:
        raise NomiraclKoRunnerError(
            f"{label}.source_splits must be ['dev.relevant', 'dev.non_relevant']"
        )


def require_official_full_corpus_counts(counts: Mapping[str, Any], *, label: str) -> None:
    expected = nomiracl_ko.OFFICIAL_SOURCE_COUNTS.as_public_json()
    require_exact_fields(counts, label, frozenset(expected))
    for field, expected_value in expected.items():
        observed = validator.require_int(counts, field, label)
        if observed != expected_value:
            raise NomiraclKoRunnerError(
                f"official_full_corpus.{field}={observed} expected {expected_value}"
            )


def require_exact_fields(
    mapping: Mapping[str, Any],
    label: str,
    expected_fields: frozenset[str],
) -> None:
    missing = sorted(expected_fields - set(mapping))
    unknown = sorted(set(mapping) - expected_fields)
    if missing:
        raise NomiraclKoRunnerError(f"{label} missing required fields: {missing}")
    if unknown:
        raise NomiraclKoRunnerError(f"{label} unknown public report fields: {unknown}")


def bundle_pool_sha256(
    corpus_ids: frozenset[str],
    query_ids: Sequence[str],
    qrels_path: Path,
) -> str:
    payload = {
        "document_ids": sorted(corpus_ids),
        "full_corpus": False,
        "protocol": nomiracl_ko.EVALUATION_PROTOCOL,
        "qrels": bundle_qrel_rows(qrels_path),
        "query_ids": list(query_ids),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def bundle_qrel_rows(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line_number, record in validator.load_jsonl(path):
        label = f"{path.name}:{line_number}"
        relevance = record.get("relevance")
        validator.require_number(record, "relevance", label)
        rows.append(
            {
                "corpus_id": validator.require_string(record, "corpus_id", label),
                "query_id": validator.require_string(record, "query_id", label),
                "relevance": cast(int | float, relevance),
            }
        )
    return rows


def limit_nomiracl_bundle(
    bundle: NomiraclKoBundle,
    *,
    relevant_query_limit: int | None,
    non_relevant_query_limit: int | None,
) -> NomiraclKoBundle:
    relevant_queries = _limit_queries(bundle.relevant_queries, relevant_query_limit, "relevant")
    non_relevant_queries = _limit_queries(
        bundle.non_relevant_queries,
        non_relevant_query_limit,
        "non_relevant",
    )
    relevant_qrel_count = sum(len(bundle.qrels_by_query[row.query_id]) for row in relevant_queries)
    positive_qrel_count = sum(
        1
        for row in relevant_queries
        for relevance in bundle.qrels_by_query[row.query_id].values()
        if relevance > 0
    )
    non_relevant_qrel_count = sum(
        len(bundle.qrels_by_query[row.query_id]) for row in non_relevant_queries
    )
    return NomiraclKoBundle(
        corpus_ids=bundle.corpus_ids,
        relevant_queries=tuple(relevant_queries),
        non_relevant_queries=tuple(non_relevant_queries),
        qrels_by_query={
            query.query_id: bundle.qrels_by_query[query.query_id]
            for query in (*relevant_queries, *non_relevant_queries)
        },
        relevant_qrel_count=relevant_qrel_count,
        positive_qrel_count=positive_qrel_count,
        non_relevant_qrel_count=non_relevant_qrel_count,
        evaluation_pool=bundle.evaluation_pool,
        official_full_corpus=bundle.official_full_corpus,
        provenance=bundle.provenance,
        public_gate=bundle.public_gate,
    )


def _limit_queries(
    queries: Sequence[QueryRow],
    query_limit: int | None,
    label: str,
) -> tuple[QueryRow, ...]:
    if query_limit is None:
        return tuple(queries)
    if query_limit <= 0:
        raise NomiraclKoRunnerError(f"{label}_query_limit must be positive")
    return tuple(queries[:query_limit])


def create_shared_service(
    wiki_dir: Path,
    *,
    vector_runtime: VectorRuntime | None,
) -> LlmWikiService:
    if vector_runtime is None:
        return LlmWikiService(
            wiki_dir,
            refresh_interval_seconds=BENCHMARK_FIXED_INDEX_REFRESH_INTERVAL_SECONDS,
            analyzer_profile=DEFAULT_ANALYZER_PROFILE,
        )
    return LlmWikiService(
        wiki_dir,
        refresh_interval_seconds=BENCHMARK_FIXED_INDEX_REFRESH_INTERVAL_SECONDS,
        analyzer_profile=DEFAULT_ANALYZER_PROFILE,
        vector_config=VectorConfig(
            enabled=True,
            cache_dir=vector_runtime.cache_root,
            model_cache_dir=vector_runtime.model_cache_root,
            model_download="never",
        ),
        vector_provider=vector_runtime.provider,
    )


def warm_shared_service(
    service: LlmWikiService,
    *,
    modes: Sequence[BenchmarkSearchMode],
    clock_ns: ClockNs,
) -> SharedSetupTelemetry:
    rss_samples = [observed_rss_bytes()]
    start_ns = clock_ns()
    index = service.index()
    projection_index_build_ms = _elapsed_ms(start_ns, clock_ns())
    rss_samples.append(observed_rss_bytes())
    lexical_ms: float | None = None
    if any(mode in {"lexical", "plain-rrf", "hybrid"} for mode in modes):
        start_ns = clock_ns()
        service._index_views(index).search_corpus(False)  # noqa: SLF001
        lexical_ms = _elapsed_ms(start_ns, clock_ns())
        rss_samples.append(observed_rss_bytes())
    vector_ms: float | None = None
    if any(mode in VECTOR_REQUIRED_MODES for mode in modes):
        start_ns = clock_ns()
        service.search("", limit=1, mode="vector")
        vector_ms = _elapsed_ms(start_ns, clock_ns())
        rss_samples.append(observed_rss_bytes())
    return SharedSetupTelemetry(
        projection_index_build_ms=projection_index_build_ms,
        lexical_corpus_build_or_load_ms=lexical_ms,
        vector_cold_build_or_load_ms=vector_ms,
        rss_observed_bytes=tuple(rss_samples),
    )


def run_mode_queries(
    service: SearchService,
    bundle: NomiraclKoBundle,
    *,
    path_to_original_id: Mapping[str, str],
    mode: BenchmarkSearchMode,
    clock_ns: ClockNs,
) -> tuple[dict[str, object], ModeTelemetry]:
    relevant_metrics: list[QueryMetrics] = []
    positive_best_hit_scores: list[float] = []
    positive_missing_hit_count = 0
    non_relevant_top_scores: list[float] = []
    non_relevant_no_result_count = 0
    non_relevant_judged_hits_at_10: list[int] = []
    non_relevant_results_at_10: list[int] = []
    search_latencies_ms: list[float] = []
    payload_sizes: list[int] = []
    rss_samples: list[int] = [observed_rss_bytes()]

    with observe_retrieval_timings(clock_ns) as observer:
        for query in bundle.relevant_queries:
            results, latency_ms = call_service_search(
                service, query.query, mode=mode, clock_ns=clock_ns
            )
            search_latencies_ms.append(latency_ms)
            payload_sizes.append(serialized_search_payload_bytes(results))
            rss_samples.append(observed_rss_bytes())
            ranked = map_search_results_to_scored_corpus_ids(results, path_to_original_id)
            ranked_ids = [corpus_id for corpus_id, _score in ranked]
            qrels = bundle.qrels_by_query[query.query_id]
            relevant_metrics.append(compute_query_metrics(ranked_ids, qrels))
            positive_scores = [
                score for corpus_id, score in ranked if qrels.get(corpus_id, 0.0) > 0
            ]
            if positive_scores:
                positive_best_hit_scores.append(max(positive_scores))
            else:
                positive_missing_hit_count += 1

        for query in bundle.non_relevant_queries:
            results, latency_ms = call_service_search(
                service, query.query, mode=mode, clock_ns=clock_ns
            )
            search_latencies_ms.append(latency_ms)
            payload_sizes.append(serialized_search_payload_bytes(results))
            rss_samples.append(observed_rss_bytes())
            ranked = map_search_results_to_scored_corpus_ids(results, path_to_original_id)
            if ranked:
                non_relevant_top_scores.append(ranked[0][1])
            else:
                non_relevant_no_result_count += 1
            qrels = bundle.qrels_by_query[query.query_id]
            top10 = ranked[:10]
            non_relevant_results_at_10.append(len(top10))
            non_relevant_judged_hits_at_10.append(
                sum(1 for corpus_id, _score in top10 if corpus_id in qrels)
            )

    telemetry = ModeTelemetry(
        search_latency_ms=tuple(search_latencies_ms),
        serialized_search_payload_bytes=tuple(payload_sizes),
        rss_observed_bytes=tuple(rss_samples),
        lexical_search_latency_ms=tuple(observer.lexical_search_latency_ms),
        vector_search_latency_ms=tuple(observer.vector_search_latency_ms),
        hybrid_fusion_latency_ms=tuple(observer.hybrid_fusion_latency_ms),
        hybrid_candidate_depths=tuple(observer.hybrid_candidate_depths),
        hybrid_diagnostics=tuple(observer.hybrid_diagnostics),
    )
    mode_report = {
        "non_relevant_diagnostics": non_relevant_diagnostics_metadata(
            top_scores=non_relevant_top_scores,
            no_result_count=non_relevant_no_result_count,
            judged_hits_at_10=non_relevant_judged_hits_at_10,
            results_at_10=non_relevant_results_at_10,
            query_count=len(bundle.non_relevant_queries),
        ),
        "orientation_diagnostics": hybrid_orientation_diagnostics_metadata(
            telemetry.hybrid_diagnostics
        ),
        "positive_score_diagnostics": positive_score_diagnostics_metadata(
            positive_scores=positive_best_hit_scores,
            positive_missing_hit_count=positive_missing_hit_count,
            relevant_query_count=len(bundle.relevant_queries),
        ),
        "quality_metrics": macro_metrics(
            relevant_metrics,
            (*PRIMARY_METRICS, *PRODUCT_SECONDARY_METRICS),
        ),
        "relevant_query_count": len(bundle.relevant_queries),
        "retrieval_mode": mode,
        "score_separation": score_separation_metadata(
            positive_scores=positive_best_hit_scores,
            non_relevant_top_scores=non_relevant_top_scores,
        ),
        "serialized_payload_bytes": percentile_distribution(
            tuple(float(value) for value in telemetry.serialized_search_payload_bytes),
            unit="bytes",
            label="serialized per-query service.search top-100 result payload size",
        ),
        "timings_ms": mode_timings_metadata(telemetry),
    }
    return mode_report, telemetry


@contextlib.contextmanager
def observe_retrieval_timings(clock_ns: ClockNs) -> Any:
    import llmwiki_serve.service as service_module

    global search_corpus

    service_namespace = cast(Any, service_module)
    original_search_corpus = search_corpus
    original_service_search_corpus = service_namespace.search_corpus
    original_vector_search = service_namespace.search_vector_index
    original_hybrid_search = service_namespace.hybrid_search_results
    observer = _RetrievalTimingObserver(
        lexical_search_latency_ms=[],
        vector_search_latency_ms=[],
        hybrid_fusion_latency_ms=[],
        hybrid_candidate_depths=[],
        hybrid_diagnostics=[],
    )

    def timed_search_corpus(*args: Any, **kwargs: Any) -> Any:
        start_ns = clock_ns()
        try:
            return original_search_corpus(*args, **kwargs)
        finally:
            observer.lexical_search_latency_ms.append(_elapsed_ms(start_ns, clock_ns()))

    def timed_vector_search(*args: Any, **kwargs: Any) -> Any:
        start_ns = clock_ns()
        try:
            return original_vector_search(*args, **kwargs)
        finally:
            observer.vector_search_latency_ms.append(_elapsed_ms(start_ns, clock_ns()))

    def timed_hybrid_search(*args: Any, **kwargs: Any) -> Any:
        candidate_limit = kwargs.get("candidate_limit")
        if isinstance(candidate_limit, int):
            observer.hybrid_candidate_depths.append(candidate_limit)
        start_ns = clock_ns()
        try:
            kwargs.setdefault("diagnostics_sink", observer.hybrid_diagnostics.append)
            return original_hybrid_search(*args, **kwargs)
        finally:
            observer.hybrid_fusion_latency_ms.append(_elapsed_ms(start_ns, clock_ns()))

    search_corpus = timed_search_corpus
    service_namespace.search_corpus = timed_search_corpus
    service_namespace.search_vector_index = timed_vector_search
    service_namespace.hybrid_search_results = timed_hybrid_search
    try:
        yield observer
    finally:
        search_corpus = original_search_corpus
        service_namespace.search_corpus = original_service_search_corpus
        service_namespace.search_vector_index = original_vector_search
        service_namespace.hybrid_search_results = original_hybrid_search


@dataclass
class _RetrievalTimingObserver:
    lexical_search_latency_ms: list[float]
    vector_search_latency_ms: list[float]
    hybrid_fusion_latency_ms: list[float]
    hybrid_candidate_depths: list[int]
    hybrid_diagnostics: list[HybridDiagnostics]


def call_service_search(
    service: SearchService,
    query_text: str,
    *,
    mode: BenchmarkSearchMode,
    clock_ns: ClockNs,
) -> tuple[list[dict[str, Any]], float]:
    start_ns = clock_ns()
    if mode == "lexical":
        results = service.search(query_text, limit=RETRIEVAL_LIMIT, mode="lexical")
    else:
        results = service.search(query_text, limit=RETRIEVAL_LIMIT, mode=mode)
    return results, _elapsed_ms(start_ns, clock_ns())


def build_path_to_original_id_map(
    index: WikiIndex,
    *,
    corpus_ids: frozenset[str],
) -> dict[str, str]:
    path_to_original_id: dict[str, str] = {}
    original_id_to_path: dict[str, str] = {}
    for page in sorted(index.pages, key=lambda item: item.path):
        original_id = page.frontmatter.get("original_id")
        if not isinstance(original_id, str) or not original_id:
            raise NomiraclKoRunnerError("indexed NoMIRACL-ko page is missing original_id")
        if original_id not in corpus_ids:
            raise NomiraclKoRunnerError("indexed page original_id is not in bundle corpus")
        if page.path in path_to_original_id:
            raise NomiraclKoRunnerError("duplicate indexed page path mapping")
        existing_path = original_id_to_path.get(original_id)
        if existing_path is not None:
            raise NomiraclKoRunnerError("duplicate indexed page original_id mapping")
        path_to_original_id[page.path] = original_id
        original_id_to_path[original_id] = page.path
    missing = corpus_ids - frozenset(original_id_to_path)
    if missing:
        raise NomiraclKoRunnerError("bundle corpus_id is missing an indexed original_id mapping")
    return path_to_original_id


def map_search_results_to_scored_corpus_ids(
    results: Sequence[Mapping[str, Any]],
    path_to_original_id: Mapping[str, str],
) -> list[tuple[str, float]]:
    ranked: list[tuple[str, float]] = []
    seen_paths: set[str] = set()
    seen_corpus_ids: set[str] = set()
    for result in results:
        raw_path = result.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise NomiraclKoRunnerError("search result is missing a path")
        if raw_path in seen_paths:
            raise NomiraclKoRunnerError("duplicate search result path mapping")
        seen_paths.add(raw_path)
        corpus_id = path_to_original_id.get(raw_path)
        if corpus_id is None:
            raise NomiraclKoRunnerError("search result path has no original_id mapping")
        if corpus_id in seen_corpus_ids:
            raise NomiraclKoRunnerError("duplicate search result original_id mapping")
        score_value = result.get("score")
        if isinstance(score_value, bool) or not isinstance(score_value, int | float):
            raise NomiraclKoRunnerError("search result score must be numeric")
        score = float(score_value)
        if not math.isfinite(score):
            raise NomiraclKoRunnerError("search result score must be finite")
        seen_corpus_ids.add(corpus_id)
        ranked.append((corpus_id, score))
    return ranked


def load_qrels_by_query(path: Path) -> QrelsByQuery:
    qrels: QrelsByQuery = {}
    for line_number, record in validator.load_jsonl(path):
        label = f"{path.name}:{line_number}"
        query_id = validator.require_string(record, "query_id", label)
        corpus_id = validator.require_string(record, "corpus_id", label)
        relevance = validator.require_number(record, "relevance", label)
        qrels.setdefault(query_id, {})[corpus_id] = relevance
    return qrels


def compute_query_metrics(
    ranked_corpus_ids: Sequence[str],
    qrels: Mapping[str, float],
) -> QueryMetrics:
    if len(set(ranked_corpus_ids)) != len(ranked_corpus_ids):
        raise NomiraclKoRunnerError("ranked corpus ids contain duplicate mappings")
    positive_ids = {corpus_id for corpus_id, relevance in qrels.items() if relevance > 0}
    if not positive_ids:
        raise NomiraclKoRunnerError("query metrics require at least one positive qrel")
    return QueryMetrics(
        ndcg_at_10=ndcg_at_k(ranked_corpus_ids, qrels, 10),
        recall_at_5=recall_at_k(ranked_corpus_ids, positive_ids, 5),
        recall_at_10=recall_at_k(ranked_corpus_ids, positive_ids, 10),
        recall_at_100=recall_at_k(ranked_corpus_ids, positive_ids, 100),
        mrr_at_10=mrr_at_k(ranked_corpus_ids, positive_ids, 10),
        precision_at_10=precision_at_k(ranked_corpus_ids, positive_ids, 10),
        map_at_100=map_at_k(ranked_corpus_ids, positive_ids, 100),
    )


def ndcg_at_k(
    ranked_corpus_ids: Sequence[str],
    qrels: Mapping[str, float],
    cutoff: int,
) -> float:
    ideal_relevances = sorted((value for value in qrels.values() if value > 0), reverse=True)[
        :cutoff
    ]
    ideal = dcg_from_relevances(ideal_relevances)
    if ideal <= 0:
        return 0.0
    observed = [qrels.get(corpus_id, 0.0) for corpus_id in ranked_corpus_ids[:cutoff]]
    return dcg_from_relevances(observed) / ideal


def dcg_from_relevances(relevances: Sequence[float]) -> float:
    return sum(
        (2**relevance - 1) / math.log2(rank + 1) for rank, relevance in enumerate(relevances, 1)
    )


def recall_at_k(ranked_corpus_ids: Sequence[str], positive_ids: set[str], cutoff: int) -> float:
    if not positive_ids:
        return 0.0
    return sum(1 for corpus_id in ranked_corpus_ids[:cutoff] if corpus_id in positive_ids) / len(
        positive_ids
    )


def mrr_at_k(ranked_corpus_ids: Sequence[str], positive_ids: set[str], cutoff: int) -> float:
    for rank, corpus_id in enumerate(ranked_corpus_ids[:cutoff], 1):
        if corpus_id in positive_ids:
            return 1.0 / rank
    return 0.0


def precision_at_k(ranked_corpus_ids: Sequence[str], positive_ids: set[str], cutoff: int) -> float:
    if cutoff <= 0:
        raise NomiraclKoRunnerError("precision cutoff must be positive")
    return sum(1 for corpus_id in ranked_corpus_ids[:cutoff] if corpus_id in positive_ids) / cutoff


def map_at_k(ranked_corpus_ids: Sequence[str], positive_ids: set[str], cutoff: int) -> float:
    if not positive_ids:
        return 0.0
    hits = 0
    precision_sum = 0.0
    for rank, corpus_id in enumerate(ranked_corpus_ids[:cutoff], 1):
        if corpus_id not in positive_ids:
            continue
        hits += 1
        precision_sum += hits / rank
    return precision_sum / len(positive_ids)


def build_public_report(
    bundle: NomiraclKoBundle,
    *,
    mode_results: Mapping[str, object],
    setup: SharedSetupTelemetry,
    analyzer_profile: AnalyzerProfile,
    implementation_revision: str,
    repo_root: Path,
    vector_runtime: VectorRuntime | None,
    rss_peak_bytes: int | None,
) -> dict[str, object]:
    revision = validate_implementation_revision(implementation_revision)
    provenance = bundle.provenance
    checksums = validator.require_mapping(provenance, "checksums", "provenance.json")
    adapter = validator.require_mapping(provenance, "adapter", "provenance.json")
    report: dict[str, object] = {
        "adapter": {
            "name": validator.require_string(adapter, "name", "provenance.json.adapter"),
            "version": validator.require_string(adapter, "version", "provenance.json.adapter"),
        },
        "abstention_policy": abstention_policy_metadata(),
        "analyzer_profile": analyzer_profile,
        "benchmark_claim_scope": "judged-pool-only",
        "component_licenses": [
            dict(component)
            for component in validator.require_object_list(
                provenance.get("component_licenses"),
                "provenance.json.component_licenses",
            )
        ],
        "corpus_count": len(bundle.corpus_ids),
        "dataset": nomiracl_ko.DATASET_NAME,
        "embedding_telemetry": embedding_telemetry_report(vector_runtime),
        "evaluation_pool": dict(bundle.evaluation_pool),
        "full_corpus": False,
        "implementation": implementation_metadata(repo_root, revision),
        "implementation_revision": revision,
        "judged_query_count": len(bundle.relevant_queries),
        "korean_labels": korean_labels(),
        "languages_evaluated": [nomiracl_ko.LANGUAGE_CODE],
        "limitations": report_limitations(),
        "metric_definitions": metric_definitions(),
        "metric_groups": {
            "primary": list(PRIMARY_METRICS),
            "product_secondary": list(PRODUCT_SECONDARY_METRICS),
            "non_relevant_diagnostic": [
                "non_relevant_top_score",
                "judged_nonrelevant_hits_at_10",
                "results_returned_at_10",
                "score_separation",
            ],
        },
        "mode_results": dict(mode_results),
        "non_relevant_diagnostic_query_count": len(bundle.non_relevant_queries),
        "non_relevant_sample": {
            "query_count": len(bundle.non_relevant_queries),
            "rule": nomiracl_ko.non_relevant_sample_rule(),
            "source_split": "dev.non_relevant",
        },
        "normalized_bundle": {
            "checksums": {
                file_name: validator.require_string(
                    checksums,
                    file_name,
                    "provenance.json.checksums",
                )
                for file_name in validator.BUNDLE_JSONL_FILES
            },
            "evaluation_pool": dict(bundle.evaluation_pool),
            "full_corpus": False,
            "protocol": nomiracl_ko.EVALUATION_PROTOCOL,
            "schema_id": validator.SCHEMA_ID,
            "source_revision": validator.require_string(
                provenance,
                "source_revision",
                "provenance.json",
            ),
            "source_url": validator.require_string(provenance, "source_url", "provenance.json"),
        },
        "orientation_expectation": {
            "expected_hybrid_behavior": "plain_rrf_fallback",
            "reason": (
                "NoMIRACL judged-pool materialization does not synthesize "
                "LLMWiki orientation pages."
            ),
        },
        "official_full_corpus": dict(bundle.official_full_corpus),
        "os_family": platform.system() or "unknown",
        "package_version": LLMWIKI_SERVE_VERSION,
        "positive_qrel_count": bundle.positive_qrel_count,
        "protocol": nomiracl_ko.EVALUATION_PROTOCOL,
        "public_report_gate": bundle.public_gate.as_json(),
        "public_safety": {
            "contains_per_query_rows": False,
            "contains_query_or_document_text": False,
            "report_class": "sanitized-aggregate-only",
        },
        "qrel_count": bundle.all_qrel_count,
        "query_count": bundle.all_query_count,
        "relevant_qrel_count": bundle.relevant_qrel_count,
        "report_policy": report_policy_metadata(bundle.public_gate),
        "retrieval_limit": RETRIEVAL_LIMIT,
        "retrieval_schema": retrieval_schema_metadata(vector_runtime=vector_runtime),
        "runner": {"name": RUNNER_NAME, "version": RUNNER_VERSION},
        "runtime_environment": runtime_environment_metadata(vector_runtime),
        "schema_id": REPORT_SCHEMA_ID,
        "schema_version": REPORT_SCHEMA_VERSION,
        "setup_timings_ms": setup_timings_metadata(setup, rss_peak_bytes=rss_peak_bytes),
        "source_files": source_files_metadata(),
        "surface": SURFACE,
        "tested_size_envelope": tested_size_envelope_metadata(bundle),
        "unanswerable_diagnostics": unanswerable_diagnostics_metadata(bundle),
        "vector_cache": vector_cache_metadata(vector_runtime),
        "vector_index": vector_index_metadata(
            vector_runtime,
            cast(Any, _VectorIndexTelemetry(setup.vector_cold_build_or_load_ms)),
        ),
        "vector_provider": vector_provider_metadata(vector_runtime),
    }
    return report


@dataclass(frozen=True)
class _VectorIndexTelemetry:
    vector_cold_build_or_load_ms: float | None


def abstention_policy_metadata() -> dict[str, object]:
    return {
        "calibrated_threshold": None,
        "evaluated": False,
        "no_abstention_threshold_claim": True,
        "no_calibrated_threshold_claim": True,
        "reason": (
            "LlmWikiService.search returns ranked retrieval results; this runner does not "
            "convert scores into answer/abstain decisions."
        ),
        "supported": False,
    }


def report_policy_metadata(public_gate: validator.PublicReleaseGateResult) -> dict[str, object]:
    return {
        "allowed_public_material": "sanitized aggregate retrieval diagnostics only",
        "calibrated_threshold_claim": "none",
        "calibrated_threshold_claim_allowed": False,
        "excluded_material": [
            "raw query text",
            "raw document text",
            "per-query rows",
            "local run manifests",
            "abstention score cutoffs",
        ],
        "policy_id": REPORT_POLICY_ID,
        "public_report_gate_mode": public_gate.mode,
        "public_report_gate_passed": public_gate.passed,
    }


def tested_size_envelope_metadata(bundle: NomiraclKoBundle) -> dict[str, object]:
    return {
        "answerable_query_count": len(bundle.relevant_queries),
        "benchmark_claim_scope": "judged-pool-only",
        "corpus_count": len(bundle.corpus_ids),
        "full_corpus": False,
        "non_relevant_qrel_count": bundle.non_relevant_qrel_count,
        "positive_qrel_count": bundle.positive_qrel_count,
        "protocol": nomiracl_ko.EVALUATION_PROTOCOL,
        "qrel_count": bundle.all_qrel_count,
        "query_count": bundle.all_query_count,
        "relevant_qrel_count": bundle.relevant_qrel_count,
        "retrieval_limit": RETRIEVAL_LIMIT,
        "size_claim": "tested rows in this report only",
        "unanswerable_diagnostic_query_count": len(bundle.non_relevant_queries),
    }


def unanswerable_diagnostics_metadata(bundle: NomiraclKoBundle) -> dict[str, object]:
    return {
        "abstention_supported": False,
        "answerability_label": "unanswerable",
        "diagnostic_only": True,
        "metric_use": "non-relevant retrieval exposure and score separation only",
        "positive_qrel_count": 0,
        "qrel_count": bundle.non_relevant_qrel_count,
        "qrel_label": "official zero-relevance qrels only",
        "query_count": len(bundle.non_relevant_queries),
        "source_split": "dev.non_relevant",
    }


def korean_labels() -> dict[str, str]:
    return {
        "benchmark": "NoMIRACL 한국어 dev 판정 풀",
        "claim_scope": "판정된 문서 풀에서의 검색 동작 근거",
        "full_corpus_recall": "전체 MIRACL-ko 코퍼스 리콜 아님",
        "non_relevant_diagnostics": "비관련 query 점수 및 노출 진단",
        "unanswerable_diagnostics": "응답불가 query 진단",
        "orientation_pages": "hot/index/overview/quickstart 페이지 없음",
    }


def report_limitations() -> list[str]:
    return [
        "This is NoMIRACL Korean judged-pool evidence, not full MIRACL-ko corpus recall.",
        "No abstention threshold is defined or claimed from non-relevant top scores.",
        (
            "The materialized pool has no LLMWiki orientation pages, so hybrid should "
            "fall back to plain RRF when no safe related set exists."
        ),
        (
            "Scores are mode-specific and are not calibrated probabilities across "
            "lexical, vector, and RRF modes."
        ),
        "Latency measurements are hardware- and runtime-specific.",
    ]


def metric_definitions() -> dict[str, dict[str, object]]:
    return {
        "MAP@100": {
            "cutoff": 100,
            "formula": "average precision over relevance > 0 qrels in top 100; macro mean",
            "group": "product_secondary",
        },
        "MRR@10": {
            "cutoff": 10,
            "formula": "reciprocal rank of first relevance > 0 qrel in top 10; macro mean",
            "group": "product_secondary",
        },
        "Precision@10": {
            "cutoff": 10,
            "formula": "relevance > 0 hits in top 10 divided by 10; macro mean",
            "group": "product_secondary",
        },
        "Recall@10": {
            "cutoff": 10,
            "formula": "relevance > 0 qrels recovered in top 10 divided by positives; macro mean",
            "group": "product_secondary",
        },
        "Recall@100": {
            "cutoff": 100,
            "formula": "relevance > 0 qrels recovered in top 100 divided by positives; macro mean",
            "group": "primary",
        },
        "Recall@5": {
            "cutoff": 5,
            "formula": "relevance > 0 qrels recovered in top 5 divided by positives; macro mean",
            "group": "product_secondary",
        },
        "nDCG@10": {
            "cutoff": 10,
            "formula": "DCG@10 divided by ideal DCG@10 using numeric qrel relevance",
            "group": "primary",
        },
        "non_relevant_top_score": {
            "formula": "score of the top returned result for sampled dev.non_relevant queries",
            "group": "non_relevant_diagnostic",
        },
        "judged_nonrelevant_hits_at_10": {
            "cutoff": 10,
            "formula": "count of official zero-qrel documents retrieved in top 10",
            "group": "non_relevant_diagnostic",
        },
        "results_returned_at_10": {
            "cutoff": 10,
            "formula": (
                "returned result count capped at top 10 for sampled dev.non_relevant queries"
            ),
            "group": "non_relevant_diagnostic",
        },
        "score_separation": {
            "formula": (
                "descriptive differences between best positive-hit scores and "
                "non-relevant top scores"
            ),
            "group": "non_relevant_diagnostic",
        },
    }


def source_files_metadata() -> list[dict[str, object]]:
    return [
        {
            "path": item.relative_path,
            "sha256": f"sha256:{item.sha256}",
            "size_bytes": item.size_bytes,
        }
        for item in nomiracl_ko.OFFICIAL_SOURCE_FILES
    ]


def retrieval_schema_metadata(*, vector_runtime: VectorRuntime | None) -> dict[str, object]:
    vector_backend = (
        "exact-cosine-over-loaded-chunk-vectors" if vector_runtime is not None else "not-run"
    )
    return {
        "approximate_vector_index": False if vector_runtime is not None else None,
        "distance_metric": "cosine",
        "index_schema": VECTOR_INDEX_SCHEMA_ID,
        "retrieval_report_schema": REPORT_SCHEMA_ID,
        "retrieval_report_schema_version": REPORT_SCHEMA_VERSION,
        "text_schema": VECTOR_TEXT_SCHEMA_ID,
        "vector_cache_schema": VECTOR_CACHE_SCHEMA_VERSION,
        "vector_search_backend": vector_backend,
    }


def setup_timings_metadata(
    setup: SharedSetupTelemetry,
    *,
    rss_peak_bytes: int | None,
) -> dict[str, object]:
    return {
        "cold_projection_index_build_ms": _round_measurement(setup.projection_index_build_ms),
        "cold_vector_index_build_or_load_ms": (
            None
            if setup.vector_cold_build_or_load_ms is None
            else _round_measurement(setup.vector_cold_build_or_load_ms)
        ),
        "lexical_corpus_build_or_load_ms": (
            None
            if setup.lexical_corpus_build_or_load_ms is None
            else _round_measurement(setup.lexical_corpus_build_or_load_ms)
        ),
        "rss_observed_peak_bytes": rss_peak_bytes,
        "rss_peak_measurement": (
            "process RSS sampled before setup, after warmup, and after each query; "
            "not an OS high-water mark"
        ),
        "service_reuse": (
            "one LlmWikiService instance is created, indexed, and warmed; selected modes share "
            "the same projection, SearchCorpus cache, vector provider, and loaded vector index "
            "where applicable"
        ),
    }


def runtime_environment_metadata(vector_runtime: VectorRuntime | None) -> dict[str, object]:
    return {
        "cpu_count_logical": os.cpu_count() or 0,
        "machine_class": machine_class(),
        "os_family": platform.system() or "unknown",
        "os_release_family": platform.release().split("-", 1)[0] or "unknown",
        "provider_runtime": "local-cpu-fastembed" if vector_runtime is not None else "local-cpu",
        "python_version": platform.python_version(),
    }


def machine_class() -> str:
    machine = platform.machine().strip().lower()
    if machine in {"amd64", "x86_64"}:
        return "x86_64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return machine or "unknown"


def implementation_metadata(repo_root: Path, implementation_revision: str) -> dict[str, object]:
    revision = validate_implementation_revision(implementation_revision)
    head = git_output(repo_root, "rev-parse", "HEAD")
    status = git_output(repo_root, "status", "--porcelain=v1", "--untracked-files=all")
    diff = git_output(repo_root, "diff", "--binary", "HEAD")
    return {
        "git_head": f"git:{head}" if re.fullmatch(r"[a-f0-9]{40}", head) else "unknown",
        "requested_revision": revision,
        "requested_revision_matches_head": revision == f"git:{head}",
        "tracked_diff_sha256": f"sha256:{hashlib.sha256(diff.encode('utf-8')).hexdigest()}",
        "worktree_dirty": bool(status.strip()),
    }


def git_output(repo_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ("git", *args),
            cwd=repo_root,
            check=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


def mode_timings_metadata(telemetry: ModeTelemetry) -> dict[str, object]:
    return {
        "hybrid_candidate_depth": optional_percentile_distribution(
            tuple(float(value) for value in telemetry.hybrid_candidate_depths),
            unit="documents",
            label="bounded lexical/vector candidate depth used before hybrid fusion",
        ),
        "hybrid_fusion_ms": optional_percentile_distribution(
            telemetry.hybrid_fusion_latency_ms,
            unit="ms",
            label="observed hybrid RRF fusion latency",
        ),
        "lexical_search_ms": optional_percentile_distribution(
            telemetry.lexical_search_latency_ms,
            unit="ms",
            label="observed lexical search latency",
        ),
        "rss_observed_peak_bytes": max(telemetry.rss_observed_bytes)
        if telemetry.rss_observed_bytes
        else None,
        "vector_search_ms": optional_percentile_distribution(
            telemetry.vector_search_latency_ms,
            unit="ms",
            label="observed vector exact-cosine search latency including query embedding",
        ),
        "warm_query_end_to_end_ms": percentile_distribution(
            telemetry.search_latency_ms,
            unit="ms",
            label="per-query service.search top-100 result payload latency",
        ),
    }


def hybrid_orientation_diagnostics_metadata(
    diagnostics: Sequence[HybridDiagnostics],
) -> dict[str, object]:
    fallback_reasons: dict[str, int] = {}
    for item in diagnostics:
        if not item.fallback_reason:
            continue
        fallback_reasons[item.fallback_reason] = fallback_reasons.get(item.fallback_reason, 0) + 1
    return {
        "expected_no_orientation_fallback": True,
        "fallback_reasons": dict(sorted(fallback_reasons.items())),
        "observed_search_count": len(diagnostics),
        "orientation_seed_count": optional_percentile_distribution(
            tuple(float(item.orientation_seed_count) for item in diagnostics),
            unit="count",
            label="orientation seeds used per hybrid search",
        ),
        "orientation_seeded_count": sum(
            1 for item in diagnostics if item.mode == "orientation-seeded"
        ),
        "plain_rrf_fallback_count": sum(1 for item in diagnostics if item.mode == "plain-rrf"),
        "related_page_count": optional_percentile_distribution(
            tuple(float(item.related_page_count) for item in diagnostics),
            unit="count",
            label="safe related pages used per hybrid search",
        ),
    }


def positive_score_diagnostics_metadata(
    *,
    positive_scores: Sequence[float],
    positive_missing_hit_count: int,
    relevant_query_count: int,
) -> dict[str, object]:
    return {
        "best_positive_hit_score": optional_percentile_distribution(
            positive_scores,
            unit="mode-score",
            label="best score among retrieved relevance > 0 qrel documents per relevant query",
        ),
        "missing_positive_hit_count_at_100": positive_missing_hit_count,
        "query_count": relevant_query_count,
    }


def non_relevant_diagnostics_metadata(
    *,
    top_scores: Sequence[float],
    no_result_count: int,
    judged_hits_at_10: Sequence[int],
    results_at_10: Sequence[int],
    query_count: int,
) -> dict[str, object]:
    queries_with_judged_hit_at_10 = sum(1 for count in judged_hits_at_10 if count > 0)
    return {
        "abstention_supported": False,
        "answerability_label": "unanswerable",
        "diagnostic_scope": "retrieval-exposure-only",
        "judged_nonrelevant_hits_at_10": optional_percentile_distribution(
            tuple(float(value) for value in judged_hits_at_10),
            unit="count",
            label="official zero-qrel document hits in top 10 for sampled non-relevant queries",
        ),
        "no_abstention_threshold_claim": True,
        "no_calibrated_threshold_claim": True,
        "no_result_count_at_100": no_result_count,
        "query_count": query_count,
        "queries_with_judged_nonrelevant_hit_at_10": queries_with_judged_hit_at_10,
        "queries_with_judged_nonrelevant_hit_at_10_rate": _round_metric(
            queries_with_judged_hit_at_10 / query_count if query_count else 0.0
        ),
        "results_returned_at_10": optional_percentile_distribution(
            tuple(float(value) for value in results_at_10),
            unit="count",
            label="returned result count capped at top 10 for sampled non-relevant queries",
        ),
        "top_score": optional_percentile_distribution(
            top_scores,
            unit="mode-score",
            label="top result score for sampled non-relevant queries",
        ),
    }


def score_separation_metadata(
    *,
    positive_scores: Sequence[float],
    non_relevant_top_scores: Sequence[float],
) -> dict[str, object]:
    if not positive_scores or not non_relevant_top_scores:
        return {
            "available": False,
            "diagnostic_only": True,
            "no_calibrated_threshold_claim": True,
            "no_threshold_claim": True,
            "p50_positive_minus_p50_non_relevant": None,
            "p50_positive_minus_p95_non_relevant": None,
            "p95_positive_minus_p95_non_relevant": None,
        }
    positive_p50 = percentile(positive_scores, 50.0)
    positive_p95 = percentile(positive_scores, 95.0)
    non_relevant_p50 = percentile(non_relevant_top_scores, 50.0)
    non_relevant_p95 = percentile(non_relevant_top_scores, 95.0)
    return {
        "available": True,
        "diagnostic_only": True,
        "no_calibrated_threshold_claim": True,
        "no_threshold_claim": True,
        "p50_positive_minus_p50_non_relevant": _round_measurement(positive_p50 - non_relevant_p50),
        "p50_positive_minus_p95_non_relevant": _round_measurement(positive_p50 - non_relevant_p95),
        "p95_positive_minus_p95_non_relevant": _round_measurement(positive_p95 - non_relevant_p95),
    }


def macro_metrics(
    query_metrics: Sequence[QueryMetrics],
    names: Sequence[str],
) -> dict[str, float]:
    if not query_metrics:
        raise NomiraclKoRunnerError("aggregate metrics require at least one relevant query")
    return {
        name: _round_metric(
            sum(metrics.value(name) for metrics in query_metrics) / len(query_metrics)
        )
        for name in names
    }


def first_calibrated_threshold_claim(value: object, *, path: str = "$") -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            normalized_key = normalize_policy_key(key_text)
            if (
                not normalized_key.startswith("no_")
                and normalized_key in CALIBRATED_THRESHOLD_CLAIM_FIELDS
                and not is_no_calibrated_threshold_claim_value(item)
            ):
                return f"{path}.{key_text}"
            nested = first_calibrated_threshold_claim(item, path=f"{path}.{key_text}")
            if nested is not None:
                return nested
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            nested = first_calibrated_threshold_claim(item, path=f"{path}[{index}]")
            if nested is not None:
                return nested
    if isinstance(value, str) and CALIBRATED_THRESHOLD_CLAIM_PATTERN.search(value):
        return path
    return None


def normalize_policy_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def is_no_calibrated_threshold_claim_value(value: object) -> bool:
    if value is None or value is False:
        return True
    if isinstance(value, str):
        return normalize_policy_key(value) in {
            normalize_policy_key(item) for item in NO_CALIBRATED_THRESHOLD_CLAIM_VALUES
        }
    return False


def validate_abstention_policy(policy: Mapping[str, object]) -> None:
    require_exact_fields(
        policy,
        "NoMIRACL report.abstention_policy",
        frozenset(
            {
                "calibrated_threshold",
                "evaluated",
                "no_abstention_threshold_claim",
                "no_calibrated_threshold_claim",
                "reason",
                "supported",
            }
        ),
    )
    if policy.get("supported") is not False or policy.get("evaluated") is not False:
        raise NomiraclKoRunnerError("abstention_policy must mark abstention unsupported")
    if policy.get("calibrated_threshold") is not None:
        raise NomiraclKoRunnerError("abstention_policy must not publish a calibrated threshold")
    if policy.get("no_abstention_threshold_claim") is not True:
        raise NomiraclKoRunnerError("abstention_policy must reject abstention threshold claims")
    if policy.get("no_calibrated_threshold_claim") is not True:
        raise NomiraclKoRunnerError("abstention_policy must reject calibrated threshold claims")
    validator.require_string(policy, "reason", "NoMIRACL report.abstention_policy")


def validate_report_policy(
    policy: Mapping[str, object],
    *,
    public_report_gate: Mapping[str, object],
) -> None:
    require_exact_fields(
        policy,
        "NoMIRACL report.report_policy",
        frozenset(
            {
                "allowed_public_material",
                "calibrated_threshold_claim",
                "calibrated_threshold_claim_allowed",
                "excluded_material",
                "policy_id",
                "public_report_gate_mode",
                "public_report_gate_passed",
            }
        ),
    )
    if policy.get("policy_id") != REPORT_POLICY_ID:
        raise NomiraclKoRunnerError("report_policy.policy_id mismatch")
    if policy.get("public_report_gate_mode") != "public-report":
        raise NomiraclKoRunnerError("report_policy.public_report_gate_mode must be public-report")
    if policy.get("public_report_gate_passed") is not True:
        raise NomiraclKoRunnerError("report_policy.public_report_gate_passed must be true")
    if policy.get("calibrated_threshold_claim") != "none":
        raise NomiraclKoRunnerError("report_policy must not allow calibrated threshold claims")
    if policy.get("calibrated_threshold_claim_allowed") is not False:
        raise NomiraclKoRunnerError("report_policy must reject calibrated threshold claims")
    if public_report_gate.get("mode") != "public-report":
        raise NomiraclKoRunnerError("public_report_gate.mode must be public-report")
    if public_report_gate.get("passed") is not True:
        raise NomiraclKoRunnerError("public_report_gate.passed must be true")
    blockers = public_report_gate.get("blockers")
    if blockers != []:
        raise NomiraclKoRunnerError("public_report_gate.blockers must be empty")
    excluded = policy.get("excluded_material")
    if not isinstance(excluded, list) or not all(isinstance(item, str) for item in excluded):
        raise NomiraclKoRunnerError("report_policy.excluded_material must be a string list")


def validate_tested_size_envelope(
    envelope: Mapping[str, object],
    *,
    report: Mapping[str, object],
) -> None:
    require_exact_fields(
        envelope,
        "NoMIRACL report.tested_size_envelope",
        frozenset(
            {
                "answerable_query_count",
                "benchmark_claim_scope",
                "corpus_count",
                "full_corpus",
                "non_relevant_qrel_count",
                "positive_qrel_count",
                "protocol",
                "qrel_count",
                "query_count",
                "relevant_qrel_count",
                "retrieval_limit",
                "size_claim",
                "unanswerable_diagnostic_query_count",
            }
        ),
    )
    expected_pairs = {
        "benchmark_claim_scope": "judged-pool-only",
        "corpus_count": report.get("corpus_count"),
        "full_corpus": False,
        "positive_qrel_count": report.get("positive_qrel_count"),
        "protocol": nomiracl_ko.EVALUATION_PROTOCOL,
        "qrel_count": report.get("qrel_count"),
        "query_count": report.get("query_count"),
        "relevant_qrel_count": report.get("relevant_qrel_count"),
        "retrieval_limit": RETRIEVAL_LIMIT,
        "unanswerable_diagnostic_query_count": report.get("non_relevant_diagnostic_query_count"),
    }
    for field, expected in expected_pairs.items():
        if envelope.get(field) != expected:
            raise NomiraclKoRunnerError(f"tested_size_envelope.{field} mismatch")
    if envelope.get("answerable_query_count") != report.get("judged_query_count"):
        raise NomiraclKoRunnerError("tested_size_envelope.answerable_query_count mismatch")
    if envelope.get("non_relevant_qrel_count") != cast(int, report.get("qrel_count")) - cast(
        int, report.get("relevant_qrel_count")
    ):
        raise NomiraclKoRunnerError("tested_size_envelope.non_relevant_qrel_count mismatch")
    if envelope.get("size_claim") != "tested rows in this report only":
        raise NomiraclKoRunnerError("tested_size_envelope.size_claim mismatch")


def validate_unanswerable_diagnostics(
    diagnostics: Mapping[str, object],
    *,
    report: Mapping[str, object],
) -> None:
    require_exact_fields(
        diagnostics,
        "NoMIRACL report.unanswerable_diagnostics",
        frozenset(
            {
                "abstention_supported",
                "answerability_label",
                "diagnostic_only",
                "metric_use",
                "positive_qrel_count",
                "qrel_count",
                "qrel_label",
                "query_count",
                "source_split",
            }
        ),
    )
    expected = {
        "abstention_supported": False,
        "answerability_label": "unanswerable",
        "diagnostic_only": True,
        "positive_qrel_count": 0,
        "query_count": report.get("non_relevant_diagnostic_query_count"),
        "source_split": "dev.non_relevant",
    }
    for field, expected_value in expected.items():
        if diagnostics.get(field) != expected_value:
            raise NomiraclKoRunnerError(f"unanswerable_diagnostics.{field} mismatch")
    envelope = cast(Mapping[str, object], report["tested_size_envelope"])
    if diagnostics.get("qrel_count") != envelope.get("non_relevant_qrel_count"):
        raise NomiraclKoRunnerError("unanswerable_diagnostics.qrel_count mismatch")
    validator.require_string(
        diagnostics,
        "metric_use",
        "NoMIRACL report.unanswerable_diagnostics",
    )
    validator.require_string(
        diagnostics,
        "qrel_label",
        "NoMIRACL report.unanswerable_diagnostics",
    )


def validate_retrieval_schema(schema: Mapping[str, object]) -> None:
    require_exact_fields(
        schema,
        "NoMIRACL report.retrieval_schema",
        frozenset(
            {
                "approximate_vector_index",
                "distance_metric",
                "index_schema",
                "retrieval_report_schema",
                "retrieval_report_schema_version",
                "text_schema",
                "vector_cache_schema",
                "vector_search_backend",
            }
        ),
    )
    expected_pairs = {
        "distance_metric": "cosine",
        "index_schema": VECTOR_INDEX_SCHEMA_ID,
        "retrieval_report_schema": REPORT_SCHEMA_ID,
        "retrieval_report_schema_version": REPORT_SCHEMA_VERSION,
        "text_schema": VECTOR_TEXT_SCHEMA_ID,
        "vector_cache_schema": VECTOR_CACHE_SCHEMA_VERSION,
    }
    for field, expected in expected_pairs.items():
        if schema.get(field) != expected:
            raise NomiraclKoRunnerError(f"retrieval_schema.{field} mismatch")
    if schema.get("vector_search_backend") not in {
        "exact-cosine-over-loaded-chunk-vectors",
        "not-run",
    }:
        raise NomiraclKoRunnerError("retrieval_schema.vector_search_backend mismatch")
    if schema.get("vector_search_backend") == "exact-cosine-over-loaded-chunk-vectors":
        if schema.get("approximate_vector_index") is not False:
            raise NomiraclKoRunnerError("retrieval_schema must mark vector search exact")
    elif schema.get("approximate_vector_index") is not None:
        raise NomiraclKoRunnerError("retrieval_schema approximate_vector_index must be null")


def validate_public_report(report: Mapping[str, object]) -> None:
    disallowed = first_disallowed_nomiracl_public_key(report)
    if disallowed is not None:
        raise NomiraclKoRunnerError(f"public report contains private field {disallowed!r}")
    calibrated_threshold_claim = first_calibrated_threshold_claim(report)
    if calibrated_threshold_claim is not None:
        raise NomiraclKoRunnerError(
            f"public report contains calibrated threshold claim at {calibrated_threshold_claim}"
        )
    if report.get("schema_id") != REPORT_SCHEMA_ID:
        raise NomiraclKoRunnerError(f"public report schema_id must be {REPORT_SCHEMA_ID!r}")
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise NomiraclKoRunnerError(
            f"public report schema_version must be {REPORT_SCHEMA_VERSION!r}"
        )
    required = {
        "abstention_policy",
        "adapter",
        "analyzer_profile",
        "benchmark_claim_scope",
        "component_licenses",
        "corpus_count",
        "dataset",
        "embedding_telemetry",
        "evaluation_pool",
        "full_corpus",
        "implementation",
        "implementation_revision",
        "judged_query_count",
        "korean_labels",
        "languages_evaluated",
        "limitations",
        "metric_definitions",
        "metric_groups",
        "mode_results",
        "non_relevant_diagnostic_query_count",
        "non_relevant_sample",
        "normalized_bundle",
        "official_full_corpus",
        "orientation_expectation",
        "os_family",
        "package_version",
        "positive_qrel_count",
        "protocol",
        "public_report_gate",
        "public_safety",
        "qrel_count",
        "query_count",
        "relevant_qrel_count",
        "report_policy",
        "retrieval_limit",
        "retrieval_schema",
        "runner",
        "runtime_environment",
        "schema_id",
        "schema_version",
        "setup_timings_ms",
        "source_files",
        "surface",
        "tested_size_envelope",
        "unanswerable_diagnostics",
        "vector_cache",
        "vector_index",
        "vector_provider",
    }
    missing = sorted(required - set(report))
    unknown = sorted(set(report) - required)
    if missing:
        raise NomiraclKoRunnerError(f"missing public report fields: {missing}")
    if unknown:
        raise NomiraclKoRunnerError(f"unknown public report fields: {unknown}")
    validate_implementation_revision(
        validator.require_string(report, "implementation_revision", "NoMIRACL report")
    )
    if report.get("dataset") != nomiracl_ko.DATASET_NAME:
        raise NomiraclKoRunnerError("dataset must be nomiracl-ko")
    if report.get("benchmark_claim_scope") != "judged-pool-only":
        raise NomiraclKoRunnerError("benchmark_claim_scope must be judged-pool-only")
    if report.get("protocol") != nomiracl_ko.EVALUATION_PROTOCOL:
        raise NomiraclKoRunnerError("protocol must be judged_pool")
    if report.get("full_corpus") is not False:
        raise NomiraclKoRunnerError("full_corpus must be false")
    if report.get("languages_evaluated") != [nomiracl_ko.LANGUAGE_CODE]:
        raise NomiraclKoRunnerError("languages_evaluated must be ['ko']")
    official_full_corpus = report.get("official_full_corpus")
    if not isinstance(official_full_corpus, dict):
        raise NomiraclKoRunnerError("official_full_corpus must be an object")
    require_official_full_corpus_counts(
        cast(Mapping[str, Any], official_full_corpus),
        label="NoMIRACL report.official_full_corpus",
    )
    evaluation_pool = report.get("evaluation_pool")
    if not isinstance(evaluation_pool, dict):
        raise NomiraclKoRunnerError("evaluation_pool must be an object")
    validate_evaluation_pool_labels(
        cast(Mapping[str, Any], evaluation_pool),
        label="NoMIRACL report.evaluation_pool",
    )
    if evaluation_pool.get("document_count") != report.get("corpus_count"):
        raise NomiraclKoRunnerError("evaluation_pool.document_count must match corpus_count")
    abstention_policy = report.get("abstention_policy")
    if not isinstance(abstention_policy, dict):
        raise NomiraclKoRunnerError("abstention_policy must be an object")
    public_report_gate = report.get("public_report_gate")
    if not isinstance(public_report_gate, dict):
        raise NomiraclKoRunnerError("public_report_gate must be an object")
    report_policy = report.get("report_policy")
    if not isinstance(report_policy, dict):
        raise NomiraclKoRunnerError("report_policy must be an object")
    tested_size_envelope = report.get("tested_size_envelope")
    if not isinstance(tested_size_envelope, dict):
        raise NomiraclKoRunnerError("tested_size_envelope must be an object")
    unanswerable_diagnostics = report.get("unanswerable_diagnostics")
    if not isinstance(unanswerable_diagnostics, dict):
        raise NomiraclKoRunnerError("unanswerable_diagnostics must be an object")
    validate_abstention_policy(cast(Mapping[str, object], abstention_policy))
    validate_report_policy(
        cast(Mapping[str, object], report_policy),
        public_report_gate=cast(Mapping[str, object], public_report_gate),
    )
    validate_tested_size_envelope(
        cast(Mapping[str, object], tested_size_envelope),
        report=report,
    )
    validate_unanswerable_diagnostics(
        cast(Mapping[str, object], unanswerable_diagnostics),
        report=report,
    )
    if not isinstance(report.get("embedding_telemetry"), dict):
        raise NomiraclKoRunnerError("embedding_telemetry must be an object")
    retrieval_schema = report.get("retrieval_schema")
    if not isinstance(retrieval_schema, dict):
        raise NomiraclKoRunnerError("retrieval_schema must be an object")
    validate_retrieval_schema(cast(Mapping[str, object], retrieval_schema))
    limitations = report.get("limitations")
    if not isinstance(limitations, list) or not all(isinstance(item, str) for item in limitations):
        raise NomiraclKoRunnerError("limitations must be a string list")
    required_limit_markers = (
        "not full MIRACL-ko corpus recall",
        "No abstention threshold",
        "no LLMWiki orientation pages",
    )
    limitations_text = "\n".join(limitations)
    for marker in required_limit_markers:
        if marker not in limitations_text:
            raise NomiraclKoRunnerError(f"limitations must mention: {marker}")
    mode_results = report.get("mode_results")
    if not isinstance(mode_results, dict) or not mode_results:
        raise NomiraclKoRunnerError("mode_results must be a non-empty object")
    for mode, mode_report in mode_results.items():
        validate_search_modes([str(mode)])
        validate_mode_report(cast(Mapping[str, object], mode_report), mode=str(mode))
    public_safety = cast(Mapping[str, object], report["public_safety"])
    if public_safety.get("contains_query_or_document_text") is not False:
        raise NomiraclKoRunnerError("public_safety must mark raw text absent")
    if public_safety.get("contains_per_query_rows") is not False:
        raise NomiraclKoRunnerError("public_safety must mark per-query rows absent")
    if public_safety.get("report_class") != "sanitized-aggregate-only":
        raise NomiraclKoRunnerError("public_safety.report_class must be sanitized-aggregate-only")
    validator.assert_public_safe_value(dict(report), "NoMIRACL-ko public report")


def validate_mode_report(report: Mapping[str, object], *, mode: str) -> None:
    require_exact_fields(
        report,
        f"NoMIRACL mode report {mode}",
        frozenset(
            {
                "non_relevant_diagnostics",
                "orientation_diagnostics",
                "positive_score_diagnostics",
                "quality_metrics",
                "relevant_query_count",
                "retrieval_mode",
                "score_separation",
                "serialized_payload_bytes",
                "timings_ms",
            }
        ),
    )
    if report.get("retrieval_mode") != mode:
        raise NomiraclKoRunnerError("mode report retrieval_mode mismatch")
    metrics = report.get("quality_metrics")
    if not isinstance(metrics, dict):
        raise NomiraclKoRunnerError("mode quality_metrics must be an object")
    for metric in (*PRIMARY_METRICS, *PRODUCT_SECONDARY_METRICS):
        value = metrics.get(metric)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise NomiraclKoRunnerError(f"mode metric {metric} must be numeric")
    diagnostics = report.get("non_relevant_diagnostics")
    if not isinstance(diagnostics, dict):
        raise NomiraclKoRunnerError("non_relevant_diagnostics must be an object")
    validate_non_relevant_mode_diagnostics(cast(Mapping[str, object], diagnostics))
    if diagnostics.get("no_abstention_threshold_claim") is not True:
        raise NomiraclKoRunnerError("non_relevant diagnostics must reject threshold claims")
    separation = report.get("score_separation")
    if not isinstance(separation, dict):
        raise NomiraclKoRunnerError("score_separation must be an object")
    validate_score_separation(cast(Mapping[str, object], separation))
    if separation.get("no_threshold_claim") is not True:
        raise NomiraclKoRunnerError("score_separation must reject threshold claims")
    orientation = report.get("orientation_diagnostics")
    if not isinstance(orientation, dict):
        raise NomiraclKoRunnerError("orientation_diagnostics must be an object")
    if mode == "hybrid":
        if orientation.get("orientation_seeded_count") != 0:
            raise NomiraclKoRunnerError("NoMIRACL-ko hybrid should not use orientation seeds")
        if orientation.get("plain_rrf_fallback_count") != orientation.get("observed_search_count"):
            raise NomiraclKoRunnerError("NoMIRACL-ko hybrid should fall back for every query")


def validate_non_relevant_mode_diagnostics(diagnostics: Mapping[str, object]) -> None:
    require_exact_fields(
        diagnostics,
        "NoMIRACL mode non_relevant_diagnostics",
        frozenset(
            {
                "abstention_supported",
                "answerability_label",
                "diagnostic_scope",
                "judged_nonrelevant_hits_at_10",
                "no_abstention_threshold_claim",
                "no_calibrated_threshold_claim",
                "no_result_count_at_100",
                "queries_with_judged_nonrelevant_hit_at_10",
                "queries_with_judged_nonrelevant_hit_at_10_rate",
                "query_count",
                "results_returned_at_10",
                "top_score",
            }
        ),
    )
    expected = {
        "abstention_supported": False,
        "answerability_label": "unanswerable",
        "diagnostic_scope": "retrieval-exposure-only",
        "no_abstention_threshold_claim": True,
        "no_calibrated_threshold_claim": True,
    }
    for field, expected_value in expected.items():
        if diagnostics.get(field) != expected_value:
            raise NomiraclKoRunnerError(f"non_relevant_diagnostics.{field} mismatch")
    for field in (
        "judged_nonrelevant_hits_at_10",
        "results_returned_at_10",
        "top_score",
    ):
        if not isinstance(diagnostics.get(field), dict):
            raise NomiraclKoRunnerError(f"non_relevant_diagnostics.{field} must be an object")


def validate_score_separation(separation: Mapping[str, object]) -> None:
    require_exact_fields(
        separation,
        "NoMIRACL mode score_separation",
        frozenset(
            {
                "available",
                "diagnostic_only",
                "no_calibrated_threshold_claim",
                "no_threshold_claim",
                "p50_positive_minus_p50_non_relevant",
                "p50_positive_minus_p95_non_relevant",
                "p95_positive_minus_p95_non_relevant",
            }
        ),
    )
    if separation.get("diagnostic_only") is not True:
        raise NomiraclKoRunnerError("score_separation must be diagnostic-only")
    if separation.get("no_calibrated_threshold_claim") is not True:
        raise NomiraclKoRunnerError("score_separation must reject calibrated threshold claims")


def first_disallowed_nomiracl_public_key(value: object) -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in PRIVATE_PUBLIC_REPORT_KEYS:
                return str(key)
            nested = first_disallowed_nomiracl_public_key(item)
            if nested is not None:
                return nested
    if isinstance(value, list | tuple):
        for item in value:
            nested = first_disallowed_nomiracl_public_key(item)
            if nested is not None:
                return nested
    return first_disallowed_public_key(value)


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
    implementation_revision: str,
) -> dict[str, object]:
    revision = validate_implementation_revision(implementation_revision)
    return {
        "benchmark_configuration": {
            "implementation_revision": revision,
            "retrieval_modes": sorted(cast(Mapping[str, object], report["mode_results"])),
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
            "dataset": report["dataset"],
            "evaluation_pool": report["evaluation_pool"],
            "full_corpus": report["full_corpus"],
            "implementation_revision": report["implementation_revision"],
            "mode_results": {
                mode: cast(Mapping[str, object], result)["quality_metrics"]
                for mode, result in cast(Mapping[str, object], report["mode_results"]).items()
            },
            "protocol": report["protocol"],
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


def _resolve_public_report_path(
    path: Path,
    *,
    repo_root: Path,
    wiki_dir: Path,
    bundle_dir: Path,
) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    if resolved.name == "run-manifest.json":
        raise NomiraclKoRunnerError("output_report must not be named run-manifest.json")
    if resolved.exists() and resolved.is_dir():
        raise NomiraclKoRunnerError("output_report must be a file path")
    if _is_same_or_nested(resolved, wiki_dir) or _is_same_or_nested(resolved, bundle_dir):
        raise NomiraclKoRunnerError("output_report must be outside wiki_dir and bundle_dir")
    resolved_repo = repo_root.expanduser().resolve(strict=False)
    try:
        relative = resolved.relative_to(resolved_repo)
    except ValueError:
        return resolved
    allowed_parts = REPORT_REPO_ROOT.parts
    if (
        len(relative.parts) <= len(allowed_parts)
        or relative.parts[: len(allowed_parts)] != allowed_parts
    ):
        raise NomiraclKoRunnerError(
            f"output_report inside the repository must stay under {REPORT_REPO_ROOT.as_posix()}/"
        )
    return resolved


def _resolve_run_manifest_path(
    path: Path,
    *,
    repo_root: Path,
    wiki_dir: Path,
    bundle_dir: Path,
) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    if _is_same_or_nested(resolved, wiki_dir) or _is_same_or_nested(resolved, bundle_dir):
        raise NomiraclKoRunnerError("run_manifest must be outside wiki_dir and bundle_dir")
    try:
        validator.validate_local_run_manifest(resolved, repo_root=repo_root)
    except validator.BundleValidationError as error:
        raise NomiraclKoRunnerError(str(error)) from error
    resolved_repo = repo_root.expanduser().resolve(strict=False)
    try:
        relative = resolved.relative_to(resolved_repo)
    except ValueError:
        return resolved
    if relative.parts[:2] != (validator.DEFAULT_WORKSPACE_NAME, "benchmark-adapters"):
        raise NomiraclKoRunnerError(
            "run-manifest.json inside the repository must stay under "
            f"{validator.DEFAULT_BENCHMARK_WORKSPACE.as_posix()}/"
        )
    return resolved


def _require_existing_dir(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    if not resolved.is_dir():
        raise NomiraclKoRunnerError(f"{label} must exist and be a directory")
    return resolved


def _require_separate_inputs(wiki_dir: Path, bundle_dir: Path) -> None:
    if _is_same_or_nested(wiki_dir, bundle_dir) or _is_same_or_nested(bundle_dir, wiki_dir):
        raise NomiraclKoRunnerError(
            "wiki_dir and bundle_dir must be separate non-nested directories"
        )


def _is_same_or_nested(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


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
        with contextlib.suppress(OSError):
            temp_path.unlink()
        raise


def _temporary_file_sibling(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile

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


def _elapsed_ms(start_ns: int, end_ns: int) -> float:
    return max(0.0, (end_ns - start_ns) / 1_000_000.0)


def _round_metric(value: float) -> float:
    return round(value, 10)


def _round_measurement(value: float) -> float:
    return round(value, 3)


def parse_search_modes(value: str) -> tuple[BenchmarkSearchMode, ...]:
    return validate_search_modes([part for part in value.split(",") if part.strip()])


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run llmwiki-serve retrieval over a NoMIRACL Korean judged-pool bundle."
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
        "--search-modes",
        default="lexical,vector,plain-rrf,hybrid",
        help="Comma-separated modes to run: lexical,vector,plain-rrf,hybrid.",
    )
    parser.add_argument("--vector-cache-root", type=Path)
    parser.add_argument("--vector-model-cache-root", type=Path)
    parser.add_argument(
        "--vector-model-download",
        choices=("never", "allow"),
        default="never",
        help="Keep set to never for smoke unless the operator approves a model download.",
    )
    parser.add_argument(
        "--relevant-query-limit",
        type=int,
        help="Run only the first N relevant queries for smoke.",
    )
    parser.add_argument(
        "--non-relevant-query-limit",
        type=int,
        help="Run only the first N deterministic non-relevant sample queries for smoke.",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Emit sanitized JSON progress events to stderr while keeping stdout as final JSON.",
    )
    return parser.parse_args(argv)


def stderr_json_progress(event: Mapping[str, object]) -> None:
    print(json.dumps(event, sort_keys=True), file=sys.stderr, flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run_nomiracl_ko_benchmark(
            wiki_dir=cast(Path, args.wiki_dir),
            bundle_dir=cast(Path, args.bundle_dir),
            output_report=cast(Path, args.output_report),
            implementation_revision=cast(str, args.implementation_revision),
            search_modes=parse_search_modes(cast(str, args.search_modes)),
            vector_cache_root=cast(Path | None, args.vector_cache_root),
            vector_model_cache_root=cast(Path | None, args.vector_model_cache_root),
            vector_model_download=cast(str, args.vector_model_download),
            relevant_query_limit=cast(int | None, args.relevant_query_limit),
            non_relevant_query_limit=cast(int | None, args.non_relevant_query_limit),
            run_manifest=cast(Path | None, args.run_manifest),
            repo_root=cast(Path, args.repo_root),
            progress_callback=stderr_json_progress if bool(args.progress) else None,
        )
    except (
        NomiraclKoRunnerError,
        OSError,
        ValueError,
        validator.BundleValidationError,
    ) as error:
        print(f"nomiracl-ko retrieval run failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result.report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PRODUCT_SECONDARY_METRICS",
    "PRIMARY_METRICS",
    "REPORT_SCHEMA_ID",
    "REPORT_SCHEMA_VERSION",
    "RETRIEVAL_LIMIT",
    "RUN_MANIFEST_SCHEMA_ID",
    "SEARCH_MODES",
    "BenchmarkSearchMode",
    "NomiraclKoBenchmarkResult",
    "NomiraclKoBundle",
    "NomiraclKoRunnerError",
    "QueryMetrics",
    "build_public_report",
    "compute_query_metrics",
    "load_nomiracl_ko_bundle",
    "main",
    "parse_search_modes",
    "run_nomiracl_ko_benchmark",
    "validate_public_report",
]
