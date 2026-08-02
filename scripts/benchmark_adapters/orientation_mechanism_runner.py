"""Run the curated LLMWiki orientation mechanism benchmark.

This runner is intentionally separate from external retrieval-quality
benchmarks. It uses synthetic public-safe Markdown to verify production hybrid
orientation mechanics: hot/index first, visible relation extraction, exact
identifier preservation, approved-only draft isolation, and exact no-orientation
fallback to the benchmark plain-RRF baseline.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
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
from llmwiki_serve.search import AnalyzerProfile, search_corpus  # noqa: E402
from llmwiki_serve.vector import (  # noqa: E402
    DEFAULT_FASTEMBED_MODEL,
    DEFAULT_FASTEMBED_MODEL_REVISION,
    HYBRID_CANDIDATE_DEPTH_CAP,
    HYBRID_CANDIDATE_DEPTH_MIN,
    HYBRID_CANDIDATE_DEPTH_MULTIPLIER,
    HYBRID_ORIENTATION_CANDIDATE_LIMIT,
    HYBRID_ORIENTATION_SEED_LIMIT,
    HYBRID_RELATED_PAGE_LIMIT,
    HYBRID_RELATED_PER_SEED_LIMIT,
    HYBRID_RRF_K,
    VECTOR_CACHE_SCHEMA_VERSION,
    VECTOR_INDEX_SCHEMA_ID,
    VECTOR_TEXT_SCHEMA_ID,
    FastEmbedProvider,
    HybridDiagnostics,
    VectorConfig,
    hybrid_candidate_depth,
    plain_hybrid_search_results,
)

BenchmarkMode: TypeAlias = Literal["lexical", "vector", "plain-rrf", "hybrid"]
ClockNs: TypeAlias = Callable[[], int]

REPORT_SCHEMA_ID = "llmwiki-orientation-mechanism-benchmark-report-v1"
REPORT_SCHEMA_VERSION = "0.2.0"
RUNNER_NAME = "llmwiki-orientation-mechanism-runner"
RUNNER_VERSION = "0.2.0"
BENCHMARK_CLASS = "curated-functional-mechanism-benchmark"
DATASET = "llmwiki-orientation-mechanism-curated-v1"
RETRIEVAL_LIMIT = 5
FIXED_INDEX_REFRESH_INTERVAL_SECONDS = 86_400.0
SEARCH_MODES: tuple[BenchmarkMode, ...] = ("lexical", "vector", "plain-rrf", "hybrid")
VECTOR_REQUIRED_MODES = frozenset({"vector", "plain-rrf", "hybrid"})
ORIENTATION_PAGE_NAMES = frozenset({"hot.md", "index.md", "overview.md"})
ORIENTATION_PAGE_IDS = frozenset(name.removesuffix(".md") for name in ORIENTATION_PAGE_NAMES)
FALLBACK_CHECK_QUERY_LIMIT = 4
MIN_ORIENTATION_QUERY_COUNT = 8
MAX_ORIENTATION_QUERY_COUNT = 24
ADVERSARIAL_CASES = frozenset(
    {
        "high_degree_generic_hub",
        "stale_deleted_link_target",
        "prompt_injection_like_prose",
        "explicit_malicious_distractor_link",
        "malicious_tag_source_ref",
        "exact_identifier_poisoned_hints",
        "draft_private_target",
        "duplicate_alias_links",
        "korean_nfc_nfd_label",
    }
)
DRAFT_PRIVATE_PAGE_IDS = frozenset({"draft_retention_plan"})
MISSING_TARGET_PAGE_IDS = frozenset({"deleted_escalation_target"})
MALICIOUS_DISTRACTOR_PAGE_IDS_BY_CASE: Mapping[str, frozenset[str]] = {
    "explicit_malicious_distractor_link": frozenset({"overcharge_glossary"}),
    "malicious_tag_source_ref": frozenset({"malicious_relation_target"}),
}
UNRELATED_BOILERPLATE_PAGE_IDS = frozenset(
    {
        "boilerplate_alpha",
        "boilerplate_beta",
        "boilerplate_gamma",
        "boilerplate_delta",
        "korean_boilerplate_alpha",
    }
)
PRIVATE_PATTERNS = (
    re.compile(r"\b[A-Za-z]:[\\/][^\s\"']*"),
    re.compile(r"\\\\[A-Za-z0-9._$-]+\\[^\s\"']+"),
    re.compile(r"\bfile://[^\s\"']+", re.IGNORECASE),
    re.compile(
        r"(?<![A-Za-z0-9.:/\\-])/"
        r"(?:Users|home|root|tmp|var|mnt|media|workspace|raid|data|opt|srv)"
        r"(?:[\\/][^\s\"']*)?"
    ),
    re.compile(r"(?<![A-Za-z0-9._-])(?:\.llmwiki-work|\.runtime-logs|\.codex)(?:[\\/]|$)"),
    re.compile(
        r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|10\.\d+\.\d+\.\d+|"
        r"192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)"
        r"(?:[:/][^\s\"']*)?",
        re.IGNORECASE,
    ),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}"),
)


class OrientationBenchmarkError(RuntimeError):
    """Raised when the curated orientation benchmark cannot run safely."""


class BenchmarkProvider(Protocol):
    provider_id: str
    model_id: str
    model_revision: str
    dimension: int
    distance_metric: Literal["cosine"]

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...

    def embed_query(self, text: str) -> Sequence[float]: ...

    def safe_metadata(self) -> dict[str, str | int]: ...


@dataclass(frozen=True)
class QueryCase:
    query_id: str
    language: Literal["en", "ko"]
    case: str
    query: str
    target_page_id: str


@dataclass
class EmbeddingTelemetry:
    document_calls: int = 0
    document_texts: int = 0
    document_chars: int = 0
    document_latency_ms: list[float] = field(default_factory=list)
    query_calls: int = 0
    query_chars: int = 0
    query_latency_ms: list[float] = field(default_factory=list)
    provider_init_ms: float | None = None
    model_cache_bytes_before: int | None = None
    model_cache_bytes_after: int | None = None

    @property
    def model_download_bytes_observed(self) -> int | None:
        if self.model_cache_bytes_before is None or self.model_cache_bytes_after is None:
            return None
        return max(0, self.model_cache_bytes_after - self.model_cache_bytes_before)


@dataclass(frozen=True)
class VectorRuntime:
    provider: TimedEmbeddingProvider
    provider_metadata: Mapping[str, str | int]
    vector_cache_root: Path
    model_cache_root: Path | None
    telemetry: EmbeddingTelemetry


@dataclass(frozen=True)
class ModeRun:
    mode: BenchmarkMode
    ranked: dict[str, list[str]]
    ranks: dict[str, int | None]
    metrics: dict[str, float]
    latencies_ms: tuple[float, ...]
    payload_bytes: tuple[int, ...]
    diagnostics: tuple[HybridDiagnostics, ...] = ()
    candidate_depths: tuple[int, ...] = ()
    diagnostics_by_query: Mapping[str, HybridDiagnostics] = field(default_factory=dict)
    candidate_depths_by_query: Mapping[str, int] = field(default_factory=dict)


class TimedEmbeddingProvider:
    def __init__(self, provider: BenchmarkProvider, telemetry: EmbeddingTelemetry) -> None:
        self._provider = provider
        self._telemetry = telemetry
        self.provider_id = provider.provider_id
        self.model_id = provider.model_id
        self.model_revision = provider.model_revision
        self.dimension = provider.dimension
        self.distance_metric = provider.distance_metric

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        start_ns = time.perf_counter_ns()
        try:
            return self._provider.embed_documents(texts)
        finally:
            self._telemetry.document_calls += 1
            self._telemetry.document_texts += len(texts)
            self._telemetry.document_chars += sum(len(text) for text in texts)
            self._telemetry.document_latency_ms.append(elapsed_ms(start_ns, time.perf_counter_ns()))

    def embed_query(self, text: str) -> Sequence[float]:
        start_ns = time.perf_counter_ns()
        try:
            return self._provider.embed_query(text)
        finally:
            self._telemetry.query_calls += 1
            self._telemetry.query_chars += len(text)
            self._telemetry.query_latency_ms.append(elapsed_ms(start_ns, time.perf_counter_ns()))

    def safe_metadata(self) -> dict[str, str | int]:
        return self._provider.safe_metadata()


def run_orientation_mechanism_benchmark(
    *,
    fixture_dir: Path,
    output_report: Path,
    vector_cache_root: Path | None = None,
    vector_model_cache_root: Path | None = None,
    vector_provider: BenchmarkProvider | None = None,
    analyzer_profile: AnalyzerProfile = "english",
    implementation_revision: str | None = None,
    clock_ns: ClockNs = time.perf_counter_ns,
    repo_root: Path = ROOT,
) -> dict[str, object]:
    fixture = fixture_dir.resolve(strict=True)
    wiki_dir = require_dir(fixture / "wiki", "fixture wiki")
    queries = load_query_cases(fixture / "queries.jsonl")
    qrels = load_qrels(fixture / "qrels.jsonl")
    validate_qrels(queries, qrels)
    report_path = output_report.expanduser().resolve(strict=False)
    revision = implementation_revision or default_implementation_revision(repo_root)
    runtime = prepare_vector_runtime(
        output_report=report_path,
        repo_root=repo_root,
        vector_cache_root=vector_cache_root,
        vector_model_cache_root=vector_model_cache_root,
        vector_provider=vector_provider,
    )
    service = LlmWikiService(
        wiki_dir,
        refresh_interval_seconds=FIXED_INDEX_REFRESH_INTERVAL_SECONDS,
        analyzer_profile=analyzer_profile,
        vector_config=VectorConfig(
            enabled=True,
            cache_dir=runtime.vector_cache_root,
            model_cache_dir=runtime.model_cache_root,
            model_download="never",
        ),
        vector_provider=runtime.provider,
    )
    index_start_ns = clock_ns()
    index = service.index()
    index_build_ms = elapsed_ms(index_start_ns, clock_ns())
    warm_start_ns = clock_ns()
    service.search("", limit=1, mode="vector")
    vector_warm_ms = elapsed_ms(warm_start_ns, clock_ns())
    vector_index_cache_hit = loaded_vector_cache_hit(service)
    mode_runs = [
        run_mode(service, queries, qrels, mode=mode, clock_ns=clock_ns) for mode in SEARCH_MODES
    ]
    fallback_check = run_missing_orientation_fallback_check(
        source_wiki_dir=wiki_dir,
        queries=queries[:FALLBACK_CHECK_QUERY_LIMIT],
        vector_runtime=runtime,
        analyzer_profile=analyzer_profile,
    )
    if runtime.model_cache_root is not None:
        runtime.telemetry.model_cache_bytes_after = tree_size_bytes(runtime.model_cache_root)
    report = build_report(
        fixture_digest=compute_tree_digest(fixture),
        index=index,
        index_build_ms=index_build_ms,
        vector_warm_ms=vector_warm_ms,
        vector_index_cache_hit=vector_index_cache_hit,
        mode_runs=mode_runs,
        queries=queries,
        qrels=qrels,
        fallback_check=fallback_check,
        vector_runtime=runtime,
        analyzer_profile=analyzer_profile,
        implementation_revision=revision,
        repo_root=repo_root,
    )
    validate_orientation_report(report)
    atomic_write_json(report_path, report)
    return report


def prepare_vector_runtime(
    *,
    output_report: Path,
    repo_root: Path,
    vector_cache_root: Path | None,
    vector_model_cache_root: Path | None,
    vector_provider: BenchmarkProvider | None,
) -> VectorRuntime:
    cache_root = (
        vector_cache_root.expanduser().resolve(strict=False)
        if vector_cache_root is not None
        else default_cache_root(repo_root, output_report, "vector-cache")
    )
    model_cache_root = (
        vector_model_cache_root.expanduser().resolve(strict=False)
        if vector_model_cache_root is not None
        else None
    )
    telemetry = EmbeddingTelemetry(
        model_cache_bytes_before=(
            tree_size_bytes(model_cache_root) if model_cache_root is not None else None
        )
    )
    if vector_provider is None:
        start_ns = time.perf_counter_ns()
        provider: BenchmarkProvider = FastEmbedProvider(
            model_name=DEFAULT_FASTEMBED_MODEL,
            model_cache_dir=model_cache_root,
            model_download="never",
        )
        telemetry.provider_init_ms = elapsed_ms(start_ns, time.perf_counter_ns())
    else:
        provider = vector_provider
        telemetry.provider_init_ms = 0.0
    timed_provider = TimedEmbeddingProvider(provider, telemetry)
    return VectorRuntime(
        provider=timed_provider,
        provider_metadata=timed_provider.safe_metadata(),
        vector_cache_root=cache_root,
        model_cache_root=model_cache_root,
        telemetry=telemetry,
    )


def run_mode(
    service: LlmWikiService,
    queries: Sequence[QueryCase],
    qrels: Mapping[str, Mapping[str, float]],
    *,
    mode: BenchmarkMode,
    clock_ns: ClockNs,
) -> ModeRun:
    ranked: dict[str, list[str]] = {}
    latencies: list[float] = []
    payload_sizes: list[int] = []
    diagnostics: list[HybridDiagnostics] = []
    candidate_depths: list[int] = []
    diagnostics_by_query: dict[str, HybridDiagnostics] = {}
    candidate_depths_by_query: dict[str, int] = {}
    for query in queries:
        start_ns = clock_ns()
        if mode == "plain-rrf":
            results, candidate_depth = plain_rrf_results(service, query.query)
            candidate_depths.append(candidate_depth)
            candidate_depths_by_query[query.query_id] = candidate_depth
        elif mode == "hybrid":
            with capture_hybrid_diagnostics() as captured:
                raw_results = service.search(query.query, limit=RETRIEVAL_LIMIT, mode="hybrid")
            diagnostics.extend(captured.diagnostics)
            candidate_depths.extend(captured.candidate_depths)
            if captured.diagnostics:
                diagnostics_by_query[query.query_id] = captured.diagnostics[-1]
            if captured.candidate_depths:
                candidate_depths_by_query[query.query_id] = captured.candidate_depths[-1]
            results = cast(list[Mapping[str, Any]], raw_results)
        else:
            raw_results = service.search(query.query, limit=RETRIEVAL_LIMIT, mode=mode)
            results = cast(list[Mapping[str, Any]], raw_results)
        latencies.append(elapsed_ms(start_ns, clock_ns()))
        payload_sizes.append(serialized_payload_bytes(results))
        ranked[query.query_id] = [require_result_page_id(item) for item in results]
    metrics = aggregate_metrics(ranked, qrels)
    ranks = {
        query.query_id: rank_of(ranked[query.query_id], query.target_page_id) for query in queries
    }
    return ModeRun(
        mode=mode,
        ranked=ranked,
        ranks=ranks,
        metrics=metrics,
        latencies_ms=tuple(latencies),
        payload_bytes=tuple(payload_sizes),
        diagnostics=tuple(diagnostics),
        candidate_depths=tuple(candidate_depths),
        diagnostics_by_query=diagnostics_by_query,
        candidate_depths_by_query=candidate_depths_by_query,
    )


def plain_rrf_results(
    service: LlmWikiService,
    query: str,
) -> tuple[list[Mapping[str, Any]], int]:
    index = service.index()
    corpus = service._index_views(index).search_corpus(False)  # noqa: SLF001
    candidate_limit = hybrid_candidate_depth(RETRIEVAL_LIMIT, total_docs=len(corpus.documents))
    lexical_results = search_corpus(
        corpus,
        query,
        limit=candidate_limit,
        mode="lexical",
        snippet_chars=None,
        min_score=None,
        exclude_page_ids=None,
    )
    vector_results = service._vector_result_objects(  # noqa: SLF001
        index,
        corpus.pages,
        query,
        limit=candidate_limit,
        include_drafts=False,
        snippet_chars=None,
        exclude_page_ids=None,
    )
    results = plain_hybrid_search_results(
        lexical_results=lexical_results,
        vector_results=vector_results,
        corpus=corpus,
        query=query,
        limit=RETRIEVAL_LIMIT,
    )
    return [result.model_dump(mode="json") for result in results], candidate_limit


@dataclass
class CapturedHybridDiagnostics:
    diagnostics: list[HybridDiagnostics] = field(default_factory=list)
    candidate_depths: list[int] = field(default_factory=list)


@contextlib.contextmanager
def capture_hybrid_diagnostics() -> Any:
    import llmwiki_serve.service as service_module

    service_namespace = cast(Any, service_module)
    captured = CapturedHybridDiagnostics()
    original_hybrid = service_namespace.hybrid_search_results

    def wrapped_hybrid(*args: Any, **kwargs: Any) -> Any:
        candidate_limit = kwargs.get("candidate_limit")
        if isinstance(candidate_limit, int):
            captured.candidate_depths.append(candidate_limit)
        kwargs.setdefault("diagnostics_sink", captured.diagnostics.append)
        return original_hybrid(*args, **kwargs)

    service_namespace.hybrid_search_results = wrapped_hybrid
    try:
        yield captured
    finally:
        service_namespace.hybrid_search_results = original_hybrid


def run_missing_orientation_fallback_check(
    *,
    source_wiki_dir: Path,
    queries: Sequence[QueryCase],
    vector_runtime: VectorRuntime,
    analyzer_profile: AnalyzerProfile,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="llmwiki-orientation-no-orientation-") as temp_name:
        target = Path(temp_name) / "wiki"
        shutil.copytree(source_wiki_dir, target)
        for file_name in ORIENTATION_PAGE_NAMES:
            with contextlib.suppress(FileNotFoundError):
                (target / file_name).unlink()
        service = LlmWikiService(
            target,
            refresh_interval_seconds=FIXED_INDEX_REFRESH_INTERVAL_SECONDS,
            analyzer_profile=analyzer_profile,
            vector_config=VectorConfig(
                enabled=True,
                cache_dir=vector_runtime.vector_cache_root / "missing-orientation",
                model_cache_dir=vector_runtime.model_cache_root,
                model_download="never",
            ),
            vector_provider=vector_runtime.provider,
        )
        exact_matches: dict[str, bool] = {}
        for query in queries:
            plain, _candidate_depth = plain_rrf_results(service, query.query)
            hybrid = service.search(query.query, limit=RETRIEVAL_LIMIT, mode="hybrid")
            exact_matches[query.query_id] = comparable_results(plain) == comparable_results(hybrid)
        return {
            "checked_query_ids": [query.query_id for query in queries],
            "exact_order_and_score_match": all(exact_matches.values()),
            "per_query_exact_match": exact_matches,
        }


def build_report(
    *,
    fixture_digest: str,
    index: WikiIndex,
    index_build_ms: float,
    vector_warm_ms: float,
    vector_index_cache_hit: bool | None,
    mode_runs: Sequence[ModeRun],
    queries: Sequence[QueryCase],
    qrels: Mapping[str, Mapping[str, float]],
    fallback_check: Mapping[str, object],
    vector_runtime: VectorRuntime,
    analyzer_profile: AnalyzerProfile,
    implementation_revision: str,
    repo_root: Path,
) -> dict[str, object]:
    runs = {run.mode: run for run in mode_runs}
    per_query = [per_query_rank_delta(query, runs) for query in queries]
    case_assertions = summarize_case_assertions(
        queries,
        runs,
        fallback_check,
        total_docs=len(index.pages),
    )
    diagnostics = summarize_orientation_diagnostics(queries, runs["hybrid"])
    robustness_gates = build_robustness_gates(case_assertions)
    return {
        "schema_id": REPORT_SCHEMA_ID,
        "schema_version": REPORT_SCHEMA_VERSION,
        "benchmark_class": BENCHMARK_CLASS,
        "dataset": DATASET,
        "notice": (
            "Curated mechanism benchmark using synthetic public-safe Markdown. "
            "Non-authoritative; not an external retrieval-quality or language-quality headline."
        ),
        "package_version": LLMWIKI_SERVE_VERSION,
        "runner": {"name": RUNNER_NAME, "version": RUNNER_VERSION},
        "implementation": implementation_metadata(repo_root, implementation_revision),
        "runtime_environment": runtime_environment_metadata(),
        "fixture": {
            "content_sha256": fixture_digest,
            "contains_raw_queries_in_fixture": True,
            "contains_synthetic_public_safe_markdown": True,
            "wiki_page_count": len(index.pages),
            "approved_page_count": sum(1 for page in index.pages if page.approved_for_serving),
            "adversarial_query_count": sum(
                1 for query in queries if query.case in ADVERSARIAL_CASES
            ),
            "orientation_pages": sorted(
                page.path for page in index.pages if page.role in {"hot", "index", "overview"}
            ),
        },
        "tested_size_envelope": {
            "query_count": len(queries),
            "qrel_count": sum(len(items) for items in qrels.values()),
            "wiki_page_count": len(index.pages),
            "approved_page_count": sum(1 for page in index.pages if page.approved_for_serving),
            "adversarial_query_count": sum(
                1 for query in queries if query.case in ADVERSARIAL_CASES
            ),
            "retrieval_limit": RETRIEVAL_LIMIT,
            "max_fixture_query_count": MAX_ORIENTATION_QUERY_COUNT,
            "synthetic_results_authority": "non-authoritative",
        },
        "public_safety": {
            "report_class": "sanitized-aggregate-and-query-id-ranks",
            "contains_raw_query_text": False,
            "contains_raw_document_text": False,
            "contains_local_paths": False,
        },
        "retrieval_configuration": {
            "analyzer_profile": analyzer_profile,
            "retrieval_limit": RETRIEVAL_LIMIT,
            "search_modes": list(SEARCH_MODES),
            "hybrid_strategy": "orientation-seeded-v1",
            "hybrid_plain_rrf_role": "benchmark-baseline-and-no-orientation-fallback",
            "hybrid_rrf_k": HYBRID_RRF_K,
            "hybrid_candidate_depth_policy": {
                "formula": (
                    "min(total_docs, max(limit, min(cap, max(minimum, multiplier * limit))))"
                ),
                "minimum": HYBRID_CANDIDATE_DEPTH_MIN,
                "multiplier": HYBRID_CANDIDATE_DEPTH_MULTIPLIER,
                "cap": HYBRID_CANDIDATE_DEPTH_CAP,
            },
            "orientation_expansion_policy": {
                "orientation_candidate_limit": HYBRID_ORIENTATION_CANDIDATE_LIMIT,
                "orientation_seed_limit": HYBRID_ORIENTATION_SEED_LIMIT,
                "related_page_limit": HYBRID_RELATED_PAGE_LIMIT,
                "related_per_seed_limit": HYBRID_RELATED_PER_SEED_LIMIT,
            },
            "exact_search_backend": {
                "name": "builtin-lexical-exact-compound-and-metadata",
                "scope": "single exact compound query tokens under the selected analyzer profile",
                "external_service": "none",
            },
            "hybrid_channel_weights": {
                "lexical_exact": 1.0,
                "related_vector": 1.0,
                "global_vector": 0.75,
                "orientation_doc": 0.35,
                "graph_prior": 0.25,
            },
        },
        "retrieval_schema": {
            "text_schema": VECTOR_TEXT_SCHEMA_ID,
            "index_schema": VECTOR_INDEX_SCHEMA_ID,
            "vector_cache_schema": VECTOR_CACHE_SCHEMA_VERSION,
            "distance_metric": "cosine",
        },
        "languages_evaluated": sorted({query.language for query in queries}),
        "query_count": len(queries),
        "qrel_count": sum(len(items) for items in qrels.values()),
        "metrics": {run.mode: run.metrics for run in mode_runs},
        "target_rank_deltas": per_query,
        "case_assertions": case_assertions,
        "robustness_gates": robustness_gates,
        "orientation_diagnostics": diagnostics,
        "timings_ms": {
            "index_build": round_measurement(index_build_ms),
            "vector_warm_load_or_build": round_measurement(vector_warm_ms),
            "per_mode_search": {
                run.mode: percentile_distribution(run.latencies_ms) for run in mode_runs
            },
        },
        "serialized_payload_bytes": {
            run.mode: percentile_distribution(run.payload_bytes) for run in mode_runs
        },
        "vector_provider": vector_provider_report(vector_runtime),
        "embedding_telemetry": embedding_telemetry_report(vector_runtime.telemetry),
        "vector_cache": {
            "cache_hit_on_warm_load": vector_index_cache_hit,
            "bytes": tree_size_bytes(vector_runtime.vector_cache_root),
            "schema": VECTOR_CACHE_SCHEMA_VERSION,
        },
        "limitations": [
            "Synthetic/public-safe Markdown designed to exercise mechanism behavior.",
            "Not an external retrieval-quality benchmark.",
            "Not a language-quality headline for English or Korean.",
            "Production hybrid weights are not tuned by this fixture.",
            (
                "The no-orientation fallback check uses a temporary copy with root orientation "
                "pages removed."
            ),
        ],
    }


def per_query_rank_delta(
    query: QueryCase,
    runs: Mapping[BenchmarkMode, ModeRun],
) -> dict[str, object]:
    ranks = {mode: runs[mode].ranks[query.query_id] for mode in SEARCH_MODES}
    plain_rank = ranks["plain-rrf"]
    hybrid_rank = ranks["hybrid"]
    delta: int | None = None
    if plain_rank is not None and hybrid_rank is not None:
        delta = plain_rank - hybrid_rank
    return {
        "query_id": query.query_id,
        "language": query.language,
        "case": query.case,
        "target_page_id": query.target_page_id,
        "target_ranks": ranks,
        "plain_rrf_to_hybrid_rank_delta": delta,
    }


def summarize_case_assertions(
    queries: Sequence[QueryCase],
    runs: Mapping[BenchmarkMode, ModeRun],
    fallback_check: Mapping[str, object],
    *,
    total_docs: int,
) -> dict[str, object]:
    lift_query_ids = [
        query.query_id
        for query in queries
        if query.case == "orientation_link_lift"
        and rank_improved(
            runs["plain-rrf"].ranks[query.query_id],
            runs["hybrid"].ranks[query.query_id],
        )
    ]
    exact_identifier_ids = [
        query.query_id
        for query in queries
        if query.case == "exact_identifier" and runs["hybrid"].ranks[query.query_id] == 1
    ]
    boilerplate_query_ids = [
        query.query_id
        for query in queries
        if query.case == "boilerplate_resistance"
        and not top_result_is_boilerplate(runs["hybrid"].ranked[query.query_id])
    ]
    draft_leaks = [
        query.query_id
        for query in queries
        if query.case == "draft_isolation"
        and "draft_retention_plan" in runs["hybrid"].ranked[query.query_id]
    ]
    adversarial_results = [
        evaluate_adversarial_case(query, runs, total_docs=total_docs)
        for query in queries
        if query.case in ADVERSARIAL_CASES
    ]
    return {
        "orientation_link_lift_query_ids": lift_query_ids,
        "boilerplate_resistance_query_ids": boilerplate_query_ids,
        "exact_identifier_rank1_query_ids": exact_identifier_ids,
        "approved_mode_draft_leak_query_ids": draft_leaks,
        "missing_orientation_fallback": dict(fallback_check),
        "adversarial_orientation_results": adversarial_results,
    }


def build_robustness_gates(case_assertions: Mapping[str, object]) -> dict[str, object]:
    results = require_report_list(
        case_assertions.get("adversarial_orientation_results"),
        "case_assertions.adversarial_orientation_results",
    )
    passed = [
        str(item["query_id"])
        for item in results
        if isinstance(item, dict) and item.get("status") == "pass"
    ]
    failed = [
        str(item["query_id"])
        for item in results
        if isinstance(item, dict) and item.get("status") == "fail"
    ]
    residual = [
        str(item["query_id"])
        for item in results
        if isinstance(item, dict) and item.get("status") == "residual_risk"
    ]
    return {
        "gate": "orientation-adversarial-public-safe-v1",
        "non_authoritative_synthetic_results": True,
        "passed_query_ids": passed,
        "failed_query_ids": failed,
        "residual_risk_query_ids": residual,
        "production_code_blocker_query_ids": failed,
        "residual_risk_policy": (
            "Explicit visible malicious relations are measured and reported instead of "
            "fixture-specific production tuning."
        ),
    }


def summarize_orientation_diagnostics(
    queries: Sequence[QueryCase],
    hybrid_run: ModeRun,
) -> dict[str, object]:
    diagnostics = hybrid_run.diagnostics
    orientation_seeded = [item for item in diagnostics if item.mode == "orientation-seeded"]
    fallback = [item for item in diagnostics if item.mode == "plain-rrf"]
    return {
        "hybrid_query_count": len(diagnostics),
        "orientation_seeded_queries": len(orientation_seeded),
        "fallback_queries": len(fallback),
        "total_orientation_seed_count": sum(item.orientation_seed_count for item in diagnostics),
        "total_related_page_count": sum(item.related_page_count for item in diagnostics),
        "fallback_reasons": sorted(
            {item.fallback_reason for item in fallback if item.fallback_reason}
        ),
        "per_query": [per_query_orientation_diagnostic(query, hybrid_run) for query in queries],
    }


def evaluate_adversarial_case(
    query: QueryCase,
    runs: Mapping[BenchmarkMode, ModeRun],
    *,
    total_docs: int,
) -> dict[str, object]:
    hybrid = runs["hybrid"]
    ranked = hybrid.ranked[query.query_id]
    target_rank = hybrid.ranks[query.query_id]
    diagnostic = hybrid.diagnostics_by_query.get(query.query_id)
    candidate_depth = hybrid.candidate_depths_by_query.get(query.query_id)
    failures: list[str] = []
    residual_risks: list[str] = []
    invariants: list[str] = []

    def require_target_retrieved() -> None:
        invariants.append("target_retrieved")
        if target_rank is None:
            failures.append("target_not_retrieved")

    def require_target_rank1() -> None:
        invariants.append("target_rank1")
        if target_rank != 1:
            failures.append("target_not_rank1")

    def require_no_orientation_evidence() -> None:
        invariants.append("orientation_result_suppressed")
        if any(page_id in ORIENTATION_PAGE_IDS for page_id in ranked):
            failures.append("orientation_page_returned")

    def require_no_draft_private_leak() -> None:
        invariants.append("no_draft_private_leak")
        if ranked_page_ids_in(DRAFT_PRIVATE_PAGE_IDS, ranked):
            failures.append("draft_private_target_returned")

    require_no_orientation_evidence()
    require_no_draft_private_leak()

    if query.case == "high_degree_generic_hub":
        require_target_retrieved()
        invariants.append("cap_enforced")
        if candidate_depth is None:
            failures.append("missing_candidate_depth")
        elif candidate_depth > min(total_docs, HYBRID_CANDIDATE_DEPTH_CAP):
            failures.append("candidate_depth_cap_exceeded")
        if diagnostic is None:
            failures.append("missing_orientation_diagnostic")
        elif diagnostic.related_page_count > HYBRID_RELATED_PAGE_LIMIT:
            failures.append("related_page_cap_exceeded")
        elif diagnostic.orientation_seed_count > 0 and diagnostic.related_page_count > (
            diagnostic.orientation_seed_count * HYBRID_RELATED_PER_SEED_LIMIT
        ):
            failures.append("related_per_seed_cap_exceeded")
    elif query.case == "stale_deleted_link_target":
        require_target_retrieved()
        invariants.append("missing_deleted_target_ignored")
        if ranked_page_ids_in(MISSING_TARGET_PAGE_IDS, ranked):
            failures.append("missing_deleted_target_returned")
    elif query.case == "prompt_injection_like_prose":
        require_target_rank1()
        invariants.append("strong_lexical_answer_not_displaced")
        if page_rank_before("overcharge_glossary", query.target_page_id, ranked):
            failures.append("prompt_like_hint_displaced_target")
    elif query.case in MALICIOUS_DISTRACTOR_PAGE_IDS_BY_CASE:
        require_target_retrieved()
        malicious_ids = MALICIOUS_DISTRACTOR_PAGE_IDS_BY_CASE[query.case]
        observed = ranked_page_ids_in(malicious_ids, ranked)
        if observed:
            residual_risks.append("explicit_malicious_relation_steered_results")
        if any(
            page_rank_before(page_id, query.target_page_id, ranked) for page_id in malicious_ids
        ):
            residual_risks.append("explicit_malicious_relation_ranked_before_target")
    elif query.case == "exact_identifier_poisoned_hints":
        require_target_rank1()
        invariants.append("exact_identifier_preserved")
    elif query.case == "draft_private_target":
        require_target_retrieved()
        invariants.append("approved_orientation_draft_target_suppressed")
        if ranked_page_ids_in(DRAFT_PRIVATE_PAGE_IDS, ranked):
            failures.append("approved_mode_draft_target_leaked")
    elif query.case == "duplicate_alias_links":
        require_target_retrieved()
        invariants.append("duplicate_alias_links_deduped")
        if len(set(ranked)) != len(ranked):
            failures.append("duplicate_result_page_ids")
    elif query.case == "korean_nfc_nfd_label":
        require_target_retrieved()
        invariants.append("korean_nfc_nfd_relation_preserved")
        if diagnostic is None:
            failures.append("missing_orientation_diagnostic")
        elif diagnostic.mode != "orientation-seeded":
            failures.append("nfc_nfd_label_did_not_seed_orientation")
    else:
        require_target_retrieved()

    status = "fail" if failures else "residual_risk" if residual_risks else "pass"
    return {
        "query_id": query.query_id,
        "case": query.case,
        "target_page_id": query.target_page_id,
        "status": status,
        "invariants": sorted(set(invariants)),
        "failures": failures,
        "residual_risks": sorted(set(residual_risks)),
        "observed": {
            "target_rank_hybrid": target_rank,
            "hybrid_top_page_id": ranked[0] if ranked else None,
            "hybrid_result_count": len(ranked),
            "orientation_page_ids_returned": ranked_page_ids_in(ORIENTATION_PAGE_IDS, ranked),
            "draft_private_page_ids_returned": ranked_page_ids_in(DRAFT_PRIVATE_PAGE_IDS, ranked),
            "missing_target_page_ids_returned": ranked_page_ids_in(MISSING_TARGET_PAGE_IDS, ranked),
            "malicious_page_ids_returned": ranked_page_ids_in(
                MALICIOUS_DISTRACTOR_PAGE_IDS_BY_CASE.get(query.case, frozenset()),
                ranked,
            ),
            "candidate_depth": candidate_depth,
            "orientation_mode": diagnostic.mode if diagnostic else "missing",
            "orientation_seed_count": (
                diagnostic.orientation_seed_count if diagnostic is not None else None
            ),
            "related_page_count": diagnostic.related_page_count if diagnostic is not None else None,
            "fallback_reason": diagnostic.fallback_reason if diagnostic is not None else "missing",
        },
    }


def per_query_orientation_diagnostic(query: QueryCase, hybrid_run: ModeRun) -> dict[str, object]:
    diagnostic = hybrid_run.diagnostics_by_query.get(query.query_id)
    return {
        "query_id": query.query_id,
        "case": query.case,
        "mode": diagnostic.mode if diagnostic else "missing",
        "orientation_seed_count": (
            diagnostic.orientation_seed_count if diagnostic is not None else None
        ),
        "related_page_count": diagnostic.related_page_count if diagnostic is not None else None,
        "fallback_reason": diagnostic.fallback_reason if diagnostic is not None else "missing",
        "candidate_depth": hybrid_run.candidate_depths_by_query.get(query.query_id),
    }


def ranked_page_ids_in(page_ids: frozenset[str], ranked: Sequence[str]) -> list[str]:
    return [page_id for page_id in ranked if page_id in page_ids]


def page_rank_before(left_page_id: str, right_page_id: str, ranked: Sequence[str]) -> bool:
    try:
        left_rank = ranked.index(left_page_id)
        right_rank = ranked.index(right_page_id)
    except ValueError:
        return False
    return left_rank < right_rank


def require_report_list(value: object, label: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise OrientationBenchmarkError(f"{label} must be a list of objects")
    return cast(list[dict[str, object]], value)


def aggregate_metrics(
    ranked: Mapping[str, Sequence[str]],
    qrels: Mapping[str, Mapping[str, float]],
) -> dict[str, float]:
    rows = [
        {
            "nDCG@5": ndcg_at_k(ranked[query_id], query_qrels, 5),
            "Hit@1": hit_at_k(ranked[query_id], query_qrels, 1),
            "Hit@3": hit_at_k(ranked[query_id], query_qrels, 3),
            "Hit@5": hit_at_k(ranked[query_id], query_qrels, 5),
            "MRR@5": mrr_at_k(ranked[query_id], query_qrels, 5),
        }
        for query_id, query_qrels in qrels.items()
    ]
    return {
        metric: round_measurement(sum(row[metric] for row in rows) / len(rows), digits=10)
        for metric in ("nDCG@5", "Hit@1", "Hit@3", "Hit@5", "MRR@5")
    }


def ndcg_at_k(ranked_ids: Sequence[str], qrels: Mapping[str, float], cutoff: int) -> float:
    ideal = sorted((value for value in qrels.values() if value > 0), reverse=True)[:cutoff]
    ideal_dcg = dcg(ideal)
    if ideal_dcg <= 0:
        return 0.0
    observed = [qrels.get(page_id, 0.0) for page_id in ranked_ids[:cutoff]]
    return dcg(observed) / ideal_dcg


def dcg(relevances: Sequence[float]) -> float:
    return sum(
        (math.pow(2.0, relevance) - 1.0) / math.log2(rank + 1)
        for rank, relevance in enumerate(relevances, start=1)
        if relevance > 0
    )


def hit_at_k(ranked_ids: Sequence[str], qrels: Mapping[str, float], cutoff: int) -> float:
    positive = {page_id for page_id, relevance in qrels.items() if relevance > 0}
    return 1.0 if positive & set(ranked_ids[:cutoff]) else 0.0


def mrr_at_k(ranked_ids: Sequence[str], qrels: Mapping[str, float], cutoff: int) -> float:
    positive = {page_id for page_id, relevance in qrels.items() if relevance > 0}
    for rank, page_id in enumerate(ranked_ids[:cutoff], start=1):
        if page_id in positive:
            return 1.0 / rank
    return 0.0


def validate_orientation_report(report: Mapping[str, object]) -> None:
    required = {
        "schema_id",
        "schema_version",
        "benchmark_class",
        "notice",
        "metrics",
        "target_rank_deltas",
        "case_assertions",
        "robustness_gates",
        "orientation_diagnostics",
        "vector_provider",
        "vector_cache",
        "embedding_telemetry",
        "public_safety",
        "tested_size_envelope",
    }
    missing = required - set(report)
    if missing:
        raise OrientationBenchmarkError(f"orientation report missing fields: {sorted(missing)}")
    if report["schema_id"] != REPORT_SCHEMA_ID:
        raise OrientationBenchmarkError("unexpected orientation report schema_id")
    if report["schema_version"] != REPORT_SCHEMA_VERSION:
        raise OrientationBenchmarkError("unexpected orientation report schema_version")
    if report["benchmark_class"] != BENCHMARK_CLASS:
        raise OrientationBenchmarkError("orientation report benchmark_class is wrong")
    notice = str(report["notice"]).casefold()
    if "non-authoritative" not in notice or "language-quality headline" not in notice:
        raise OrientationBenchmarkError("orientation report must state non-authoritative limits")
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)
    for pattern in PRIVATE_PATTERNS:
        if pattern.search(encoded):
            raise OrientationBenchmarkError("orientation report contains a private-looking value")
    if '"query":' in encoded or '"text":' in encoded:
        raise OrientationBenchmarkError(
            "orientation report must not include raw query/document text"
        )
    metrics = cast(Mapping[str, object], report["metrics"])
    if set(metrics) != set(SEARCH_MODES):
        raise OrientationBenchmarkError("orientation report metrics must cover all search modes")
    public_safety = cast(Mapping[str, object], report["public_safety"])
    if public_safety.get("contains_raw_query_text") is not False:
        raise OrientationBenchmarkError("orientation report must flag raw query text as absent")
    size = cast(Mapping[str, object], report["tested_size_envelope"])
    query_count = size.get("query_count")
    if not isinstance(query_count, int) or query_count < MIN_ORIENTATION_QUERY_COUNT:
        raise OrientationBenchmarkError("orientation report missing tested query-count envelope")
    if size.get("synthetic_results_authority") != "non-authoritative":
        raise OrientationBenchmarkError("orientation report must label synthetic results")
    diagnostics = cast(Mapping[str, object], report["orientation_diagnostics"])
    per_query = require_report_list(
        diagnostics.get("per_query"),
        "orientation_diagnostics.per_query",
    )
    if len(per_query) != query_count:
        raise OrientationBenchmarkError("orientation diagnostics must include every query id")
    for row in per_query:
        if not isinstance(row.get("query_id"), str) or not isinstance(row.get("case"), str):
            raise OrientationBenchmarkError("orientation diagnostics rows need query_id and case")
        if row.get("mode") not in {"plain-rrf", "orientation-seeded", "missing"}:
            raise OrientationBenchmarkError("orientation diagnostics row has unknown mode")
    gates = cast(Mapping[str, object], report["robustness_gates"])
    for field_name in (
        "passed_query_ids",
        "failed_query_ids",
        "residual_risk_query_ids",
        "production_code_blocker_query_ids",
    ):
        value = gates.get(field_name)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise OrientationBenchmarkError(f"robustness_gates.{field_name} must be string ids")
    case_assertions = cast(Mapping[str, object], report["case_assertions"])
    adversarial = require_report_list(
        case_assertions.get("adversarial_orientation_results"),
        "case_assertions.adversarial_orientation_results",
    )
    observed_cases = {str(row.get("case")) for row in adversarial}
    if observed_cases != ADVERSARIAL_CASES:
        raise OrientationBenchmarkError("adversarial orientation results must cover all cases")
    for row in adversarial:
        if row.get("status") not in {"pass", "fail", "residual_risk"}:
            raise OrientationBenchmarkError("adversarial orientation result has unknown status")


def load_query_cases(path: Path) -> list[QueryCase]:
    rows: list[QueryCase] = []
    for line_number, record in load_jsonl(path):
        label = f"{path.name}:{line_number}"
        language = require_string(record, "language", label)
        if language not in {"en", "ko"}:
            raise OrientationBenchmarkError("query language must be en or ko")
        rows.append(
            QueryCase(
                query_id=require_string(record, "query_id", label),
                language=cast(Literal["en", "ko"], language),
                case=require_string(record, "case", label),
                query=require_string(record, "query", label),
                target_page_id=require_string(record, "target_page_id", label),
            )
        )
    if not MIN_ORIENTATION_QUERY_COUNT <= len(rows) <= MAX_ORIENTATION_QUERY_COUNT:
        raise OrientationBenchmarkError(
            "orientation fixture must contain "
            f"{MIN_ORIENTATION_QUERY_COUNT}-{MAX_ORIENTATION_QUERY_COUNT} queries"
        )
    if {row.language for row in rows} != {"en", "ko"}:
        raise OrientationBenchmarkError("orientation fixture must split queries across en and ko")
    observed_adversarial = {row.case for row in rows if row.case in ADVERSARIAL_CASES}
    if observed_adversarial != ADVERSARIAL_CASES:
        missing = sorted(ADVERSARIAL_CASES - observed_adversarial)
        raise OrientationBenchmarkError(f"orientation fixture missing adversarial cases: {missing}")
    return rows


def load_qrels(path: Path) -> dict[str, dict[str, float]]:
    qrels: dict[str, dict[str, float]] = {}
    for line_number, record in load_jsonl(path):
        label = f"{path.name}:{line_number}"
        query_id = require_string(record, "query_id", label)
        page_id = require_string(record, "page_id", label)
        relevance = require_number(record, "relevance", label)
        qrels.setdefault(query_id, {})[page_id] = relevance
    return qrels


def validate_qrels(
    queries: Sequence[QueryCase],
    qrels: Mapping[str, Mapping[str, float]],
) -> None:
    query_ids = {query.query_id for query in queries}
    if set(qrels) != query_ids:
        raise OrientationBenchmarkError("qrels must exactly match query ids")
    for query in queries:
        if qrels[query.query_id].get(query.target_page_id, 0.0) <= 0:
            raise OrientationBenchmarkError("each query target_page_id must be relevant")


def load_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise OrientationBenchmarkError(f"{path.name}:{line_number} must be an object")
        rows.append((line_number, record))
    return rows


def require_string(record: Mapping[str, Any], key: str, label: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OrientationBenchmarkError(f"{label} missing non-empty string {key}")
    return value


def require_number(record: Mapping[str, Any], key: str, label: str) -> float:
    value = record.get(key)
    if not isinstance(value, int | float) or not math.isfinite(float(value)):
        raise OrientationBenchmarkError(f"{label} missing finite number {key}")
    return float(value)


def require_dir(path: Path, label: str) -> Path:
    if not path.is_dir():
        raise OrientationBenchmarkError(f"{label} must be an existing directory")
    return path


def rank_of(ranked_ids: Sequence[str], target_page_id: str) -> int | None:
    try:
        return ranked_ids.index(target_page_id) + 1
    except ValueError:
        return None


def rank_improved(plain_rank: int | None, hybrid_rank: int | None) -> bool:
    if hybrid_rank is None:
        return False
    if plain_rank is None:
        return True
    return hybrid_rank < plain_rank


def top_result_is_boilerplate(ranked_ids: Sequence[str]) -> bool:
    return bool(ranked_ids) and ranked_ids[0] in UNRELATED_BOILERPLATE_PAGE_IDS


def require_result_page_id(result: Mapping[str, Any]) -> str:
    value = result.get("page_id")
    if not isinstance(value, str) or not value:
        raise OrientationBenchmarkError("search result missing page_id")
    return value


def comparable_results(results: Sequence[Mapping[str, Any]]) -> list[tuple[str, float, str]]:
    comparable: list[tuple[str, float, str]] = []
    for item in results:
        page_id = require_result_page_id(item)
        score = item.get("score")
        route = item.get("route")
        if not isinstance(score, int | float) or not isinstance(route, str):
            raise OrientationBenchmarkError("search result missing comparable score/route")
        comparable.append((page_id, round(float(score), 4), route))
    return comparable


def loaded_vector_cache_hit(service: LlmWikiService) -> bool | None:
    loaded = list(service._loaded_vector_indexes.values())  # noqa: SLF001
    if not loaded:
        return None
    return loaded[-1].cache_hit


def vector_provider_report(runtime: VectorRuntime) -> dict[str, object]:
    metadata: dict[str, object] = dict(runtime.provider_metadata)
    metadata["model_revision_expected"] = DEFAULT_FASTEMBED_MODEL_REVISION
    return metadata


def embedding_telemetry_report(telemetry: EmbeddingTelemetry) -> dict[str, object]:
    return {
        "provider_init_ms": round_optional(telemetry.provider_init_ms),
        "document_calls": telemetry.document_calls,
        "document_texts": telemetry.document_texts,
        "document_chars": telemetry.document_chars,
        "document_latency_ms": percentile_distribution(telemetry.document_latency_ms),
        "query_calls": telemetry.query_calls,
        "query_chars": telemetry.query_chars,
        "query_latency_ms": percentile_distribution(telemetry.query_latency_ms),
        "model_cache_bytes_before": telemetry.model_cache_bytes_before,
        "model_cache_bytes_after": telemetry.model_cache_bytes_after,
        "model_download_bytes_observed": telemetry.model_download_bytes_observed,
        "model_download_policy": "never",
    }


def percentile_distribution(values: Sequence[int | float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "p50": 0.0, "p95": 0.0}
    sorted_values = sorted(float(value) for value in values)
    return {
        "count": len(sorted_values),
        "p50": round_measurement(percentile(sorted_values, 50)),
        "p95": round_measurement(percentile(sorted_values, 95)),
    }


def percentile(sorted_values: Sequence[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * (pct / 100.0)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def elapsed_ms(start_ns: int, end_ns: int) -> float:
    return (end_ns - start_ns) / 1_000_000.0


def round_measurement(value: float, *, digits: int = 3) -> float:
    return round(float(value), digits)


def round_optional(value: float | None) -> float | None:
    return None if value is None else round_measurement(value)


def serialized_payload_bytes(results: Sequence[Mapping[str, Any]]) -> int:
    return len(json.dumps(results, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def tree_size_bytes(root: Path | None) -> int:
    if root is None or not root.exists():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def compute_tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rel = path.relative_to(root).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def default_cache_root(repo_root: Path, output_report: Path, leaf: str) -> Path:
    digest = hashlib.sha256(output_report.name.encode("utf-8")).hexdigest()[:16]
    return (
        repo_root.resolve(strict=False)
        / ".llmwiki-work"
        / "benchmark-adapters"
        / "orientation-mechanism"
        / f"{output_report.stem}-{digest}"
        / leaf
    )


def implementation_metadata(repo_root: Path, revision: str) -> dict[str, object]:
    head = git_output(repo_root, "rev-parse", "HEAD")
    diff = git_output(repo_root, "diff", "--binary")
    diff_digest = "sha256:" + hashlib.sha256(diff.encode("utf-8")).hexdigest()
    return {
        "implementation_revision": revision,
        "git_head": f"git:{head}" if head else "unknown",
        "working_tree_dirty": bool(diff),
        "diff_digest": diff_digest,
    }


def default_implementation_revision(repo_root: Path) -> str:
    head = git_output(repo_root, "rev-parse", "HEAD")
    return f"git:{head}" if head else "unknown"


def git_output(repo_root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except Exception:
        return ""
    return completed.stdout.strip()


def runtime_environment_metadata() -> dict[str, object]:
    return {
        "os_family": platform.system() or "unknown",
        "os_release_family": platform.release().split("-", 1)[0] or "unknown",
        "python_version": platform.python_version(),
        "cpu_count_logical": os.cpu_count() or 0,
        "provider_runtime": "local-cpu-fastembed",
    }


def atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture-dir",
        type=Path,
        default=ROOT / "benchmarks" / "orientation_mechanism" / "fixture",
    )
    parser.add_argument("--output-report", type=Path, required=True)
    parser.add_argument("--vector-cache-root", type=Path, default=None)
    parser.add_argument("--vector-model-cache-root", type=Path, default=None)
    parser.add_argument("--implementation-revision", default=None)
    parser.add_argument("--analyzer-profile", choices=("english", "legacy"), default="english")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_orientation_mechanism_benchmark(
        fixture_dir=args.fixture_dir,
        output_report=args.output_report,
        vector_cache_root=args.vector_cache_root,
        vector_model_cache_root=args.vector_model_cache_root,
        analyzer_profile=cast(AnalyzerProfile, args.analyzer_profile),
        implementation_revision=args.implementation_revision,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
