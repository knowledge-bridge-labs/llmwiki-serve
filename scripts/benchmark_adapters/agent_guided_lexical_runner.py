"""Run the bounded agent-guided lexical benchmark from external plan JSONL.

The runner evaluates retrieval plumbing only. It never calls an LLM and it
never derives query variants from qrels. Agent-written variants are supplied in
a separate, versioned JSONL plan so another client can generate them from
source-only context before qrels exist.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
import platform
import re
import subprocess
import sys
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, TypeAlias, TypeVar, cast

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from llmwiki_serve import LlmWikiService  # noqa: E402
from llmwiki_serve import __version__ as LLMWIKI_SERVE_VERSION  # noqa: E402

REPORT_SCHEMA_ID = "llmwiki-agent-guided-lexical-benchmark-report-v1"
REPORT_SCHEMA_VERSION = "0.2.0"
PLAN_SCHEMA_ID = "llmwiki-agent-guided-lexical-plan-v1"
PLAN_PROVENANCE_SCHEMA_VERSION = "llmwiki.agent_plan_provenance.v1"
RUNNER_NAME = "agent-guided-lexical-benchmark-runner"
RUNNER_VERSION = "0.2.0"
RETRIEVAL_LIMIT = 5
RRF_K = 60
BENCHMARK_FIXED_INDEX_REFRESH_INTERVAL_SECONDS = 86_400.0
DEFAULT_FIXTURE_DIR = ROOT / "benchmarks" / "agent_guided_lexical" / "fixture"
SERVICE_INSTANCE_ISOLATION_METHOD = "factory_return_object_identity_unique_per_request"

CorpusId: TypeAlias = Literal["authored", "projection"]
BenchmarkArm: TypeAlias = Literal[
    "authored_raw_lexical",
    "authored_agent_guided_lexical",
    "projection_raw_lexical",
    "projection_sketch_agent_guided_lexical",
    "authored_raw_hybrid",
    "projection_raw_hybrid",
]
BenchmarkSearchMode: TypeAlias = Literal["lexical", "literal", "vector", "hybrid"]
Answerability: TypeAlias = Literal["answerable", "unanswerable", "unknown"]
Language: TypeAlias = Literal["en", "ko", "code"]
OrientationSource: TypeAlias = Literal["authored", "projection_extractive", "not_applicable"]
T = TypeVar("T")

CORPORA: tuple[CorpusId, ...] = ("authored", "projection")
LEXICAL_ARMS: tuple[BenchmarkArm, ...] = (
    "authored_raw_lexical",
    "authored_agent_guided_lexical",
    "projection_raw_lexical",
    "projection_sketch_agent_guided_lexical",
)
HYBRID_ARMS: tuple[BenchmarkArm, ...] = ("authored_raw_hybrid", "projection_raw_hybrid")
ALL_ARMS: tuple[BenchmarkArm, ...] = (*LEXICAL_ARMS, *HYBRID_ARMS)
AGENT_GUIDED_ARMS: frozenset[BenchmarkArm] = frozenset(
    {"authored_agent_guided_lexical", "projection_sketch_agent_guided_lexical"}
)
ARM_CORPUS: Mapping[BenchmarkArm, CorpusId] = {
    "authored_raw_lexical": "authored",
    "authored_agent_guided_lexical": "authored",
    "projection_raw_lexical": "projection",
    "projection_sketch_agent_guided_lexical": "projection",
    "authored_raw_hybrid": "authored",
    "projection_raw_hybrid": "projection",
}
EXPECTED_ORIENTATION_SOURCE: Mapping[CorpusId, OrientationSource] = {
    "authored": "authored",
    "projection": "projection_extractive",
}
RAW_LEXICAL_ARM_FOR_CORPUS: Mapping[CorpusId, BenchmarkArm] = {
    "authored": "authored_raw_lexical",
    "projection": "projection_raw_lexical",
}
AGENT_ARM_FOR_CORPUS: Mapping[CorpusId, BenchmarkArm] = {
    "authored": "authored_agent_guided_lexical",
    "projection": "projection_sketch_agent_guided_lexical",
}
HYBRID_ARM_FOR_CORPUS: Mapping[CorpusId, BenchmarkArm] = {
    "authored": "authored_raw_hybrid",
    "projection": "projection_raw_hybrid",
}

AUTHORED_ORIENTATION_FILENAMES = frozenset({"hot.md", "index.md", "overview.md"})
PLAN_ROW_FIELDS = frozenset(
    {"schema_id", "query_id", "arm", "primary_query", "query_variants", "usage", "provenance"}
)
PLAN_USAGE_FIELDS = frozenset({"input_tokens", "output_tokens"})
QREL_ROW_FIELDS = frozenset({"query_id", "page_id", "relevance"})
PLAN_PROVENANCE_FIELDS = frozenset(
    {
        "schema_version",
        "generator_kind",
        "model",
        "human_fixture_id",
        "prompt_template_revision",
        "prompt_template_sha256",
        "source_context_digest",
        "input_digest",
        "timestamp_utc",
        "stable_fixture_marker",
        "accounting_source",
    }
)
PLAN_QREL_LEAK_FIELDS = frozenset(
    {
        "answer",
        "citation",
        "citations",
        "corpus_id",
        "doc_id",
        "expected",
        "expected_answer",
        "expected_page_id",
        "gold",
        "gold_answer",
        "judgment",
        "page_id",
        "qrel",
        "qrels",
        "relevance",
        "score",
        "target",
        "target_doc_id",
        "target_page_id",
    }
)
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
IDENTIFIER_SEPARATOR_RE = re.compile(r"[\s_./\\-]+")
PRIVATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[A-Za-z]:[\\/][^\s\"']*"),
    re.compile(r"\\\\[A-Za-z0-9._$-]+\\[^\s\"']+"),
    re.compile(r"\bfile://[^\s\"']+", re.IGNORECASE),
    re.compile(
        r"(?<![A-Za-z0-9.:/\\-])/"
        r"(?:Users|home|root|tmp|var|mnt|media|workspace|raid|data|opt|srv)"
        r"(?:[\\/][^\s\"']*)?"
    ),
    re.compile(
        r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|10\.\d+\.\d+\.\d+|"
        r"192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)"
        r"(?:[:/][^\s\"']*)?",
        re.IGNORECASE,
    ),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}"),
)
SUPERIORITY_CLAIM_RE = re.compile(
    r"\b(?:outperform(?:s|ed)?|beats?|better than|superior|state[- ]of[- ]the[- ]art|sota|"
    r"leaderboard|best[- ]in[- ]class)\b",
    re.IGNORECASE,
)

REPORT_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_id",
        "schema_version",
        "runner",
        "benchmark_class",
        "dataset",
        "surface",
        "package_version",
        "implementation_revision",
        "runtime_environment",
        "fixture",
        "constraints",
        "arms",
        "per_query",
        "metric_definitions",
        "provenance",
        "limitations",
    }
)
ARM_REPORT_FIELDS = frozenset(
    {
        "corpus",
        "status",
        "retrieval_guidance_orientation_source",
        "metrics",
        "usage",
        "latency_ms",
        "payload_bytes",
        "limitations",
    }
)
METRIC_FIELDS = frozenset(
    {
        "nDCG@5",
        "Recall@5",
        "MAP@5",
        "citation_precision@5",
        "negative_false_positive_rate@5",
    }
)
USAGE_FIELDS = frozenset(
    {
        "cold_usage_cache",
        "cache_isolation",
        "public_context_requests",
        "public_search_requests",
        "internal_lexical_channel_evaluations",
        "adapter_search_calls",
        "read_calls",
        "variant_count",
        "query_character_count",
        "character_counting_method",
        "cold_usage_cache_evidence",
        "token_count_source",
        "input_tokens",
        "output_tokens",
        "accounting_source",
        "llm_calls",
        "service_instance_isolation_verified",
        "service_instance_isolation_method",
    }
)
DISTRIBUTION_FIELDS = frozenset({"count", "p50", "p95"})
PER_QUERY_ROW_FIELDS = frozenset(
    {
        "query_id",
        "answerability",
        "ranked_page_ids",
        "public_search_requests",
        "internal_lexical_channel_evaluations",
        "adapter_search_calls",
        "query_variant_count",
    }
)


class AgentGuidedLexicalRunnerError(RuntimeError):
    """Raised when the benchmark fixture, plan, or report is unsafe."""


class SearchService(Protocol):
    def context(
        self,
        query: str,
        *,
        limit: int = RETRIEVAL_LIMIT,
        mode: BenchmarkSearchMode = "lexical",
    ) -> object: ...

    def search(
        self,
        query: str,
        *,
        limit: int,
        mode: BenchmarkSearchMode = "lexical",
        query_variants: Sequence[Any] | None = None,
    ) -> list[dict[str, Any]]: ...


ServiceFactory = Callable[[Path], SearchService]
ClockNs = Callable[[], int]


@dataclass(frozen=True)
class FixturePaths:
    fixture_dir: Path
    queries: Path
    qrels: Path
    agent_plan: Path
    authored_wiki: Path
    projection_wiki: Path


@dataclass(frozen=True)
class QueryCase:
    query_id: str
    language: Language
    case: str
    query: str
    answerability: Answerability


@dataclass(frozen=True)
class AgentPlanProvenance:
    schema_version: str
    generator_kind: str
    model: str
    human_fixture_id: str
    prompt_template_revision: str
    prompt_template_sha256: str
    source_context_digest: str
    input_digest: str
    timestamp_utc: str
    stable_fixture_marker: bool
    accounting_source: str


@dataclass(frozen=True)
class AgentPlanRow:
    query_id: str
    arm: BenchmarkArm
    primary_query: str
    query_variants: tuple[str, ...]
    input_tokens: int
    output_tokens: int
    provenance: AgentPlanProvenance


@dataclass(frozen=True)
class PageIdentity:
    page_id: str
    path: str
    title: str


@dataclass(frozen=True)
class SearchCall:
    results: tuple[Mapping[str, Any], ...]
    latency_ms: float
    payload_bytes: int
    public_search_requests: int
    internal_lexical_channel_evaluations: int
    adapter_search_calls: int


@dataclass(frozen=True)
class ArmQueryRun:
    query_id: str
    answerability: Answerability
    ranked_page_ids: tuple[str, ...]
    public_search_requests: int
    internal_lexical_channel_evaluations: int
    adapter_search_calls: int
    query_variant_count: int
    query_character_count: int
    latency_ms: float
    payload_bytes: int
    public_context_requests: int


@dataclass(frozen=True)
class ArmRun:
    arm: BenchmarkArm
    corpus: CorpusId
    status: Literal["available", "skipped"]
    orientation_source: OrientationSource
    query_runs: tuple[ArmQueryRun, ...]
    metrics: Mapping[str, float] | None
    usage: Mapping[str, object]
    latency_ms: Mapping[str, object] | None
    payload_bytes: Mapping[str, object] | None
    limitations: tuple[str, ...] = ()


def default_service_factory(wiki_dir: Path) -> SearchService:
    return LlmWikiService(
        wiki_dir,
        refresh_interval_seconds=BENCHMARK_FIXED_INDEX_REFRESH_INTERVAL_SECONDS,
    )


class ServiceFactoryTracker:
    """Verifies each benchmark service request receives a distinct object."""

    def __init__(self, factory: ServiceFactory, *, label: str) -> None:
        self.factory = factory
        self.label = label
        self._seen_ids: set[int] = set()
        self.instances: list[SearchService] = []

    def __call__(self, wiki_dir: Path) -> SearchService:
        service = self.factory(wiki_dir)
        identity = id(service)
        if identity in self._seen_ids:
            raise AgentGuidedLexicalRunnerError(
                f"{self.label} reused service instance object across benchmark requests"
            )
        self._seen_ids.add(identity)
        self.instances.append(service)
        return service

    @property
    def service_instance_isolation_verified(self) -> bool:
        return True

    @property
    def service_instance_isolation_method(self) -> str:
        return SERVICE_INSTANCE_ISOLATION_METHOD


def run_agent_guided_lexical_benchmark(
    *,
    fixture_dir: Path = DEFAULT_FIXTURE_DIR,
    service_factory: ServiceFactory = default_service_factory,
    hybrid_service_factory: ServiceFactory | None = None,
    output_report: Path | None = None,
    implementation_revision: str | None = None,
    clock_ns: ClockNs = time.perf_counter_ns,
) -> dict[str, object]:
    paths = resolve_fixture_paths(fixture_dir)
    validate_fixture_separation(paths)
    validate_corpus_layout(paths)
    queries = load_query_cases(paths.queries)
    qrels = load_qrels(paths.qrels)
    corpus_pages: dict[CorpusId, dict[str, PageIdentity]] = {
        "authored": page_identities_from_wiki(paths.authored_wiki),
        "projection": page_identities_from_wiki(paths.projection_wiki),
    }
    corpus_page_ids: dict[CorpusId, frozenset[str]] = {
        corpus_id: frozenset(pages) for corpus_id, pages in corpus_pages.items()
    }
    validate_qrel_query_consistency(queries, qrels, corpus_page_ids)
    plan_rows = load_agent_plan(paths.agent_plan)
    validate_plan_coverage(queries, plan_rows)
    validate_no_positive_qrel_identifier_leakage(
        queries,
        plan_rows=plan_rows,
        qrels=qrels,
        corpus_pages=corpus_pages,
    )
    tracked_service_factory = ServiceFactoryTracker(
        service_factory,
        label="service_factory",
    )
    tracked_hybrid_service_factory = (
        None
        if hybrid_service_factory is None
        else ServiceFactoryTracker(hybrid_service_factory, label="hybrid_service_factory")
    )

    arm_runs: list[ArmRun] = []
    for corpus in CORPORA:
        wiki_dir = wiki_dir_for_corpus(paths, corpus)
        wiki_page_ids = corpus_page_ids[corpus]
        arm_runs.append(
            run_lexical_arm(
                tracked_service_factory,
                wiki_dir=wiki_dir,
                corpus=corpus,
                queries=queries,
                qrels=qrels,
                plan_rows=plan_rows,
                arm=RAW_LEXICAL_ARM_FOR_CORPUS[corpus],
                wiki_page_ids=wiki_page_ids,
                clock_ns=clock_ns,
            )
        )
        arm_runs.append(
            run_lexical_arm(
                tracked_service_factory,
                wiki_dir=wiki_dir,
                corpus=corpus,
                queries=queries,
                qrels=qrels,
                plan_rows=plan_rows,
                arm=AGENT_ARM_FOR_CORPUS[corpus],
                wiki_page_ids=wiki_page_ids,
                clock_ns=clock_ns,
            )
        )
        arm_runs.append(
            run_raw_hybrid_reference(
                tracked_hybrid_service_factory,
                wiki_dir=wiki_dir,
                corpus=corpus,
                queries=queries,
                qrels=qrels,
                wiki_page_ids=wiki_page_ids,
                clock_ns=clock_ns,
            )
        )

    implementation = implementation_revision_metadata(
        ROOT,
        override=implementation_revision,
    )
    report = build_report(
        paths=paths,
        queries=queries,
        qrels=qrels,
        corpus_page_ids=corpus_page_ids,
        plan_rows=plan_rows,
        arm_runs=tuple(arm_runs),
        implementation=implementation,
    )
    validate_runner_report(report)
    validate_report_public_safety(report)
    if output_report is not None:
        atomic_write_json(output_report, report)
    return report


def run_lexical_arm(
    service_factory: ServiceFactory,
    *,
    wiki_dir: Path,
    corpus: CorpusId,
    queries: Sequence[QueryCase],
    qrels: Mapping[str, Mapping[str, float]],
    plan_rows: Mapping[tuple[str, BenchmarkArm], AgentPlanRow],
    arm: BenchmarkArm,
    wiki_page_ids: frozenset[str],
    clock_ns: ClockNs,
) -> ArmRun:
    expected_orientation = EXPECTED_ORIENTATION_SOURCE[corpus]
    query_runs: list[ArmQueryRun] = []
    input_tokens = 0
    output_tokens = 0
    accounting_sources: set[str] = set()
    for query in queries:
        orientation_service = service_factory(wiki_dir)
        orientation_source = assert_service_orientation_source(
            orientation_service,
            query=query.query,
            expected=expected_orientation,
        )
        primary_query = query.query
        variants: tuple[str, ...] = ()
        if arm in AGENT_GUIDED_ARMS:
            row = plan_rows[(query.query_id, arm)]
            primary_query = row.primary_query
            variants = row.query_variants
            input_tokens += row.input_tokens
            output_tokens += row.output_tokens
            accounting_sources.add(row.provenance.accounting_source)
        channels = effective_channels(primary_query, variants)
        search_service = service_factory(wiki_dir)
        call = search_lexical(
            search_service,
            primary_query,
            channels[1:],
            limit=RETRIEVAL_LIMIT,
            clock_ns=clock_ns,
        )
        ranked_ids = tuple(result_page_id(item) for item in call.results[:RETRIEVAL_LIMIT])
        validate_original_page_ids(ranked_ids, wiki_page_ids)
        if orientation_source != expected_orientation:
            raise AgentGuidedLexicalRunnerError("unreachable orientation mismatch")
        query_runs.append(
            ArmQueryRun(
                query_id=query.query_id,
                answerability=query.answerability,
                ranked_page_ids=ranked_ids,
                public_search_requests=call.public_search_requests,
                internal_lexical_channel_evaluations=(call.internal_lexical_channel_evaluations),
                adapter_search_calls=call.adapter_search_calls,
                query_variant_count=max(0, len(channels) - 1),
                query_character_count=sum(len(channel) for channel in channels),
                latency_ms=call.latency_ms,
                payload_bytes=call.payload_bytes,
                public_context_requests=1,
            )
        )
    ranked = {run.query_id: run.ranked_page_ids for run in query_runs}
    return ArmRun(
        arm=arm,
        corpus=corpus,
        status="available",
        orientation_source=expected_orientation,
        query_runs=tuple(query_runs),
        metrics=aggregate_metrics(queries, ranked, qrels),
        usage=aggregate_usage(
            query_runs,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            token_count_source=(
                "external-agent-plan-jsonl" if arm in AGENT_GUIDED_ARMS else "not-applicable"
            ),
            accounting_source=(
                ",".join(sorted(accounting_sources)) if accounting_sources else "not-applicable"
            ),
        ),
        latency_ms=percentile_distribution([run.latency_ms for run in query_runs]),
        payload_bytes=percentile_distribution([run.payload_bytes for run in query_runs]),
    )


def run_raw_hybrid_reference(
    hybrid_service_factory: ServiceFactory | None,
    *,
    wiki_dir: Path,
    corpus: CorpusId,
    queries: Sequence[QueryCase],
    qrels: Mapping[str, Mapping[str, float]],
    wiki_page_ids: frozenset[str],
    clock_ns: ClockNs,
) -> ArmRun:
    arm = HYBRID_ARM_FOR_CORPUS[corpus]
    if hybrid_service_factory is None:
        return ArmRun(
            arm=arm,
            corpus=corpus,
            status="skipped",
            orientation_source="not_applicable",
            query_runs=(),
            metrics=None,
            usage=skipped_usage(),
            latency_ms=None,
            payload_bytes=None,
            limitations=(
                "raw_hybrid reference skipped because no explicit provider-backed service "
                "factory was supplied",
            ),
        )

    expected_orientation = EXPECTED_ORIENTATION_SOURCE[corpus]
    query_runs: list[ArmQueryRun] = []
    for query in queries:
        orientation_service = hybrid_service_factory(wiki_dir)
        assert_service_orientation_source(
            orientation_service,
            query=query.query,
            expected=expected_orientation,
        )
        search_service = hybrid_service_factory(wiki_dir)
        start_ns = clock_ns()
        results = tuple(search_service.search(query.query, limit=RETRIEVAL_LIMIT, mode="hybrid"))
        elapsed = elapsed_ms(start_ns, clock_ns())
        ranked_ids = tuple(result_page_id(item) for item in results[:RETRIEVAL_LIMIT])
        validate_original_page_ids(ranked_ids, wiki_page_ids)
        query_runs.append(
            ArmQueryRun(
                query_id=query.query_id,
                answerability=query.answerability,
                ranked_page_ids=ranked_ids,
                public_search_requests=1,
                internal_lexical_channel_evaluations=0,
                adapter_search_calls=1,
                query_variant_count=0,
                query_character_count=len(query.query),
                latency_ms=elapsed,
                payload_bytes=serialized_payload_bytes(results),
                public_context_requests=1,
            )
        )
    ranked = {run.query_id: run.ranked_page_ids for run in query_runs}
    return ArmRun(
        arm=arm,
        corpus=corpus,
        status="available",
        orientation_source=expected_orientation,
        query_runs=tuple(query_runs),
        metrics=aggregate_metrics(queries, ranked, qrels),
        usage=aggregate_usage(
            query_runs,
            input_tokens=0,
            output_tokens=0,
            token_count_source="not-applicable",
            accounting_source="not-applicable",
        ),
        latency_ms=percentile_distribution([run.latency_ms for run in query_runs]),
        payload_bytes=percentile_distribution([run.payload_bytes for run in query_runs]),
    )


def assert_service_orientation_source(
    service: SearchService,
    *,
    query: str,
    expected: OrientationSource,
) -> OrientationSource:
    context = service.context(query, limit=RETRIEVAL_LIMIT, mode="lexical")
    guidance = value_field(context, "retrieval_guidance")
    if guidance is None:
        raise AgentGuidedLexicalRunnerError("context missing retrieval_guidance")
    source = value_field(guidance, "orientation_source")
    if source != expected:
        raise AgentGuidedLexicalRunnerError(
            f"retrieval_guidance.orientation_source must be {expected!r}, got {source!r}"
        )
    return source


def value_field(value: object, field: str) -> object:
    if isinstance(value, Mapping):
        return value.get(field)
    return getattr(value, field, None)


def search_lexical(
    service: SearchService,
    primary_query: str,
    query_variants: Sequence[str],
    *,
    limit: int,
    clock_ns: ClockNs,
) -> SearchCall:
    channels = effective_channels(primary_query, query_variants)
    start_ns = clock_ns()
    if service_supports_query_variants(service):
        if len(channels) == 1:
            results = tuple(service.search(channels[0], limit=limit, mode="lexical"))
        else:
            results = tuple(
                service.search(
                    channels[0],
                    limit=limit,
                    mode="lexical",
                    query_variants=channels[1:],
                )
            )
        return SearchCall(
            results=results,
            latency_ms=elapsed_ms(start_ns, clock_ns()),
            payload_bytes=serialized_payload_bytes(results),
            public_search_requests=1,
            internal_lexical_channel_evaluations=len(channels),
            adapter_search_calls=1,
        )

    channel_results: list[tuple[Mapping[str, Any], ...]] = []
    for channel in channels:
        channel_results.append(tuple(service.search(channel, limit=limit, mode="lexical")))
    fused = tuple(rrf_fuse(channel_results, limit=limit))
    return SearchCall(
        results=fused,
        latency_ms=elapsed_ms(start_ns, clock_ns()),
        payload_bytes=serialized_payload_bytes(fused),
        public_search_requests=1,
        internal_lexical_channel_evaluations=len(channels),
        adapter_search_calls=len(channels),
    )


def service_supports_query_variants(service: SearchService) -> bool:
    try:
        signature = inspect.signature(service.search)
    except (TypeError, ValueError):
        return True
    return "query_variants" in signature.parameters


def rrf_fuse(
    result_lists: Sequence[Sequence[Mapping[str, Any]]],
    *,
    limit: int,
) -> list[Mapping[str, Any]]:
    scores: dict[str, float] = {}
    first_seen: dict[str, tuple[int, int]] = {}
    payloads: dict[str, Mapping[str, Any]] = {}
    for channel_index, results in enumerate(result_lists):
        for rank_index, result in enumerate(results):
            page_id = result_page_id(result)
            scores[page_id] = scores.get(page_id, 0.0) + 1.0 / (RRF_K + rank_index + 1)
            first_seen.setdefault(page_id, (channel_index, rank_index))
            payloads.setdefault(page_id, result)
    ordered = sorted(
        payloads,
        key=lambda page_id: (
            -scores[page_id],
            first_seen[page_id][0],
            first_seen[page_id][1],
            page_id,
        ),
    )
    fused: list[Mapping[str, Any]] = []
    for page_id in ordered[:limit]:
        payload = dict(payloads[page_id])
        payload["score"] = scores[page_id]
        fused.append(payload)
    return fused


def build_report(
    *,
    paths: FixturePaths,
    queries: Sequence[QueryCase],
    qrels: Mapping[str, Mapping[str, float]],
    corpus_page_ids: Mapping[CorpusId, frozenset[str]],
    plan_rows: Mapping[tuple[str, BenchmarkArm], AgentPlanRow],
    arm_runs: Sequence[ArmRun],
    implementation: Mapping[str, object],
) -> dict[str, object]:
    answerable_count = sum(1 for query in queries if query.answerability == "answerable")
    negative_count = sum(1 for query in queries if query.answerability == "unanswerable")
    positive_qrel_count = sum(
        1 for query_qrels in qrels.values() for relevance in query_qrels.values() if relevance > 0
    )
    arms_by_name = {run.arm: run for run in arm_runs}
    return {
        "schema_id": REPORT_SCHEMA_ID,
        "schema_version": REPORT_SCHEMA_VERSION,
        "runner": {"name": RUNNER_NAME, "version": RUNNER_VERSION},
        "benchmark_class": "bounded-engineering-harness",
        "dataset": "agent-guided-lexical-curated-fixture-v2",
        "surface": "SearchService.search(query, mode='lexical', optional query_variants)",
        "package_version": LLMWIKI_SERVE_VERSION,
        "implementation_revision": require_report_string(
            implementation.get("revision"),
            "provenance.implementation.revision",
        ),
        "runtime_environment": runtime_environment_metadata(),
        "fixture": {
            "path": "benchmarks/agent_guided_lexical/fixture",
            "content_sha256": compute_tree_digest(paths.fixture_dir),
            "queries_sha256": compute_file_digest(paths.queries),
            "qrels_sha256": compute_file_digest(paths.qrels),
            "agent_plan_sha256": compute_file_digest(paths.agent_plan),
            "corpora": {
                "authored": {
                    "path": "benchmarks/agent_guided_lexical/fixture/authored/wiki",
                    "content_sha256": compute_tree_digest(paths.authored_wiki),
                    "orientation_source_expected": "authored",
                    "page_count": len(corpus_page_ids["authored"]),
                },
                "projection": {
                    "path": "benchmarks/agent_guided_lexical/fixture/projection/wiki",
                    "content_sha256": compute_tree_digest(paths.projection_wiki),
                    "orientation_source_expected": "projection_extractive",
                    "page_count": len(corpus_page_ids["projection"]),
                },
            },
            "query_counts": {
                "total": len(queries),
                "evaluated": answerable_count,
                "negative": negative_count,
            },
            "qrel_counts": {
                "total": sum(len(items) for items in qrels.values()),
                "positive": positive_qrel_count,
            },
            "agent_plan_row_count": len(plan_rows),
            "languages": sorted({query.language for query in queries}),
            "cases": sorted({query.case for query in queries}),
        },
        "constraints": {
            "runner_calls_llm": False,
            "llm_call_count": 0,
            "variants_generated_from_qrels": False,
            "max_query_variants": 2,
            "source_qrels_separated": True,
            "original_page_citations_only": True,
            "public_superiority_claim": False,
            "raw_hybrid_requires_explicit_provider": True,
            "semantic_leakage_mechanically_proven": False,
            "release_evidence_requires_independent_source_only_plans_before_qrels": True,
            "fixture_is_release_evidence": False,
            "cold_cache_mechanically_verified": False,
            "service_instance_isolation_verified": True,
            "service_instance_isolation_method": SERVICE_INSTANCE_ISOLATION_METHOD,
        },
        "arms": {arm: arm_report_payload(arms_by_name[arm]) for arm in ALL_ARMS},
        "per_query": {
            arm: [per_query_payload(run) for run in arms_by_name[arm].query_runs]
            for arm in ALL_ARMS
        },
        "metric_definitions": metric_definitions(),
        "provenance": build_provenance(paths, plan_rows=plan_rows, implementation=implementation),
        "limitations": [
            "This is a small deterministic engineering harness, not a public benchmark claim.",
            (
                "The runner rejects explicit qrel identifiers in plans, but semantic leakage "
                "cannot be mechanically proven absent."
            ),
            (
                "Release evidence must use independently generated source-only plans before "
                "qrels are available."
            ),
            (
                "Agent variants are supplied by external plan JSONL and are not generated by "
                "this runner."
            ),
            "Hybrid is a reference arm only when an explicit provider-backed service is supplied.",
            "Dirty-worktree reports are engineering diagnostics only.",
        ],
    }


def arm_report_payload(run: ArmRun) -> dict[str, object]:
    if run.status == "skipped":
        return {
            "corpus": run.corpus,
            "status": run.status,
            "retrieval_guidance_orientation_source": run.orientation_source,
            "metrics": None,
            "usage": dict(run.usage),
            "latency_ms": None,
            "payload_bytes": None,
            "limitations": list(run.limitations),
        }
    return {
        "corpus": run.corpus,
        "status": run.status,
        "retrieval_guidance_orientation_source": run.orientation_source,
        "metrics": dict(assert_not_none(run.metrics, "available arm metrics")),
        "usage": dict(run.usage),
        "latency_ms": dict(assert_not_none(run.latency_ms, "available arm latency_ms")),
        "payload_bytes": dict(assert_not_none(run.payload_bytes, "available arm payload_bytes")),
        "limitations": list(run.limitations),
    }


def assert_not_none(value: T | None, label: str) -> T:
    if value is None:
        raise AgentGuidedLexicalRunnerError(f"{label} must be present")
    return value


def per_query_payload(run: ArmQueryRun) -> dict[str, object]:
    return {
        "query_id": run.query_id,
        "answerability": run.answerability,
        "ranked_page_ids": list(run.ranked_page_ids),
        "public_search_requests": run.public_search_requests,
        "internal_lexical_channel_evaluations": run.internal_lexical_channel_evaluations,
        "adapter_search_calls": run.adapter_search_calls,
        "query_variant_count": run.query_variant_count,
    }


def aggregate_usage(
    query_runs: Sequence[ArmQueryRun],
    *,
    input_tokens: int,
    output_tokens: int,
    token_count_source: str,
    accounting_source: str,
) -> dict[str, object]:
    return {
        "cold_usage_cache": None,
        "cold_usage_cache_evidence": "unknown",
        "cache_isolation": "unknown",
        "service_instance_isolation_verified": True,
        "service_instance_isolation_method": SERVICE_INSTANCE_ISOLATION_METHOD,
        "public_context_requests": sum(run.public_context_requests for run in query_runs),
        "public_search_requests": sum(run.public_search_requests for run in query_runs),
        "internal_lexical_channel_evaluations": sum(
            run.internal_lexical_channel_evaluations for run in query_runs
        ),
        "adapter_search_calls": sum(run.adapter_search_calls for run in query_runs),
        "read_calls": 0,
        "variant_count": sum(run.query_variant_count for run in query_runs),
        "query_character_count": sum(run.query_character_count for run in query_runs),
        "character_counting_method": "unicode code points after JSON decoding",
        "token_count_source": token_count_source,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "accounting_source": accounting_source,
        "llm_calls": 0,
    }


def skipped_usage() -> dict[str, object]:
    return {
        "cold_usage_cache": None,
        "cold_usage_cache_evidence": "not-applicable",
        "cache_isolation": "not-applicable",
        "service_instance_isolation_verified": False,
        "service_instance_isolation_method": "not-applicable",
        "public_context_requests": 0,
        "public_search_requests": 0,
        "internal_lexical_channel_evaluations": 0,
        "adapter_search_calls": 0,
        "read_calls": 0,
        "variant_count": 0,
        "query_character_count": 0,
        "character_counting_method": "not-applicable",
        "token_count_source": "not-applicable",
        "input_tokens": 0,
        "output_tokens": 0,
        "accounting_source": "not-applicable",
        "llm_calls": 0,
    }


def build_provenance(
    paths: FixturePaths,
    *,
    plan_rows: Mapping[tuple[str, BenchmarkArm], AgentPlanRow],
    implementation: Mapping[str, object],
) -> dict[str, object]:
    rows = tuple(plan_rows.values())
    return {
        "implementation": dict(implementation),
        "inputs": {
            "fixture_tree_sha256": compute_tree_digest(paths.fixture_dir),
            "authored_corpus_sha256": compute_tree_digest(paths.authored_wiki),
            "projection_corpus_sha256": compute_tree_digest(paths.projection_wiki),
            "queries_sha256": compute_file_digest(paths.queries),
            "qrels_sha256": compute_file_digest(paths.qrels),
            "agent_plan_sha256": compute_file_digest(paths.agent_plan),
        },
        "plan": {
            "schema_id": PLAN_SCHEMA_ID,
            "provenance_schema_version": PLAN_PROVENANCE_SCHEMA_VERSION,
            "row_count": len(rows),
            "generator_kinds": sorted({row.provenance.generator_kind for row in rows}),
            "models": sorted({row.provenance.model for row in rows}),
            "human_fixture_ids": sorted({row.provenance.human_fixture_id for row in rows}),
            "prompt_template_revisions": sorted(
                {row.provenance.prompt_template_revision for row in rows}
            ),
            "prompt_template_sha256": sorted(
                {row.provenance.prompt_template_sha256 for row in rows}
            ),
            "source_context_digests": sorted(
                {row.provenance.source_context_digest for row in rows}
            ),
            "input_digests": sorted({row.provenance.input_digest for row in rows}),
            "timestamp_utc_values": sorted({row.provenance.timestamp_utc for row in rows}),
            "stable_fixture_marker": all(row.provenance.stable_fixture_marker for row in rows),
            "accounting_sources": sorted({row.provenance.accounting_source for row in rows}),
        },
    }


def aggregate_metrics(
    queries: Sequence[QueryCase],
    ranked: Mapping[str, Sequence[str]],
    qrels: Mapping[str, Mapping[str, float]],
) -> dict[str, float]:
    answerable_queries = [
        query
        for query in queries
        if query.answerability == "answerable" and has_positive_qrels(qrels, query.query_id)
    ]
    negative_queries = [query for query in queries if query.answerability == "unanswerable"]
    quality = {
        "nDCG@5": mean(
            ndcg_at_k(ranked.get(query.query_id, ()), qrels[query.query_id], 5)
            for query in answerable_queries
        ),
        "Recall@5": mean(
            recall_at_k(ranked.get(query.query_id, ()), qrels[query.query_id], 5)
            for query in answerable_queries
        ),
        "MAP@5": mean(
            map_at_k(ranked.get(query.query_id, ()), qrels[query.query_id], 5)
            for query in answerable_queries
        ),
        "citation_precision@5": mean(
            citation_precision_at_k(ranked.get(query.query_id, ()), qrels[query.query_id], 5)
            for query in answerable_queries
        ),
    }
    if negative_queries:
        false_positive_rate = mean(
            1.0 if ranked.get(query.query_id, ())[:5] else 0.0 for query in negative_queries
        )
    else:
        false_positive_rate = 0.0
    quality["negative_false_positive_rate@5"] = round_metric(false_positive_rate)
    return {name: round_metric(value) for name, value in quality.items()}


def empty_metrics() -> dict[str, float]:
    return {
        "nDCG@5": 0.0,
        "Recall@5": 0.0,
        "MAP@5": 0.0,
        "citation_precision@5": 0.0,
        "negative_false_positive_rate@5": 0.0,
    }


def ndcg_at_k(ranked_ids: Sequence[str], qrels: Mapping[str, float], cutoff: int) -> float:
    ideal = sorted((value for value in qrels.values() if value > 0), reverse=True)[:cutoff]
    if not ideal:
        return 0.0
    observed = [qrels.get(page_id, 0.0) for page_id in ranked_ids[:cutoff]]
    return dcg(observed) / dcg(ideal)


def dcg(relevances: Sequence[float]) -> float:
    return sum(relevance / math.log2(index + 2) for index, relevance in enumerate(relevances))


def recall_at_k(ranked_ids: Sequence[str], qrels: Mapping[str, float], cutoff: int) -> float:
    positive = {page_id for page_id, relevance in qrels.items() if relevance > 0}
    if not positive:
        return 0.0
    return len(positive.intersection(ranked_ids[:cutoff])) / len(positive)


def map_at_k(ranked_ids: Sequence[str], qrels: Mapping[str, float], cutoff: int) -> float:
    positive = {page_id for page_id, relevance in qrels.items() if relevance > 0}
    if not positive:
        return 0.0
    hits = 0
    precision_sum = 0.0
    for index, page_id in enumerate(ranked_ids[:cutoff], start=1):
        if page_id not in positive:
            continue
        hits += 1
        precision_sum += hits / index
    return precision_sum / min(len(positive), cutoff)


def citation_precision_at_k(
    ranked_ids: Sequence[str],
    qrels: Mapping[str, float],
    cutoff: int,
) -> float:
    emitted = list(ranked_ids[:cutoff])
    if not emitted:
        return 0.0
    positive = {page_id for page_id, relevance in qrels.items() if relevance > 0}
    return len(positive.intersection(emitted)) / len(emitted)


def resolve_fixture_paths(fixture_dir: Path) -> FixturePaths:
    fixture = fixture_dir.resolve(strict=True)
    return FixturePaths(
        fixture_dir=fixture,
        queries=(fixture / "queries.jsonl").resolve(strict=True),
        qrels=(fixture / "qrels.jsonl").resolve(strict=True),
        agent_plan=(fixture / "agent-plan.jsonl").resolve(strict=True),
        authored_wiki=require_dir(fixture / "authored" / "wiki", "authored fixture wiki"),
        projection_wiki=require_dir(fixture / "projection" / "wiki", "projection fixture wiki"),
    )


def wiki_dir_for_corpus(paths: FixturePaths, corpus: CorpusId) -> Path:
    if corpus == "authored":
        return paths.authored_wiki
    return paths.projection_wiki


def load_query_cases(path: Path) -> tuple[QueryCase, ...]:
    cases: list[QueryCase] = []
    seen: set[str] = set()
    for line_number, record in load_jsonl(path):
        query_id = require_string(record, "query_id", path, line_number)
        if query_id in seen:
            raise AgentGuidedLexicalRunnerError(f"{path}:{line_number}: duplicate query_id")
        seen.add(query_id)
        language = require_enum(record, "language", {"en", "ko", "code"}, path, line_number)
        answerability = require_enum(
            record,
            "answerability",
            {"answerable", "unanswerable", "unknown"},
            path,
            line_number,
        )
        case = require_string(record, "case", path, line_number)
        query = require_string(record, "query", path, line_number)
        validate_public_safe_text(query, path=path, line_number=line_number, field="query")
        cases.append(
            QueryCase(
                query_id=query_id,
                language=cast(Language, language),
                case=case,
                query=query,
                answerability=cast(Answerability, answerability),
            )
        )
    if not cases:
        raise AgentGuidedLexicalRunnerError("fixture queries.jsonl must not be empty")
    return tuple(cases)


def load_qrels(path: Path) -> dict[str, dict[str, float]]:
    qrels: dict[str, dict[str, float]] = {}
    seen: set[tuple[str, str]] = set()
    for line_number, record in load_jsonl(path):
        require_exact_keys(record, f"{path}:{line_number}", QREL_ROW_FIELDS)
        query_id = require_string(record, "query_id", path, line_number)
        page_id = require_string(record, "page_id", path, line_number)
        key = (query_id, page_id)
        if key in seen:
            raise AgentGuidedLexicalRunnerError(
                f"{path}:{line_number}: duplicate qrel row for {query_id!r} {page_id!r}"
            )
        seen.add(key)
        relevance = require_number(record, "relevance", path, line_number)
        qrels.setdefault(query_id, {})[page_id] = relevance
    return qrels


def load_agent_plan(path: Path) -> dict[tuple[str, BenchmarkArm], AgentPlanRow]:
    rows = tuple(
        parse_plan_row(line_number, record, path) for line_number, record in load_jsonl(path)
    )
    validate_plan_rows(rows)
    return {(row.query_id, row.arm): row for row in rows}


def parse_plan_row(line_number: int, record: Mapping[str, Any], path: Path) -> AgentPlanRow:
    require_exact_keys(record, f"{path}:{line_number}", PLAN_ROW_FIELDS)
    schema_id = require_string(record, "schema_id", path, line_number)
    if schema_id != PLAN_SCHEMA_ID:
        raise AgentGuidedLexicalRunnerError(f"{path}:{line_number}: unsupported schema_id")
    arm = require_enum(record, "arm", set(AGENT_GUIDED_ARMS), path, line_number)
    usage = require_object(record.get("usage"), "usage", path, line_number)
    require_exact_keys(usage, f"{path}:{line_number}: usage", PLAN_USAGE_FIELDS)
    variants_value = record.get("query_variants", [])
    if not isinstance(variants_value, list):
        raise AgentGuidedLexicalRunnerError(
            f"{path}:{line_number}: query_variants must be an array"
        )
    variants = tuple(validate_variant_list(variants_value, path=path, line_number=line_number))
    primary_query = require_string(record, "primary_query", path, line_number)
    validate_public_safe_text(
        primary_query, path=path, line_number=line_number, field="primary_query"
    )
    for index, variant in enumerate(variants):
        validate_public_safe_text(
            variant,
            path=path,
            line_number=line_number,
            field=f"query_variants[{index}]",
        )
    provenance = parse_plan_provenance(
        require_object(record.get("provenance"), "provenance", path, line_number),
        path=path,
        line_number=line_number,
    )
    return AgentPlanRow(
        query_id=require_string(record, "query_id", path, line_number),
        arm=cast(BenchmarkArm, arm),
        primary_query=primary_query,
        query_variants=variants,
        input_tokens=require_nonnegative_int(
            usage.get("input_tokens", 0), "usage.input_tokens", path, line_number
        ),
        output_tokens=require_nonnegative_int(
            usage.get("output_tokens", 0), "usage.output_tokens", path, line_number
        ),
        provenance=provenance,
    )


def parse_plan_provenance(
    value: Mapping[str, Any],
    *,
    path: Path,
    line_number: int,
) -> AgentPlanProvenance:
    require_exact_keys(value, f"{path}:{line_number}: provenance", PLAN_PROVENANCE_FIELDS)
    schema_version = require_literal_string(
        value,
        "schema_version",
        PLAN_PROVENANCE_SCHEMA_VERSION,
        path,
        line_number,
    )
    generator_kind = require_enum(
        value,
        "generator_kind",
        {"external_agent", "llm", "human_fixture"},
        path,
        line_number,
    )
    model = require_string(value, "model", path, line_number)
    human_fixture_id = require_string(value, "human_fixture_id", path, line_number)
    if generator_kind == "human_fixture" and human_fixture_id == "not-applicable":
        raise AgentGuidedLexicalRunnerError(
            f"{path}:{line_number}: human_fixture provenance requires human_fixture_id"
        )
    if generator_kind != "human_fixture" and model == "not-applicable":
        raise AgentGuidedLexicalRunnerError(
            f"{path}:{line_number}: generated plans require a model identifier"
        )
    prompt_template_revision = require_string(
        value,
        "prompt_template_revision",
        path,
        line_number,
    )
    prompt_template_sha256 = require_digest_string(
        value,
        "prompt_template_sha256",
        path,
        line_number,
    )
    source_context_digest = require_digest_string(
        value,
        "source_context_digest",
        path,
        line_number,
    )
    input_digest = require_digest_string(value, "input_digest", path, line_number)
    timestamp_utc = require_string(value, "timestamp_utc", path, line_number)
    stable_fixture_marker = require_bool(value, "stable_fixture_marker", path, line_number)
    if stable_fixture_marker:
        if timestamp_utc != "stable-fixture":
            raise AgentGuidedLexicalRunnerError(
                f"{path}:{line_number}: stable fixtures must use timestamp_utc=stable-fixture"
            )
    elif TIMESTAMP_RE.match(timestamp_utc) is None:
        raise AgentGuidedLexicalRunnerError(
            f"{path}:{line_number}: timestamp_utc must be ISO-8601 UTC seconds"
        )
    accounting_source = require_string(value, "accounting_source", path, line_number)
    return AgentPlanProvenance(
        schema_version=schema_version,
        generator_kind=generator_kind,
        model=model,
        human_fixture_id=human_fixture_id,
        prompt_template_revision=prompt_template_revision,
        prompt_template_sha256=prompt_template_sha256,
        source_context_digest=source_context_digest,
        input_digest=input_digest,
        timestamp_utc=timestamp_utc,
        stable_fixture_marker=stable_fixture_marker,
        accounting_source=accounting_source,
    )


def validate_plan_rows(rows: Sequence[AgentPlanRow]) -> None:
    seen: set[tuple[str, BenchmarkArm]] = set()
    for row in rows:
        key = (row.query_id, row.arm)
        if key in seen:
            raise AgentGuidedLexicalRunnerError(
                f"duplicate agent plan row for {row.query_id} {row.arm}"
            )
        seen.add(key)
        effective_channels(row.primary_query, row.query_variants)


def validate_plan_record_has_no_qrel_leak(
    record: Mapping[str, Any], *, path: Path, line_number: int
) -> None:
    leaked = sorted(recursive_forbidden_plan_keys(record))
    if leaked:
        raise AgentGuidedLexicalRunnerError(
            f"{path}:{line_number}: agent plan must not contain qrel/citation fields: "
            f"{', '.join(leaked)}"
        )


def recursive_forbidden_plan_keys(value: object) -> set[str]:
    leaked: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key).casefold()
            if key_text in PLAN_QREL_LEAK_FIELDS:
                leaked.add(str(key))
            leaked.update(recursive_forbidden_plan_keys(item))
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for item in value:
            leaked.update(recursive_forbidden_plan_keys(item))
    return leaked


def validate_plan_coverage(
    queries: Sequence[QueryCase],
    plan_rows: Mapping[tuple[str, BenchmarkArm], AgentPlanRow],
) -> None:
    required = {(query.query_id, arm) for query in queries for arm in AGENT_GUIDED_ARMS}
    missing = sorted(required.difference(plan_rows))
    if missing:
        raise AgentGuidedLexicalRunnerError(f"agent plan missing rows: {missing[:5]}")
    extras = sorted(set(plan_rows).difference(required))
    if extras:
        raise AgentGuidedLexicalRunnerError(f"agent plan contains unknown rows: {extras[:5]}")


def validate_variant_list(values: Sequence[Any], *, path: Path, line_number: int) -> list[str]:
    if len(values) > 2:
        raise AgentGuidedLexicalRunnerError(f"{path}:{line_number}: query_variants maxItems is 2")
    variants: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise AgentGuidedLexicalRunnerError(
                f"{path}:{line_number}: query_variants entries must be strings"
            )
        text = value.strip()
        if not text:
            raise AgentGuidedLexicalRunnerError(
                f"{path}:{line_number}: query_variants entries must be non-empty"
            )
        variants.append(text)
    return variants


def validate_qrel_query_consistency(
    queries: Sequence[QueryCase],
    qrels: Mapping[str, Mapping[str, float]],
    corpus_page_ids: Mapping[CorpusId, frozenset[str]],
) -> None:
    query_by_id = {query.query_id: query for query in queries}
    unknown_qrels = sorted(set(qrels).difference(query_by_id))
    if unknown_qrels:
        raise AgentGuidedLexicalRunnerError(f"qrels contain unknown query ids: {unknown_qrels[:5]}")
    unknown_answerability = sorted(
        query.query_id for query in queries if query.answerability == "unknown"
    )
    if unknown_answerability:
        raise AgentGuidedLexicalRunnerError(
            f"unknown answerability is not evaluable: {unknown_answerability[:5]}"
        )
    common_page_ids = set.intersection(*(set(page_ids) for page_ids in corpus_page_ids.values()))
    unknown_page_ids = sorted(
        page_id
        for query_qrels in qrels.values()
        for page_id in query_qrels
        if page_id not in common_page_ids
    )
    if unknown_page_ids:
        raise AgentGuidedLexicalRunnerError(
            f"qrels reference page ids missing from every corpus: {unknown_page_ids[:5]}"
        )
    for query in queries:
        query_qrels = qrels.get(query.query_id, {})
        if query.answerability == "answerable" and not any(
            relevance > 0 for relevance in query_qrels.values()
        ):
            raise AgentGuidedLexicalRunnerError(
                f"answerable query {query.query_id!r} must have a positive qrel"
            )
        if query.answerability == "unanswerable" and query_qrels:
            raise AgentGuidedLexicalRunnerError(
                f"unanswerable query {query.query_id!r} must not have qrels"
            )


def validate_no_positive_qrel_identifier_leakage(
    queries: Sequence[QueryCase],
    *,
    plan_rows: Mapping[tuple[str, BenchmarkArm], AgentPlanRow],
    qrels: Mapping[str, Mapping[str, float]],
    corpus_pages: Mapping[CorpusId, Mapping[str, PageIdentity]],
) -> None:
    positive_keys = positive_qrel_identifier_keys(qrels, corpus_pages)
    for query in queries:
        reject_if_positive_identifier_match(
            query.query,
            positive_keys,
            label=f"queries.jsonl query {query.query_id}",
        )
    for row in plan_rows.values():
        reject_if_positive_identifier_match(
            row.primary_query,
            positive_keys,
            label=f"agent plan primary_query {row.query_id} {row.arm}",
        )
        for index, variant in enumerate(row.query_variants):
            reject_if_positive_identifier_match(
                variant,
                positive_keys,
                label=f"agent plan query_variants[{index}] {row.query_id} {row.arm}",
            )


def positive_qrel_identifier_keys(
    qrels: Mapping[str, Mapping[str, float]],
    corpus_pages: Mapping[CorpusId, Mapping[str, PageIdentity]],
) -> frozenset[str]:
    keys: set[str] = set()
    for query_qrels in qrels.values():
        for page_id, relevance in query_qrels.items():
            if relevance <= 0:
                continue
            for pages in corpus_pages.values():
                page = pages.get(page_id)
                if page is None:
                    continue
                for value in (page.page_id, page.path, Path(page.path).name, page.title):
                    keys.update(identifier_match_keys(value))
    return frozenset(keys)


def reject_if_positive_identifier_match(
    text: str,
    positive_identifier_keys: frozenset[str],
    *,
    label: str,
) -> None:
    matches = identifier_match_keys(text).intersection(positive_identifier_keys)
    if matches:
        raise AgentGuidedLexicalRunnerError(
            f"{label} must not match positive qrel identifier/path/page-id"
        )


def identifier_match_keys(text: str) -> frozenset[str]:
    normalized = normalize_identifier_key(text)
    return frozenset({normalized, IDENTIFIER_SEPARATOR_RE.sub("", normalized)})


def normalize_identifier_key(text: str) -> str:
    value = unicodedata.normalize("NFC", text).casefold().strip()
    value = value.strip("\"'` ")
    value = value.replace("\\", "/")
    value = re.sub(r"/+", "/", value)
    value = value.removeprefix("./").strip("/")
    if value.endswith(".md"):
        value = value[:-3]
    return value


def effective_channels(primary_query: str, query_variants: Sequence[str]) -> tuple[str, ...]:
    primary = primary_query.strip()
    if not primary:
        raise AgentGuidedLexicalRunnerError("primary_query must be non-empty")
    if len(query_variants) > 2:
        raise AgentGuidedLexicalRunnerError("query_variants maxItems is 2")
    channels: list[str] = []
    seen: set[str] = set()
    for value in (primary, *query_variants):
        text = value.strip()
        if not text:
            raise AgentGuidedLexicalRunnerError("query channel must be non-empty")
        key = normalize_channel_key(text)
        if key in seen:
            continue
        seen.add(key)
        channels.append(text)
    return tuple(channels[:3])


def normalize_channel_key(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def load_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise AgentGuidedLexicalRunnerError(f"failed to read {path}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AgentGuidedLexicalRunnerError(f"{path}:{line_number}: invalid JSON") from exc
        if not isinstance(value, dict):
            raise AgentGuidedLexicalRunnerError(f"{path}:{line_number}: row must be a JSON object")
        if path.name == "agent-plan.jsonl":
            validate_plan_record_has_no_qrel_leak(value, path=path, line_number=line_number)
        rows.append((line_number, cast(dict[str, Any], value)))
    return rows


def validate_fixture_separation(paths: FixturePaths) -> None:
    for wiki_dir in (paths.authored_wiki, paths.projection_wiki):
        for artifact in (paths.qrels, paths.queries, paths.agent_plan):
            if is_same_or_nested(artifact, wiki_dir):
                raise AgentGuidedLexicalRunnerError(
                    "queries, qrels, and agent plans must stay outside served source wiki "
                    "directories"
                )
        forbidden = [
            path.relative_to(wiki_dir).as_posix()
            for path in wiki_dir.rglob("*")
            if path.is_file() and path.name.casefold() in {"qrels.jsonl", "queries.jsonl"}
        ]
        if forbidden:
            raise AgentGuidedLexicalRunnerError(
                f"served source wiki must not contain qrel/query files: {forbidden[:5]}"
            )


def validate_corpus_layout(paths: FixturePaths) -> None:
    if is_same_or_nested(paths.authored_wiki, paths.projection_wiki) or is_same_or_nested(
        paths.projection_wiki,
        paths.authored_wiki,
    ):
        raise AgentGuidedLexicalRunnerError("authored and projection corpora must be separate")
    authored_root_pages = {
        path.name.casefold() for path in paths.authored_wiki.iterdir() if path.is_file()
    }
    if not authored_root_pages.intersection(AUTHORED_ORIENTATION_FILENAMES):
        raise AgentGuidedLexicalRunnerError(
            "authored corpus must include a root hot/index/overview orientation page"
        )
    projection_orientation_pages = [
        path.relative_to(paths.projection_wiki).as_posix()
        for path in paths.projection_wiki.rglob("*.md")
        if path.name.casefold() in AUTHORED_ORIENTATION_FILENAMES
    ]
    if projection_orientation_pages:
        raise AgentGuidedLexicalRunnerError(
            "projection corpus must not include hot/index/overview pages: "
            f"{projection_orientation_pages[:5]}"
        )


def validate_runner_report(report: Mapping[str, object]) -> None:
    require_exact_keys(report, "report", REPORT_TOP_LEVEL_FIELDS)
    if report.get("schema_id") != REPORT_SCHEMA_ID:
        raise AgentGuidedLexicalRunnerError("report schema_id is wrong")
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise AgentGuidedLexicalRunnerError("report schema_version is wrong")
    require_exact_keys(
        require_mapping(report.get("runner"), "runner"), "runner", {"name", "version"}
    )
    validate_fixture_report(require_mapping(report.get("fixture"), "fixture"))
    constraints = require_mapping(report.get("constraints"), "constraints")
    require_exact_keys(
        constraints,
        "constraints",
        {
            "runner_calls_llm",
            "llm_call_count",
            "variants_generated_from_qrels",
            "max_query_variants",
            "source_qrels_separated",
            "original_page_citations_only",
            "public_superiority_claim",
            "raw_hybrid_requires_explicit_provider",
            "semantic_leakage_mechanically_proven",
            "release_evidence_requires_independent_source_only_plans_before_qrels",
            "fixture_is_release_evidence",
            "cold_cache_mechanically_verified",
            "service_instance_isolation_verified",
            "service_instance_isolation_method",
        },
    )
    if constraints.get("runner_calls_llm") is not False or constraints.get("llm_call_count") != 0:
        raise AgentGuidedLexicalRunnerError("report must declare zero runner LLM calls")
    if constraints.get("variants_generated_from_qrels") is not False:
        raise AgentGuidedLexicalRunnerError(
            "report must declare variants_generated_from_qrels=false"
        )
    if constraints.get("max_query_variants") != 2:
        raise AgentGuidedLexicalRunnerError("report must declare max_query_variants=2")
    if constraints.get("semantic_leakage_mechanically_proven") is not False:
        raise AgentGuidedLexicalRunnerError(
            "report must not claim semantic leakage was mechanically proven absent"
        )
    if constraints.get("cold_cache_mechanically_verified") is not False:
        raise AgentGuidedLexicalRunnerError("report must not claim cold cache verification")
    if constraints.get("service_instance_isolation_verified") is not True:
        raise AgentGuidedLexicalRunnerError("report must verify service instance isolation")
    if constraints.get("service_instance_isolation_method") != SERVICE_INSTANCE_ISOLATION_METHOD:
        raise AgentGuidedLexicalRunnerError("report service isolation method mismatch")
    arms = require_mapping(report.get("arms"), "arms")
    validate_arms_report(arms)
    validate_per_query_report(require_mapping(report.get("per_query"), "per_query"), arms)
    metric_definitions = require_mapping(report.get("metric_definitions"), "metric_definitions")
    require_exact_keys(metric_definitions, "metric_definitions", METRIC_FIELDS)
    validate_provenance_report(require_mapping(report.get("provenance"), "provenance"))
    limitations = report.get("limitations")
    if not isinstance(limitations, list) or not all(isinstance(item, str) for item in limitations):
        raise AgentGuidedLexicalRunnerError("limitations must be a string array")


def validate_fixture_report(fixture: Mapping[str, object]) -> None:
    require_exact_keys(
        fixture,
        "fixture",
        {
            "path",
            "content_sha256",
            "queries_sha256",
            "qrels_sha256",
            "agent_plan_sha256",
            "corpora",
            "query_counts",
            "qrel_counts",
            "agent_plan_row_count",
            "languages",
            "cases",
        },
    )
    for key in ("content_sha256", "queries_sha256", "qrels_sha256", "agent_plan_sha256"):
        validate_digest_value(fixture.get(key), f"fixture.{key}")
    corpora = require_mapping(fixture.get("corpora"), "fixture.corpora")
    require_exact_keys(corpora, "fixture.corpora", {"authored", "projection"})
    for corpus, expected_source in (
        ("authored", "authored"),
        ("projection", "projection_extractive"),
    ):
        payload = require_mapping(corpora.get(corpus), f"fixture.corpora.{corpus}")
        require_exact_keys(
            payload,
            f"fixture.corpora.{corpus}",
            {"path", "content_sha256", "orientation_source_expected", "page_count"},
        )
        if payload.get("orientation_source_expected") != expected_source:
            raise AgentGuidedLexicalRunnerError("fixture corpus orientation expectation mismatch")
        validate_digest_value(payload.get("content_sha256"), f"fixture.corpora.{corpus}.content")
    query_counts = require_mapping(fixture.get("query_counts"), "fixture.query_counts")
    require_exact_keys(query_counts, "fixture.query_counts", {"total", "evaluated", "negative"})
    qrel_counts = require_mapping(fixture.get("qrel_counts"), "fixture.qrel_counts")
    require_exact_keys(qrel_counts, "fixture.qrel_counts", {"total", "positive"})


def validate_arms_report(arms: Mapping[str, object]) -> None:
    require_exact_keys(arms, "arms", set(ALL_ARMS))
    for arm in ALL_ARMS:
        payload = require_mapping(arms.get(arm), f"arms.{arm}")
        require_exact_keys(payload, f"arms.{arm}", ARM_REPORT_FIELDS)
        if payload.get("corpus") != ARM_CORPUS[arm]:
            raise AgentGuidedLexicalRunnerError(f"arms.{arm}.corpus mismatch")
        status = payload.get("status")
        if status not in {"available", "skipped"}:
            raise AgentGuidedLexicalRunnerError(f"arms.{arm}.status is invalid")
        expected_orientation: OrientationSource = (
            "not_applicable"
            if status == "skipped"
            else EXPECTED_ORIENTATION_SOURCE[ARM_CORPUS[arm]]
        )
        if payload.get("retrieval_guidance_orientation_source") != expected_orientation:
            raise AgentGuidedLexicalRunnerError(f"arms.{arm}.orientation_source mismatch")
        usage = require_mapping(payload.get("usage"), f"arms.{arm}.usage")
        limitations = payload.get("limitations")
        if not isinstance(limitations, list) or not all(
            isinstance(item, str) for item in limitations
        ):
            raise AgentGuidedLexicalRunnerError(f"arms.{arm}.limitations must be strings")
        if status == "skipped":
            if payload.get("metrics") is not None:
                raise AgentGuidedLexicalRunnerError(f"arms.{arm}.metrics must be null when skipped")
            if payload.get("latency_ms") is not None:
                raise AgentGuidedLexicalRunnerError(
                    f"arms.{arm}.latency_ms must be null when skipped"
                )
            if payload.get("payload_bytes") is not None:
                raise AgentGuidedLexicalRunnerError(
                    f"arms.{arm}.payload_bytes must be null when skipped"
                )
            if not limitations:
                raise AgentGuidedLexicalRunnerError(f"arms.{arm}.limitations must explain skip")
            validate_skipped_usage(usage, f"arms.{arm}.usage")
            continue

        metrics = require_mapping(payload.get("metrics"), f"arms.{arm}.metrics")
        require_exact_keys(metrics, f"arms.{arm}.metrics", METRIC_FIELDS)
        validate_available_usage(usage, f"arms.{arm}.usage")
        validate_distribution(payload.get("latency_ms"), f"arms.{arm}.latency_ms")
        validate_distribution(payload.get("payload_bytes"), f"arms.{arm}.payload_bytes")


def validate_available_usage(usage: Mapping[str, object], label: str) -> None:
    require_exact_keys(usage, label, USAGE_FIELDS)
    if usage.get("cold_usage_cache") is not None:
        raise AgentGuidedLexicalRunnerError(f"{label}.cold_usage_cache must be null/unknown")
    if usage.get("cold_usage_cache_evidence") != "unknown":
        raise AgentGuidedLexicalRunnerError(f"{label}.cold_usage_cache_evidence must be unknown")
    if usage.get("cache_isolation") != "unknown":
        raise AgentGuidedLexicalRunnerError(f"{label}.cache_isolation must be unknown")
    if usage.get("service_instance_isolation_verified") is not True:
        raise AgentGuidedLexicalRunnerError(f"{label} must verify service instance isolation")
    if usage.get("service_instance_isolation_method") != SERVICE_INSTANCE_ISOLATION_METHOD:
        raise AgentGuidedLexicalRunnerError(f"{label} service isolation method mismatch")
    if usage.get("character_counting_method") != "unicode code points after JSON decoding":
        raise AgentGuidedLexicalRunnerError(f"{label}.character_counting_method mismatch")
    validate_usage_common_counts(usage, label)


def validate_skipped_usage(usage: Mapping[str, object], label: str) -> None:
    require_exact_keys(usage, label, USAGE_FIELDS)
    if usage.get("cold_usage_cache") is not None:
        raise AgentGuidedLexicalRunnerError(f"{label}.cold_usage_cache must be null")
    expected_not_applicable = {
        "cold_usage_cache_evidence",
        "cache_isolation",
        "service_instance_isolation_method",
        "character_counting_method",
        "token_count_source",
        "accounting_source",
    }
    for field in expected_not_applicable:
        if usage.get(field) != "not-applicable":
            raise AgentGuidedLexicalRunnerError(f"{label}.{field} must be not-applicable")
    if usage.get("service_instance_isolation_verified") is not False:
        raise AgentGuidedLexicalRunnerError(
            f"{label}.service_instance_isolation_verified must be false"
        )
    validate_usage_common_counts(usage, label)
    for field in COUNT_USAGE_FIELDS:
        if usage.get(field) != 0:
            raise AgentGuidedLexicalRunnerError(f"{label}.{field} must be 0 when skipped")


COUNT_USAGE_FIELDS = frozenset(
    {
        "public_context_requests",
        "public_search_requests",
        "internal_lexical_channel_evaluations",
        "adapter_search_calls",
        "read_calls",
        "variant_count",
        "query_character_count",
        "input_tokens",
        "output_tokens",
        "llm_calls",
    }
)


def validate_usage_common_counts(usage: Mapping[str, object], label: str) -> None:
    for field in COUNT_USAGE_FIELDS:
        value = usage.get(field)
        if not isinstance(value, int) or value < 0:
            raise AgentGuidedLexicalRunnerError(f"{label}.{field} must be a non-negative integer")
    if usage.get("llm_calls") != 0:
        raise AgentGuidedLexicalRunnerError(f"{label} must declare llm_calls=0")


def validate_per_query_report(
    per_query: Mapping[str, object],
    arms: Mapping[str, object],
) -> None:
    require_exact_keys(per_query, "per_query", set(ALL_ARMS))
    query_ids_by_arm: list[set[str]] = []
    for arm in ALL_ARMS:
        arm_payload = require_mapping(arms.get(arm), f"arms.{arm}")
        status = arm_payload.get("status")
        rows = per_query.get(arm)
        if not isinstance(rows, list):
            raise AgentGuidedLexicalRunnerError(f"per_query.{arm} must be an array")
        if status == "skipped":
            if rows:
                raise AgentGuidedLexicalRunnerError(f"per_query.{arm} must be empty when skipped")
            continue
        if status != "available":
            raise AgentGuidedLexicalRunnerError(f"arms.{arm}.status is invalid")
        if not rows:
            raise AgentGuidedLexicalRunnerError(f"per_query.{arm} must contain measured rows")
        seen: set[str] = set()
        for index, row_value in enumerate(rows):
            row = require_mapping(row_value, f"per_query.{arm}[{index}]")
            require_exact_keys(row, f"per_query.{arm}[{index}]", PER_QUERY_ROW_FIELDS)
            query_id = row.get("query_id")
            if not isinstance(query_id, str) or not query_id:
                raise AgentGuidedLexicalRunnerError("per_query query_id must be non-empty")
            if query_id in seen:
                raise AgentGuidedLexicalRunnerError(f"per_query.{arm} has duplicate query_id")
            seen.add(query_id)
            if row.get("answerability") not in {"answerable", "unanswerable"}:
                raise AgentGuidedLexicalRunnerError("per_query answerability is invalid")
            page_ids = row.get("ranked_page_ids")
            if not isinstance(page_ids, list) or not all(
                isinstance(item, str) for item in page_ids
            ):
                raise AgentGuidedLexicalRunnerError("ranked_page_ids must be a string array")
        query_ids_by_arm.append(seen)
    if len({frozenset(ids) for ids in query_ids_by_arm}) != 1:
        raise AgentGuidedLexicalRunnerError("per_query arms must contain the same query ids")


def validate_provenance_report(provenance: Mapping[str, object]) -> None:
    require_exact_keys(provenance, "provenance", {"implementation", "inputs", "plan"})
    implementation = require_mapping(provenance.get("implementation"), "provenance.implementation")
    require_exact_keys(
        implementation,
        "provenance.implementation",
        {"revision", "revision_source", "dirty", "dirty_detection"},
    )
    if not isinstance(implementation.get("revision"), str):
        raise AgentGuidedLexicalRunnerError("provenance implementation revision must be a string")
    if implementation.get("dirty") not in {True, False, None}:
        raise AgentGuidedLexicalRunnerError("provenance implementation dirty must be boolean/null")
    inputs = require_mapping(provenance.get("inputs"), "provenance.inputs")
    require_exact_keys(
        inputs,
        "provenance.inputs",
        {
            "fixture_tree_sha256",
            "authored_corpus_sha256",
            "projection_corpus_sha256",
            "queries_sha256",
            "qrels_sha256",
            "agent_plan_sha256",
        },
    )
    for key, value in inputs.items():
        validate_digest_value(value, f"provenance.inputs.{key}")
    plan = require_mapping(provenance.get("plan"), "provenance.plan")
    require_exact_keys(
        plan,
        "provenance.plan",
        {
            "schema_id",
            "provenance_schema_version",
            "row_count",
            "generator_kinds",
            "models",
            "human_fixture_ids",
            "prompt_template_revisions",
            "prompt_template_sha256",
            "source_context_digests",
            "input_digests",
            "timestamp_utc_values",
            "stable_fixture_marker",
            "accounting_sources",
        },
    )
    if plan.get("schema_id") != PLAN_SCHEMA_ID:
        raise AgentGuidedLexicalRunnerError("provenance plan schema_id mismatch")
    if plan.get("provenance_schema_version") != PLAN_PROVENANCE_SCHEMA_VERSION:
        raise AgentGuidedLexicalRunnerError("provenance plan schema version mismatch")
    for field in ("prompt_template_sha256", "source_context_digests", "input_digests"):
        values = plan.get(field)
        if not isinstance(values, list) or not values:
            raise AgentGuidedLexicalRunnerError(f"provenance.plan.{field} must be a non-empty list")
        for item in values:
            validate_digest_value(item, f"provenance.plan.{field}")


def validate_distribution(value: object, label: str) -> None:
    payload = require_mapping(value, label)
    require_exact_keys(payload, label, DISTRIBUTION_FIELDS)
    count = payload.get("count")
    if not isinstance(count, int) or count < 0:
        raise AgentGuidedLexicalRunnerError(f"{label}.count must be a non-negative integer")
    for key in ("p50", "p95"):
        item = payload.get(key)
        if item is not None and not isinstance(item, int | float):
            raise AgentGuidedLexicalRunnerError(f"{label}.{key} must be numeric or null")


def validate_report_public_safety(value: object) -> None:
    for text in iter_strings(value):
        if SUPERIORITY_CLAIM_RE.search(text):
            raise AgentGuidedLexicalRunnerError("public report must not contain superiority claims")
        for pattern in PRIVATE_PATTERNS:
            if pattern.search(text):
                raise AgentGuidedLexicalRunnerError(
                    "public report contains a private path, endpoint, or secret"
                )


def validate_public_safe_text(text: str, *, path: Path, line_number: int, field: str) -> None:
    for pattern in PRIVATE_PATTERNS:
        if pattern.search(text):
            raise AgentGuidedLexicalRunnerError(
                f"{path}:{line_number}: {field} contains a private path, endpoint, or secret"
            )


def validate_original_page_ids(page_ids: Iterable[str], wiki_page_ids: frozenset[str]) -> None:
    unknown = sorted(set(page_ids).difference(wiki_page_ids))
    if unknown:
        raise AgentGuidedLexicalRunnerError(f"search returned non-source page ids: {unknown[:5]}")


def page_identities_from_wiki(wiki_dir: Path) -> dict[str, PageIdentity]:
    identities: dict[str, PageIdentity] = {}
    service = LlmWikiService(
        wiki_dir,
        refresh_interval_seconds=BENCHMARK_FIXED_INDEX_REFRESH_INTERVAL_SECONDS,
    )
    for page in service.index().pages:
        if page.id in identities:
            raise AgentGuidedLexicalRunnerError(
                f"fixture wiki has duplicate canonical service page id: {page.id!r}"
            )
        identities[page.id] = PageIdentity(
            page_id=page.id,
            path=page.path,
            title=page.title,
        )
    if not identities:
        raise AgentGuidedLexicalRunnerError("fixture wiki must contain Markdown pages")
    return identities


def metric_definitions() -> dict[str, dict[str, object]]:
    return {
        "nDCG@5": {
            "owner": "serve",
            "description": "Discounted retrieval relevance over top five.",
        },
        "Recall@5": {
            "owner": "serve",
            "description": "Relevant original pages retrieved in top five.",
        },
        "MAP@5": {"owner": "serve", "description": "Mean average precision over top five."},
        "citation_precision@5": {
            "owner": "serve",
            "description": "Top-five original page ids that match qrels for answerable cases.",
        },
        "negative_false_positive_rate@5": {
            "owner": "serve",
            "description": "Unanswerable cases with any top-five retrieval exposure.",
        },
    }


def result_page_id(result: Mapping[str, Any]) -> str:
    value = result.get("page_id")
    if not isinstance(value, str) or not value:
        raise AgentGuidedLexicalRunnerError("search result missing page_id")
    return value


def serialized_payload_bytes(results: Sequence[Mapping[str, Any]]) -> int:
    return len(json.dumps(list(results), ensure_ascii=False, sort_keys=True).encode("utf-8"))


def percentile_distribution(values: Sequence[int | float]) -> dict[str, object]:
    if not values:
        return {"count": 0, "p50": None, "p95": None}
    ordered = sorted(float(value) for value in values)
    return {
        "count": len(ordered),
        "p50": round_measurement(percentile(ordered, 50)),
        "p95": round_measurement(percentile(ordered, 95)),
    }


def percentile(ordered: Sequence[float], percentile_value: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (percentile_value / 100.0)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def mean(values: Iterable[float]) -> float:
    items = list(values)
    if not items:
        return 0.0
    return sum(items) / len(items)


def has_positive_qrels(qrels: Mapping[str, Mapping[str, float]], query_id: str) -> bool:
    return any(value > 0 for value in qrels.get(query_id, {}).values())


def elapsed_ms(start_ns: int, end_ns: int) -> float:
    return (end_ns - start_ns) / 1_000_000


def round_metric(value: float) -> float:
    return round(value, 4)


def round_measurement(value: float) -> float:
    return round(value, 3)


def runtime_environment_metadata() -> dict[str, object]:
    return {
        "os": platform.system(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "machine": platform.machine(),
    }


def implementation_revision_metadata(
    repo_root: Path,
    *,
    override: str | None = None,
) -> dict[str, object]:
    detected_revision = "git:unknown"
    dirty: bool | None = None
    dirty_detection = "unknown"
    try:
        detected_revision = "git:" + git_output(repo_root, "rev-parse", "HEAD").strip()
        status = git_output(repo_root, "status", "--porcelain", "--untracked-files=all")
        dirty = bool(status.strip())
        dirty_detection = "git-status"
    except AgentGuidedLexicalRunnerError:
        detected_revision = "git:unknown"
    if override is not None:
        revision = override
        revision_source = "argument"
    else:
        revision = detected_revision
        revision_source = "git" if detected_revision != "git:unknown" else "unknown"
        if dirty is True:
            revision = revision + "+dirty"
    return {
        "revision": revision,
        "revision_source": revision_source,
        "dirty": dirty,
        "dirty_detection": dirty_detection,
    }


def default_implementation_revision(repo_root: Path) -> str:
    return require_report_string(
        implementation_revision_metadata(repo_root).get("revision"),
        "implementation.revision",
    )


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
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AgentGuidedLexicalRunnerError(f"git command failed: {' '.join(args)}") from exc
    return completed.stdout


def compute_tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def compute_file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def require_dir(path: Path, label: str) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise AgentGuidedLexicalRunnerError(f"{label} is not a directory: {path}")
    return resolved


def require_string(record: Mapping[str, Any], key: str, path: Path, line_number: int) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AgentGuidedLexicalRunnerError(
            f"{path}:{line_number}: {key} must be a non-empty string"
        )
    return value.strip()


def require_literal_string(
    record: Mapping[str, Any],
    key: str,
    expected: str,
    path: Path,
    line_number: int,
) -> str:
    value = require_string(record, key, path, line_number)
    if value != expected:
        raise AgentGuidedLexicalRunnerError(f"{path}:{line_number}: {key} must be {expected!r}")
    return value


def require_digest_string(
    record: Mapping[str, Any],
    key: str,
    path: Path,
    line_number: int,
) -> str:
    value = require_string(record, key, path, line_number)
    if HASH_RE.match(value) is None:
        raise AgentGuidedLexicalRunnerError(f"{path}:{line_number}: {key} must be a sha256 digest")
    return value


def require_number(record: Mapping[str, Any], key: str, path: Path, line_number: int) -> float:
    value = record.get(key)
    if not isinstance(value, int | float):
        raise AgentGuidedLexicalRunnerError(f"{path}:{line_number}: {key} must be numeric")
    return float(value)


def require_nonnegative_int(value: object, key: str, path: Path, line_number: int) -> int:
    if not isinstance(value, int) or value < 0:
        raise AgentGuidedLexicalRunnerError(
            f"{path}:{line_number}: {key} must be a non-negative integer"
        )
    return value


def require_bool(
    record: Mapping[str, Any],
    key: str,
    path: Path,
    line_number: int,
) -> bool:
    value = record.get(key)
    if not isinstance(value, bool):
        raise AgentGuidedLexicalRunnerError(f"{path}:{line_number}: {key} must be boolean")
    return value


def require_enum(
    record: Mapping[str, Any],
    key: str,
    allowed: set[str] | frozenset[str],
    path: Path,
    line_number: int,
) -> str:
    value = require_string(record, key, path, line_number)
    if value not in allowed:
        raise AgentGuidedLexicalRunnerError(f"{path}:{line_number}: unsupported {key}: {value}")
    return value


def require_object(
    value: object,
    key: str,
    path: Path,
    line_number: int,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AgentGuidedLexicalRunnerError(f"{path}:{line_number}: {key} must be an object")
    return cast(Mapping[str, Any], value)


def require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise AgentGuidedLexicalRunnerError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def require_exact_keys(
    value: Mapping[str, object],
    label: str,
    expected: set[str] | frozenset[str],
) -> None:
    actual = set(value)
    missing = sorted(expected.difference(actual))
    extra = sorted(actual.difference(expected))
    if missing or extra:
        raise AgentGuidedLexicalRunnerError(
            f"{label} fields mismatch; missing={missing}, extra={extra}"
        )


def require_report_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AgentGuidedLexicalRunnerError(f"{label} must be a non-empty string")
    return value


def validate_digest_value(value: object, label: str) -> None:
    if not isinstance(value, str) or HASH_RE.match(value) is None:
        raise AgentGuidedLexicalRunnerError(f"{label} must be a sha256 digest")


def iter_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from iter_strings(key)
            yield from iter_strings(item)
    elif isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        for item in value:
            yield from iter_strings(item)


def is_same_or_nested(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--output-report", type=Path)
    parser.add_argument("--implementation-revision", default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    run_agent_guided_lexical_benchmark(
        fixture_dir=args.fixture_dir,
        output_report=args.output_report,
        implementation_revision=args.implementation_revision,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
