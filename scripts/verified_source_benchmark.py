"""Deterministic verified-source benchmark validation and reporting.

Run rows store `context_tokens` as the per-result token contribution and
`payload_tokens` as the full serialized query-payload contribution measured
with the recorded Qwen tokenizer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import random
import re
import statistics
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

try:
    from llmwiki_serve import __version__
except ImportError:  # pragma: no cover - supports direct script execution.
    __version__ = "unknown"


SCHEMA = "llmwiki-serve-verified-source-benchmark-v1"
ALLOWED_HARDWARE_BUCKETS = {"windows-local", "dgx-spark-ubuntu", "macos-planned"}
ALLOWED_QUERY_CLASSES = {
    "global-map",
    "known-item",
    "topical",
    "multi-hop",
    "negative",
    "korean-numeric",
    "citation",
    "plain-markdown",
    "native-llmwiki",
}
ALLOWED_EXPECTED_BEHAVIORS = {"answerable", "unanswerable"}
ALLOWED_SURFACES = {
    "raw-full",
    "raw-hot-index-search-read",
    "http-query",
    "http-search-read",
    "service-context",
    "service-context-orientation",
    "service-context-bundle",
    "service-search-read",
    "mcp-context",
    "mcp-search-read",
    "agent-bridge",
}
TELEMETRY_ONLY_SURFACES = {"service-context-bundle"}
RETRIEVAL_EVALUATION_MODE = "retrieval"
TELEMETRY_ONLY_EVALUATION_MODE = "telemetry-only"
RETRIEVAL_DISTRIBUTION_FIELDS = (
    "context_tokens",
    "payload_tokens",
    "payload_bytes",
    "latency_ms",
)
TELEMETRY_DISTRIBUTION_FIELDS = ("payload_tokens", "payload_bytes", "latency_ms")
QUALITY_METRIC_FIELDS = (
    "recall_at_5",
    "hit_at_5",
    "mrr",
    "ndcg_at_10",
    "citation_precision",
    "citation_recall",
    "negative_false_positive_rate",
)
RELEVANT_THRESHOLD = 2
UNKNOWN_VALUE = "unknown"
BENCHMARK_INPUT_FILES = ("corpus.jsonl", "queries.jsonl", "qrels.jsonl", "runs.jsonl")
TOKENIZER_EVIDENCE_DECLARED = "declared-qwen-provenance"
TOKENIZER_EVIDENCE_LOAD_VERIFIED = "local-qwen-tokenizer-load-verified"
ALLOWED_TOKENIZER_EVIDENCE_LEVELS = {
    TOKENIZER_EVIDENCE_DECLARED,
    TOKENIZER_EVIDENCE_LOAD_VERIFIED,
}
PUBLIC_QUALITY_TOKENIZER_EVIDENCE_LEVELS = {TOKENIZER_EVIDENCE_LOAD_VERIFIED}
PUBLIC_MIN_QUERY_COUNT = 50
PUBLIC_MIN_JUDGED_QUERY_COUNT = 50
QUALITY_THRESHOLDS = {
    "recall_at_5": (">=", 0.90),
    "mrr": (">=", 0.75),
    "ndcg_at_10": (">=", 0.85),
    "citation_precision": (">=", 0.95),
    "citation_recall": (">=", 0.85),
    "negative_false_positive_rate": ("<=", 0.05),
}
CONTEXT_TOKEN_P95_BASELINE_RATIO = 1.20

PRIVATE_PATTERNS = (
    ("windows-path", re.compile(r"\b[A-Za-z]:[\\/][^\s\"']+")),
    ("windows-unc-path", re.compile(r"\\\\[A-Za-z0-9._$-]+\\[^\s\"']+")),
    (
        "windows-home-env-path",
        re.compile(
            r"%(?:USERPROFILE|HOMEPATH|APPDATA|LOCALAPPDATA|TEMP|TMP)%[\\/][^\s\"']*",
            re.IGNORECASE,
        ),
    ),
    ("posix-home-path", re.compile(r"(?<![A-Za-z0-9])/(?:Users|home)/[A-Za-z0-9._-]+(?:/|\\)")),
    ("posix-home-env-path", re.compile(r"(?:\$HOME|\$TMPDIR|~)(?:/|\\)[^\s\"']*")),
    (
        "private-scratch-dir",
        re.compile(
            r"(?<![A-Za-z0-9._-])(?:\.llmwiki-work|\.runtime-logs|\.codex-subagents|"
            r"\.codex)(?:[\\/]|[A-Za-z0-9._-])"
        ),
    ),
    (
        "private-loopback-or-rfc1918-url",
        re.compile(
            r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|10\.\d+\.\d+\.\d+|"
            r"192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)[^\s\"']*",
            re.IGNORECASE,
        ),
    ),
    (
        "private-loopback-or-rfc1918-host",
        re.compile(
            r"(?<![A-Za-z0-9.-])(?:localhost|127\.0\.0\.1|0\.0\.0\.0|10\.\d+\.\d+\.\d+|"
            r"192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+):\d{2,5}"
            r"(?:/[^\s\"']*)?",
            re.IGNORECASE,
        ),
    ),
    ("tailscale-url", re.compile(r"https?://[A-Za-z0-9.-]+\.ts\.net[^\s\"']*", re.IGNORECASE)),
    (
        "tailscale-host",
        re.compile(r"(?<![A-Za-z0-9.-])[A-Za-z0-9.-]+\.ts\.net(?::\d{2,5})?", re.IGNORECASE),
    ),
    ("bearer-token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")),
    (
        "secret-env-assignment",
        re.compile(
            r"\b[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)\s*[:=]\s*"
            r"['\"]?[A-Za-z0-9._~+/=-]{8,}",
            re.IGNORECASE,
        ),
    ),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{16,}")),
    ("npm-token", re.compile(r"\bnpm_[A-Za-z0-9]{16,}")),
    ("pypi-token", re.compile(r"\bpypi-[A-Za-z0-9_-]{16,}")),
    ("redis-url", re.compile(r"\bredis(?:s)?://[^\s\"']+", re.IGNORECASE)),
    ("raw-redis-key", re.compile(r"\bllmwiki:[A-Za-z0-9:_-]{8,}")),
)
TOKEN_PROXY_MARKERS = (
    "byte/4",
    "bytes/4",
    "byte_div_4",
    "bytes_div_4",
    "byte-4",
    "bytes-4",
    "heuristic",
    "approx",
)
PUBLIC_MATERIAL_QUALITY_IMPROVEMENT = 0.05
DIGEST_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
}
UTF8_BOM = b"\xef\xbb\xbf"


class BenchmarkValidationError(ValueError):
    """Raised when public benchmark inputs or reports violate the schema."""


class TokenizerAdapter(Protocol):
    tokenizer_id: str
    tokenizer_revision: str

    def count_tokens(self, text: str) -> int:
        """Count tokens without using byte-length proxy fallbacks."""


@dataclass(frozen=True)
class TokenizerProvenance:
    tokenizer_id: str
    tokenizer_revision: str
    evidence_level: str = TOKENIZER_EVIDENCE_DECLARED
    verified_by_harness: bool = False

    def __post_init__(self) -> None:
        validate_tokenizer_provenance(self.tokenizer_id, self.tokenizer_revision)
        if self.evidence_level not in ALLOWED_TOKENIZER_EVIDENCE_LEVELS:
            raise BenchmarkValidationError(
                f"tokenizer evidence_level must be one of "
                f"{sorted(ALLOWED_TOKENIZER_EVIDENCE_LEVELS)}"
            )
        if self.verified_by_harness != (self.evidence_level == TOKENIZER_EVIDENCE_LOAD_VERIFIED):
            raise BenchmarkValidationError(
                "verified tokenizer evidence must be produced by the harness"
            )

    def as_report(self) -> dict[str, object]:
        return {
            "id": self.tokenizer_id,
            "revision": self.tokenizer_revision,
            "policy": "qwen-tokenizer-required-no-byte-proxy",
            "evidence_level": self.evidence_level,
            "verified_by_harness": self.verified_by_harness,
            "input_accounting": (
                "runs.jsonl context_tokens are precomputed per result; "
                "payload_tokens are precomputed per full serialized query payload"
            ),
        }


class HuggingFaceQwenTokenizerAdapter:
    def __init__(self, tokenizer_id: str, tokenizer_revision: str, tokenizer: Any) -> None:
        validate_tokenizer_provenance(tokenizer_id, tokenizer_revision)
        self.tokenizer_id = tokenizer_id
        self.tokenizer_revision = tokenizer_revision
        self._tokenizer = tokenizer

    @classmethod
    def load(
        cls, tokenizer_id: str, tokenizer_revision: str = UNKNOWN_VALUE
    ) -> HuggingFaceQwenTokenizerAdapter:
        validate_tokenizer_provenance(tokenizer_id, tokenizer_revision)
        try:
            from transformers import AutoTokenizer
        except ImportError as error:
            raise BenchmarkValidationError(
                "Qwen token counting requires the optional transformers package. "
                "Install it explicitly for collection; no byte/4 fallback is allowed."
            ) from error
        revision = None if tokenizer_revision == UNKNOWN_VALUE else tokenizer_revision
        tokenizer = AutoTokenizer.from_pretrained(tokenizer_id, revision=revision)
        return cls(tokenizer_id, tokenizer_revision, tokenizer)

    def count_tokens(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=False))


@dataclass(frozen=True)
class CorpusRow:
    doc_id: str
    path: str
    title: str
    adapter: str
    role: str
    approved: bool
    sha256: str
    source_ref_ids: tuple[str, ...]
    license: str
    public_source: str


@dataclass(frozen=True)
class QueryRow:
    query_id: str
    text: str
    query_class: str
    locale: str
    expected_behavior: str


@dataclass(frozen=True)
class SupportSpan:
    start: int
    end: int


@dataclass(frozen=True)
class QrelRow:
    query_id: str
    doc_id: str
    relevance: int
    support_spans: tuple[SupportSpan, ...]
    citation_required: bool


@dataclass(frozen=True)
class RunRow:
    run_id: str
    query_id: str
    rank: int
    doc_id: str
    score: float
    citation_ids: tuple[str, ...]
    context_tokens: int
    payload_tokens: int
    payload_bytes: int
    latency_ms: float
    surface: str
    tokenizer_id: str
    tokenizer_revision: str
    source_bytes_scanned: int | None


@dataclass(frozen=True)
class BenchmarkData:
    corpus: dict[str, CorpusRow]
    queries: dict[str, QueryRow]
    qrels_by_query: dict[str, list[QrelRow]]
    runs_by_id: dict[str, list[RunRow]]


@dataclass(frozen=True)
class SourceMutationDigest:
    before_sha256: str
    after_sha256: str
    mutated: bool

    def as_report(self) -> dict[str, object]:
        return {
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "mutated": self.mutated,
        }


def validate_tokenizer_provenance(tokenizer_id: str, tokenizer_revision: str) -> None:
    if not tokenizer_id or not tokenizer_revision:
        raise BenchmarkValidationError("tokenizer_id and tokenizer_revision are required")
    normalized = f"{tokenizer_id} {tokenizer_revision}".lower().replace(" ", "_")
    if "qwen" not in normalized:
        raise BenchmarkValidationError(
            f"tokenizer provenance must identify a Qwen tokenizer, got {tokenizer_id!r}"
        )
    if any(marker in normalized for marker in TOKEN_PROXY_MARKERS):
        raise BenchmarkValidationError("byte/4 or heuristic token-count provenance is forbidden")


def load_benchmark_data(input_dir: Path, tokenizer: TokenizerProvenance) -> BenchmarkData:
    corpus = load_corpus(input_dir / "corpus.jsonl")
    queries = load_queries(input_dir / "queries.jsonl")
    qrels = load_qrels(input_dir / "qrels.jsonl", corpus, queries)
    runs = load_runs(input_dir / "runs.jsonl", corpus, queries, tokenizer)
    validate_citation_ids(runs, corpus)
    validate_every_query_has_qrel(queries, qrels)
    return BenchmarkData(
        corpus=corpus,
        queries=queries,
        qrels_by_query=qrels,
        runs_by_id=group_runs_by_id(runs),
    )


def load_corpus(path: Path) -> dict[str, CorpusRow]:
    rows: dict[str, CorpusRow] = {}
    for record in load_jsonl(path):
        ensure_fields(
            record,
            path,
            required={
                "doc_id",
                "path",
                "title",
                "adapter",
                "role",
                "approved",
                "sha256",
                "source_ref_ids",
                "license",
                "public_source",
            },
        )
        doc_id = require_string(record, "doc_id", path)
        source_path = require_string(record, "path", path)
        validate_public_relative_path(source_path, f"{path.name}:{doc_id}:path")
        sha256 = require_string(record, "sha256", path)
        if not re.fullmatch(r"[a-fA-F0-9]{64}", sha256):
            raise BenchmarkValidationError(f"{path.name}:{doc_id}: sha256 must be 64 hex chars")
        if doc_id in rows:
            raise BenchmarkValidationError(f"{path.name}: duplicate doc_id {doc_id!r}")
        approved = require_bool(record, "approved", path)
        if not approved:
            raise BenchmarkValidationError(
                f"{path.name}:{doc_id}: public benchmark corpus rows must be approved"
            )
        rows[doc_id] = CorpusRow(
            doc_id=doc_id,
            path=source_path,
            title=require_string(record, "title", path),
            adapter=require_string(record, "adapter", path),
            role=require_string(record, "role", path),
            approved=approved,
            sha256=sha256.lower(),
            source_ref_ids=require_string_tuple(record, "source_ref_ids", path),
            license=require_string(record, "license", path),
            public_source=require_string(record, "public_source", path),
        )
    if not rows:
        raise BenchmarkValidationError(f"{path.name}: expected at least one corpus row")
    return rows


def load_queries(path: Path) -> dict[str, QueryRow]:
    rows: dict[str, QueryRow] = {}
    for record in load_jsonl(path):
        ensure_fields(
            record,
            path,
            required={"query_id", "text", "class", "locale", "expected_behavior"},
        )
        query_id = require_string(record, "query_id", path)
        query_class = require_string(record, "class", path)
        expected_behavior = require_string(record, "expected_behavior", path)
        if query_class not in ALLOWED_QUERY_CLASSES:
            raise BenchmarkValidationError(f"{path.name}:{query_id}: unknown query class")
        if expected_behavior not in ALLOWED_EXPECTED_BEHAVIORS:
            raise BenchmarkValidationError(f"{path.name}:{query_id}: unknown expected behavior")
        if query_id in rows:
            raise BenchmarkValidationError(f"{path.name}: duplicate query_id {query_id!r}")
        rows[query_id] = QueryRow(
            query_id=query_id,
            text=require_string(record, "text", path),
            query_class=query_class,
            locale=require_string(record, "locale", path),
            expected_behavior=expected_behavior,
        )
    if not rows:
        raise BenchmarkValidationError(f"{path.name}: expected at least one query")
    return rows


def load_qrels(
    path: Path, corpus: Mapping[str, CorpusRow], queries: Mapping[str, QueryRow]
) -> dict[str, list[QrelRow]]:
    rows: dict[str, list[QrelRow]] = defaultdict(list)
    seen_qrels: set[tuple[str, str]] = set()
    for record in load_jsonl(path):
        ensure_fields(
            record,
            path,
            required={"query_id", "doc_id", "relevance", "support_spans", "citation_required"},
        )
        query_id = require_string(record, "query_id", path)
        doc_id = require_string(record, "doc_id", path)
        relevance = require_int(record, "relevance", path)
        if query_id not in queries:
            raise BenchmarkValidationError(f"{path.name}: unknown qrel query_id {query_id!r}")
        if doc_id not in corpus:
            raise BenchmarkValidationError(f"{path.name}: unknown qrel doc_id {doc_id!r}")
        if (query_id, doc_id) in seen_qrels:
            raise BenchmarkValidationError(
                f"{path.name}:{query_id}: duplicate qrel for doc_id {doc_id!r}"
            )
        seen_qrels.add((query_id, doc_id))
        if relevance not in {0, 1, 2, 3}:
            raise BenchmarkValidationError(f"{path.name}:{query_id}: relevance must be 0..3")
        rows[query_id].append(
            QrelRow(
                query_id=query_id,
                doc_id=doc_id,
                relevance=relevance,
                support_spans=parse_support_spans(record["support_spans"], path, query_id),
                citation_required=require_bool(record, "citation_required", path),
            )
        )
    return dict(rows)


def load_runs(
    path: Path,
    corpus: Mapping[str, CorpusRow],
    queries: Mapping[str, QueryRow],
    tokenizer: TokenizerProvenance,
) -> list[RunRow]:
    rows: list[RunRow] = []
    seen_ranks: set[tuple[str, str, int]] = set()
    seen_docs: set[tuple[str, str, str]] = set()
    for record in load_jsonl(path):
        ensure_fields(
            record,
            path,
            required={
                "run_id",
                "query_id",
                "rank",
                "doc_id",
                "score",
                "citation_ids",
                "context_tokens",
                "payload_tokens",
                "payload_bytes",
                "latency_ms",
                "surface",
                "tokenizer_id",
                "tokenizer_revision",
            },
            optional={"source_bytes_scanned"},
        )
        run_id = require_string(record, "run_id", path)
        query_id = require_string(record, "query_id", path)
        doc_id = require_string(record, "doc_id", path)
        rank = require_int(record, "rank", path)
        surface = require_string(record, "surface", path)
        tokenizer_id = require_string(record, "tokenizer_id", path)
        tokenizer_revision = require_string(record, "tokenizer_revision", path)
        validate_tokenizer_provenance(tokenizer_id, tokenizer_revision)
        if (
            tokenizer_id != tokenizer.tokenizer_id
            or tokenizer_revision != tokenizer.tokenizer_revision
        ):
            raise BenchmarkValidationError(
                f"{path.name}:{run_id}/{query_id}: tokenizer provenance does not match report"
            )
        if query_id not in queries:
            raise BenchmarkValidationError(f"{path.name}: unknown run query_id {query_id!r}")
        if doc_id not in corpus:
            raise BenchmarkValidationError(f"{path.name}: unknown run doc_id {doc_id!r}")
        if rank < 1:
            raise BenchmarkValidationError(f"{path.name}:{run_id}/{query_id}: rank must be >= 1")
        if surface not in ALLOWED_SURFACES:
            raise BenchmarkValidationError(f"{path.name}:{run_id}/{query_id}: unknown surface")
        if (run_id, query_id, rank) in seen_ranks:
            raise BenchmarkValidationError(
                f"{path.name}:{run_id}/{query_id}: duplicate rank {rank}"
            )
        seen_ranks.add((run_id, query_id, rank))
        if (run_id, query_id, doc_id) in seen_docs:
            raise BenchmarkValidationError(
                f"{path.name}:{run_id}/{query_id}: duplicate doc_id {doc_id!r}"
            )
        seen_docs.add((run_id, query_id, doc_id))
        latency_ms = require_float(record, "latency_ms", path)
        if latency_ms < 0:
            raise BenchmarkValidationError(
                f"{path.name}:{run_id}/{query_id}: latency_ms must be non-negative"
            )
        context_tokens = require_int(record, "context_tokens", path)
        payload_tokens = require_int(record, "payload_tokens", path)
        payload_bytes = require_int(record, "payload_bytes", path)
        if context_tokens < 0 or payload_tokens < 0 or payload_bytes < 0:
            raise BenchmarkValidationError(
                f"{path.name}:{run_id}/{query_id}: tokens and payload bytes must be non-negative"
            )
        source_bytes_scanned = parse_optional_nonnegative_int(
            record.get("source_bytes_scanned"), path, "source_bytes_scanned"
        )
        if surface.startswith("raw-") and source_bytes_scanned is None:
            raise BenchmarkValidationError(
                f"{path.name}:{run_id}/{query_id}: raw surfaces require source_bytes_scanned"
            )
        if not surface.startswith("raw-") and source_bytes_scanned is not None:
            raise BenchmarkValidationError(
                f"{path.name}:{run_id}/{query_id}: served surfaces must use null "
                "source_bytes_scanned"
            )
        rows.append(
            RunRow(
                run_id=run_id,
                query_id=query_id,
                rank=rank,
                doc_id=doc_id,
                score=require_float(record, "score", path),
                citation_ids=require_string_tuple(record, "citation_ids", path),
                context_tokens=context_tokens,
                payload_tokens=payload_tokens,
                payload_bytes=payload_bytes,
                latency_ms=latency_ms,
                surface=surface,
                tokenizer_id=tokenizer_id,
                tokenizer_revision=tokenizer_revision,
                source_bytes_scanned=source_bytes_scanned,
            )
        )
    if not rows:
        raise BenchmarkValidationError(f"{path.name}: expected at least one run row")
    validate_run_query_groups(rows, path)
    return rows


def validate_run_query_groups(rows: Sequence[RunRow], path: Path) -> None:
    grouped: dict[tuple[str, str], list[RunRow]] = defaultdict(list)
    surfaces_by_run: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        grouped[(row.run_id, row.query_id)].append(row)
        surfaces_by_run[row.run_id].add(row.surface)

    for run_id, surfaces in surfaces_by_run.items():
        if len(surfaces) != 1:
            raise BenchmarkValidationError(
                f"{path.name}:{run_id}: surface must be run-level and identical "
                f"across queries; got {sorted(surfaces)}"
            )

    for (run_id, query_id), query_rows in grouped.items():
        ranks = sorted(row.rank for row in query_rows)
        expected_ranks = list(range(1, len(query_rows) + 1))
        if ranks != expected_ranks:
            raise BenchmarkValidationError(
                f"{path.name}:{run_id}/{query_id}: ranks must be contiguous from 1; got {ranks}"
            )
        for field in (
            "surface",
            "payload_tokens",
            "payload_bytes",
            "latency_ms",
            "source_bytes_scanned",
        ):
            values = {getattr(row, field) for row in query_rows}
            if len(values) > 1:
                raise BenchmarkValidationError(
                    f"{path.name}:{run_id}/{query_id}: {field} must be query-level "
                    "and identical across ranked rows"
                )


def run_benchmark(
    input_dir: Path,
    *,
    hardware_bucket: str,
    tokenizer: TokenizerProvenance,
    seed: int,
    bootstrap_samples: int,
    baseline_run_id: str | None = None,
    source_root: Path | None = None,
) -> dict[str, object]:
    validate_hardware_bucket(hardware_bucket)
    input_artifacts = compute_input_artifact_digests(input_dir)
    source_before = compute_tree_digest(source_root) if source_root else None
    data = load_benchmark_data(input_dir, tokenizer)
    if source_root is not None:
        validate_corpus_source_hashes(data.corpus, source_root)
    source_after = compute_tree_digest(source_root) if source_root else None
    source_digest = (
        SourceMutationDigest(
            before_sha256=source_before or "",
            after_sha256=source_after or "",
            mutated=source_before != source_after,
        )
        if source_before is not None and source_after is not None
        else None
    )
    report = build_report(
        data,
        hardware_bucket=hardware_bucket,
        tokenizer=tokenizer,
        seed=seed,
        bootstrap_samples=bootstrap_samples,
        baseline_run_id=baseline_run_id,
        source_digest=source_digest,
        input_artifacts=input_artifacts,
    )
    validate_report(report)
    return report


def build_report(
    data: BenchmarkData,
    *,
    hardware_bucket: str,
    tokenizer: TokenizerProvenance,
    seed: int,
    bootstrap_samples: int,
    baseline_run_id: str | None,
    source_digest: SourceMutationDigest | None = None,
    input_artifacts: Mapping[str, str] | None = None,
) -> dict[str, object]:
    metrics: dict[str, dict[str, object]] = {}
    per_query_maps: dict[str, dict[str, dict[str, float]]] = {}
    for run_id, rows in sorted(data.runs_by_id.items()):
        run_metrics, query_maps = compute_run_metrics(
            run_id,
            rows,
            corpus=data.corpus,
            queries=data.queries,
            qrels_by_query=data.qrels_by_query,
        )
        metrics[run_id] = run_metrics
        per_query_maps[run_id] = query_maps

    hard_failures: list[str] = []
    source_report: dict[str, object] | None = None
    if source_digest is not None:
        source_report = source_digest.as_report()
        if source_digest.mutated:
            hard_failures.append("source tree mutated during benchmark evaluation")

    report: dict[str, object] = {
        "schema": SCHEMA,
        "evidence_track": "quality-benchmark",
        "hardware_bucket": hardware_bucket,
        "environment": public_environment(hardware_bucket),
        "tokenizer": tokenizer.as_report(),
        "seed": seed,
        "bootstrap_samples": bootstrap_samples,
        "inputs": {
            "corpus_records": len(data.corpus),
            "query_records": len(data.queries),
            "qrel_records": sum(len(rows) for rows in data.qrels_by_query.values()),
            "run_ids": sorted(data.runs_by_id),
        },
        "input_artifacts": dict(input_artifacts or {}),
        "metrics": metrics,
        "deltas": compute_deltas(
            per_query_maps,
            baseline_run_id=baseline_run_id,
            seed=seed,
            bootstrap_samples=bootstrap_samples,
        ),
        "quality_gates": evaluate_quality_gates(
            metrics,
            baseline_run_id=baseline_run_id,
            tokenizer=tokenizer,
            hard_failures=hard_failures,
        ),
        "source_mutation": source_report,
        "hard_failures": hard_failures,
    }
    assert_public_safe_value(report, "report")
    return report


def compute_run_metrics(
    run_id: str,
    rows: Sequence[RunRow],
    *,
    corpus: Mapping[str, CorpusRow],
    queries: Mapping[str, QueryRow],
    qrels_by_query: Mapping[str, Sequence[QrelRow]],
) -> tuple[dict[str, object], dict[str, dict[str, float]]]:
    rows_by_query: dict[str, list[RunRow]] = defaultdict(list)
    for row in rows:
        rows_by_query[row.query_id].append(row)
    for query_rows in rows_by_query.values():
        query_rows.sort(key=lambda item: item.rank)

    surface = rows[0].surface
    telemetry_only = surface in TELEMETRY_ONLY_SURFACES
    query_ids = list(queries)
    metrics, per_query_maps = compute_metric_subset(
        query_ids,
        rows_by_query=rows_by_query,
        queries=queries,
        qrels_by_query=qrels_by_query,
        corpus=corpus,
        telemetry_only=telemetry_only,
    )
    metrics = {
        "run_id": run_id,
        "surface": surface,
        "evaluation_mode": (
            TELEMETRY_ONLY_EVALUATION_MODE if telemetry_only else RETRIEVAL_EVALUATION_MODE
        ),
        **metrics,
    }
    query_classes: dict[str, object] = {}
    for query_class in sorted({query.query_class for query in queries.values()}):
        class_query_ids = [
            query_id for query_id, query in queries.items() if query.query_class == query_class
        ]
        class_metrics, _class_maps = compute_metric_subset(
            class_query_ids,
            rows_by_query=rows_by_query,
            queries=queries,
            qrels_by_query=qrels_by_query,
            corpus=corpus,
            telemetry_only=telemetry_only,
        )
        query_classes[query_class] = {"query_class": query_class, **class_metrics}
    metrics["query_classes"] = query_classes
    return metrics, per_query_maps


def compute_metric_subset(
    query_ids: Sequence[str],
    *,
    rows_by_query: Mapping[str, Sequence[RunRow]],
    queries: Mapping[str, QueryRow],
    qrels_by_query: Mapping[str, Sequence[QrelRow]],
    corpus: Mapping[str, CorpusRow],
    telemetry_only: bool,
) -> tuple[dict[str, object], dict[str, dict[str, float]]]:
    recall_values: dict[str, float] = {}
    hit_values: dict[str, float] = {}
    mrr_values: dict[str, float] = {}
    ndcg_values: dict[str, float] = {}
    token_values: dict[str, float] = {}
    payload_token_values: dict[str, float] = {}
    payload_values: dict[str, float] = {}
    latency_values: dict[str, float] = {}
    negative_false_positives: list[float] = []

    citation_supported = 0
    citation_total = 0
    citation_required_seen = 0
    citation_required_total = 0
    judged_query_count = 0
    run_row_count = 0

    for query_id in query_ids:
        query = queries[query_id]
        query_rows = list(rows_by_query.get(query_id, []))
        run_row_count += len(query_rows)
        qrels = list(qrels_by_query.get(query_id, ()))
        relevant = {qrel.doc_id: qrel.relevance for qrel in qrels if qrel.relevance >= 2}
        if not telemetry_only:
            if relevant:
                judged_query_count += 1
                recall_values[query_id] = recall_at_k(query_rows, relevant, k=5)
                hit_values[query_id] = hit_at_k(query_rows, relevant, k=5)
                mrr_values[query_id] = reciprocal_rank(query_rows, relevant)
                ndcg_values[query_id] = ndcg_at_k(query_rows, qrels, k=10)
            if query.expected_behavior == "unanswerable":
                negative_false_positives.append(1.0 if query_rows else 0.0)
            token_values[query_id] = float(sum(row.context_tokens for row in query_rows))
        payload_token_values[query_id] = float(
            query_level_value(query_rows, "payload_tokens", default=0, query_id=query_id)
        )
        payload_values[query_id] = float(
            query_level_value(query_rows, "payload_bytes", default=0, query_id=query_id)
        )
        latency_values[query_id] = float(
            query_level_value(query_rows, "latency_ms", default=0.0, query_id=query_id)
        )

        if not telemetry_only:
            supported_refs = supported_citation_refs(qrels, corpus)
            required_refs = required_citation_refs(qrels, corpus)
            surfaced_required_refs: set[str] = set()
            for row in query_rows:
                for citation_id in row.citation_ids:
                    citation_total += 1
                    if citation_id in supported_refs:
                        citation_supported += 1
                    if citation_id in required_refs:
                        surfaced_required_refs.add(citation_id)
            if required_refs:
                citation_required_total += len(required_refs)
                citation_required_seen += len(surfaced_required_refs)

    metrics: dict[str, object] = {
        "query_count": len(query_ids),
        "run_row_count": run_row_count,
        "payload_tokens": distribution(payload_token_values.values()),
        "payload_bytes": distribution(payload_values.values()),
        "latency_ms": distribution(latency_values.values()),
    }
    if not telemetry_only:
        metrics.update(
            {
                "judged_query_count": judged_query_count,
                "recall_at_5": mean_or_none(recall_values.values()),
                "hit_at_5": mean_or_none(hit_values.values()),
                "mrr": mean_or_none(mrr_values.values()),
                "ndcg_at_10": mean_or_none(ndcg_values.values()),
                "citation_precision": (
                    citation_supported / citation_total if citation_total else None
                ),
                "citation_recall": (
                    citation_required_seen / citation_required_total
                    if citation_required_total
                    else None
                ),
                "negative_false_positive_rate": mean_or_none(negative_false_positives),
                "context_tokens": distribution(token_values.values()),
            }
        )
    per_query_maps = {
        "payload_tokens": payload_token_values,
        "payload_bytes": payload_values,
        "latency_ms": latency_values,
    }
    if not telemetry_only:
        per_query_maps.update(
            {
                "recall_at_5": recall_values,
                "hit_at_5": hit_values,
                "mrr": mrr_values,
                "ndcg_at_10": ndcg_values,
                "context_tokens": token_values,
            }
        )
    return metrics, per_query_maps


def query_level_value(
    rows: Sequence[RunRow], field: str, *, default: int | float, query_id: str
) -> int | float:
    if not rows:
        return default
    values = {getattr(row, field) for row in rows}
    if len(values) != 1:
        raise BenchmarkValidationError(
            f"{field} must be recorded once per query; inconsistent values for {query_id}"
        )
    return values.pop()


def compute_deltas(
    per_query_maps: Mapping[str, Mapping[str, Mapping[str, float]]],
    *,
    baseline_run_id: str | None,
    seed: int,
    bootstrap_samples: int,
) -> dict[str, object]:
    if baseline_run_id is None:
        return {}
    if baseline_run_id not in per_query_maps:
        raise BenchmarkValidationError(f"baseline_run_id {baseline_run_id!r} was not found")
    deltas: dict[str, object] = {}
    baseline = per_query_maps[baseline_run_id]
    for run_id, candidate in sorted(per_query_maps.items()):
        if run_id == baseline_run_id:
            continue
        metric_deltas: dict[str, object] = {}
        for metric_name in sorted(set(baseline) & set(candidate)):
            metric_deltas[metric_name] = paired_bootstrap_delta_ci(
                baseline[metric_name],
                candidate[metric_name],
                seed=seed,
                samples=bootstrap_samples,
            )
        deltas[f"{baseline_run_id}->{run_id}"] = metric_deltas
    return deltas


def paired_bootstrap_delta_ci(
    baseline: Mapping[str, float],
    candidate: Mapping[str, float],
    *,
    seed: int,
    samples: int,
    confidence: float = 0.95,
) -> dict[str, object]:
    baseline_keys = set(baseline)
    candidate_keys = set(candidate)
    if baseline_keys != candidate_keys:
        missing = sorted(baseline_keys ^ candidate_keys)
        raise BenchmarkValidationError(
            "paired bootstrap requires identical query ids for each metric; "
            f"different ids: {missing}"
        )
    if not baseline_keys:
        return {"mean_delta": None, "ci95": None, "sample_count": 0}
    if samples < 1:
        raise BenchmarkValidationError("bootstrap sample count must be positive")
    query_ids = sorted(baseline_keys)
    observed = mean(candidate[qid] - baseline[qid] for qid in query_ids)
    rng = random.Random(seed)
    bootstrapped: list[float] = []
    for _ in range(samples):
        sample_ids = [query_ids[rng.randrange(len(query_ids))] for _ in query_ids]
        bootstrapped.append(mean(candidate[qid] - baseline[qid] for qid in sample_ids))
    lower_tail = (1.0 - confidence) / 2.0
    lower = percentile(bootstrapped, lower_tail * 100.0)
    upper = percentile(bootstrapped, (1.0 - lower_tail) * 100.0)
    return {
        "mean_delta": observed,
        "ci95": [lower, upper],
        "sample_count": samples,
        "seed": seed,
    }


def recall_at_k(rows: Sequence[RunRow], relevant: Mapping[str, int], *, k: int) -> float:
    recovered = {row.doc_id for row in rows if row.rank <= k and row.doc_id in relevant}
    return len(recovered) / len(relevant) if relevant else 0.0


def hit_at_k(rows: Sequence[RunRow], relevant: Mapping[str, int], *, k: int) -> float:
    return 1.0 if any(row.rank <= k and row.doc_id in relevant for row in rows) else 0.0


def reciprocal_rank(rows: Sequence[RunRow], relevant: Mapping[str, int]) -> float:
    ranks = [row.rank for row in rows if row.doc_id in relevant]
    return 1.0 / min(ranks) if ranks else 0.0


def ndcg_at_k(rows: Sequence[RunRow], qrels: Sequence[QrelRow], *, k: int) -> float:
    relevance_by_doc = {qrel.doc_id: qrel.relevance for qrel in qrels}
    gains = [
        dcg_gain(relevance_by_doc.get(row.doc_id, 0), row.rank) for row in rows if row.rank <= k
    ]
    dcg = sum(gains)
    ideal_relevances = sorted((qrel.relevance for qrel in qrels), reverse=True)[:k]
    idcg = sum(dcg_gain(relevance, index + 1) for index, relevance in enumerate(ideal_relevances))
    return dcg / idcg if idcg else 0.0


def dcg_gain(relevance: int, rank: int) -> float:
    return (2**relevance - 1) / math.log2(rank + 1)


def required_citation_refs(qrels: Sequence[QrelRow], corpus: Mapping[str, CorpusRow]) -> set[str]:
    return citation_refs_for_qrels(
        (qrel for qrel in qrels if qrel.citation_required and qrel.relevance >= RELEVANT_THRESHOLD),
        corpus,
    )


def supported_citation_refs(qrels: Sequence[QrelRow], corpus: Mapping[str, CorpusRow]) -> set[str]:
    return citation_refs_for_qrels(
        (qrel for qrel in qrels if qrel.relevance >= RELEVANT_THRESHOLD),
        corpus,
    )


def citation_refs_for_qrels(qrels: Iterable[QrelRow], corpus: Mapping[str, CorpusRow]) -> set[str]:
    refs: set[str] = set()
    for qrel in qrels:
        refs.update(citation_refs_for_doc(corpus[qrel.doc_id]))
    return refs


def validate_citation_ids(rows: Sequence[RunRow], corpus: Mapping[str, CorpusRow]) -> None:
    known_refs = set(corpus)
    for row in corpus.values():
        known_refs.update(row.source_ref_ids)
    for row in rows:
        unknown = sorted(
            citation_id for citation_id in row.citation_ids if citation_id not in known_refs
        )
        if unknown:
            raise BenchmarkValidationError(
                f"runs.jsonl:{row.run_id}/{row.query_id}: citation outside corpus: {unknown}"
            )
        allowed_for_row = citation_refs_for_doc(corpus[row.doc_id])
        wrong_doc_refs = sorted(
            citation_id for citation_id in row.citation_ids if citation_id not in allowed_for_row
        )
        if wrong_doc_refs:
            raise BenchmarkValidationError(
                f"runs.jsonl:{row.run_id}/{row.query_id}: citation not attached to "
                f"returned doc_id {row.doc_id!r}: {wrong_doc_refs}"
            )


def citation_refs_for_doc(row: CorpusRow) -> set[str]:
    return set(row.source_ref_ids) if row.source_ref_ids else {row.doc_id}


def validate_every_query_has_qrel(
    queries: Mapping[str, QueryRow], qrels_by_query: Mapping[str, Sequence[QrelRow]]
) -> None:
    missing = sorted(query_id for query_id in queries if query_id not in qrels_by_query)
    if missing:
        raise BenchmarkValidationError(f"qrels.jsonl: missing qrels for queries: {missing}")


def validate_report(report: Mapping[str, object]) -> None:
    assert_public_safe_value(report, "report")
    ensure_mapping_fields(
        report,
        "report",
        required={
            "schema",
            "evidence_track",
            "hardware_bucket",
            "environment",
            "tokenizer",
            "seed",
            "bootstrap_samples",
            "inputs",
            "input_artifacts",
            "metrics",
            "deltas",
            "quality_gates",
            "source_mutation",
            "hard_failures",
        },
    )
    if report.get("schema") != SCHEMA:
        raise BenchmarkValidationError("report schema is invalid")
    if report.get("evidence_track") != "quality-benchmark":
        raise BenchmarkValidationError("report evidence_track is invalid")
    bucket = report.get("hardware_bucket")
    if not isinstance(bucket, str):
        raise BenchmarkValidationError("report hardware_bucket is required")
    validate_hardware_bucket(bucket)
    environment = report.get("environment")
    if not isinstance(environment, dict):
        raise BenchmarkValidationError("report environment must be an object")
    validate_environment_report(environment, bucket)
    inputs = report.get("inputs")
    if not isinstance(inputs, dict):
        raise BenchmarkValidationError("report inputs must be an object")
    validate_inputs_report(inputs)
    input_artifacts = report.get("input_artifacts")
    if not isinstance(input_artifacts, dict):
        raise BenchmarkValidationError("report input_artifacts must be an object")
    validate_input_artifacts_report(input_artifacts)
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        raise BenchmarkValidationError("report metrics must be an object")
    validate_metrics_report(metrics)
    if not isinstance(report.get("deltas"), dict):
        raise BenchmarkValidationError("report deltas must be an object")
    tokenizer = report.get("tokenizer")
    if not isinstance(tokenizer, dict):
        raise BenchmarkValidationError("report tokenizer must be an object")
    ensure_mapping_fields(
        tokenizer,
        "report.tokenizer",
        required={
            "id",
            "revision",
            "policy",
            "evidence_level",
            "verified_by_harness",
            "input_accounting",
        },
    )
    validate_tokenizer_provenance(
        require_mapping_string(tokenizer, "id", "report.tokenizer"),
        require_mapping_string(tokenizer, "revision", "report.tokenizer"),
    )
    if tokenizer.get("evidence_level") not in ALLOWED_TOKENIZER_EVIDENCE_LEVELS:
        raise BenchmarkValidationError("report.tokenizer.evidence_level is invalid")
    if not isinstance(tokenizer.get("verified_by_harness"), bool):
        raise BenchmarkValidationError("report.tokenizer.verified_by_harness must be a boolean")
    if tokenizer.get("verified_by_harness") != (
        tokenizer.get("evidence_level") == TOKENIZER_EVIDENCE_LOAD_VERIFIED
    ):
        raise BenchmarkValidationError("report.tokenizer evidence is inconsistent")
    seed = report.get("seed")
    bootstrap_samples = report.get("bootstrap_samples")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise BenchmarkValidationError("report seed must be an integer")
    if (
        isinstance(bootstrap_samples, bool)
        or not isinstance(bootstrap_samples, int)
        or bootstrap_samples < 1
    ):
        raise BenchmarkValidationError("report bootstrap_samples must be a positive integer")
    quality_gates = report.get("quality_gates")
    if not isinstance(quality_gates, dict):
        raise BenchmarkValidationError("report quality_gates must be an object")
    validate_quality_gates_report(quality_gates)
    if not isinstance(report.get("hard_failures"), list):
        raise BenchmarkValidationError("report hard_failures must be a list")
    if not all(isinstance(item, str) for item in report["hard_failures"]):
        raise BenchmarkValidationError("report hard_failures must be string values")
    source_mutation = report.get("source_mutation")
    if source_mutation is not None:
        if not isinstance(source_mutation, dict):
            raise BenchmarkValidationError("report source_mutation must be null or an object")
        ensure_mapping_fields(
            source_mutation,
            "report.source_mutation",
            required={"before_sha256", "after_sha256", "mutated"},
        )
        if not isinstance(source_mutation.get("mutated"), bool):
            raise BenchmarkValidationError("report source_mutation.mutated must be a boolean")
        for field in ("before_sha256", "after_sha256"):
            value = source_mutation.get(field)
            if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
                raise BenchmarkValidationError(
                    f"report source_mutation.{field} must be a sha256 hex digest"
                )


def validate_environment_report(environment: Mapping[str, object], hardware_bucket: str) -> None:
    ensure_mapping_fields(
        environment,
        "report.environment",
        required={
            "hardware_bucket",
            "os_family",
            "os_release_family",
            "machine_class",
            "python_version",
            "package_version",
        },
    )
    if (
        require_mapping_string(environment, "hardware_bucket", "report.environment")
        != hardware_bucket
    ):
        raise BenchmarkValidationError("report.environment.hardware_bucket must match report")
    for field in ("os_family", "os_release_family", "machine_class", "python_version"):
        require_mapping_string(environment, field, "report.environment")
    package_version = environment.get("package_version")
    if not isinstance(package_version, str):
        raise BenchmarkValidationError("report.environment.package_version must be a string")


def validate_inputs_report(inputs: Mapping[str, object]) -> None:
    ensure_mapping_fields(
        inputs,
        "report.inputs",
        required={"corpus_records", "query_records", "qrel_records", "run_ids"},
    )
    for field in ("corpus_records", "query_records", "qrel_records"):
        require_mapping_nonnegative_int(inputs, field, "report.inputs")
    run_ids = inputs.get("run_ids")
    if not isinstance(run_ids, list) or not all(isinstance(item, str) for item in run_ids):
        raise BenchmarkValidationError("report.inputs.run_ids must be a string list")


def validate_input_artifacts_report(input_artifacts: Mapping[str, object]) -> None:
    ensure_mapping_fields(
        input_artifacts,
        "report.input_artifacts",
        required=set(BENCHMARK_INPUT_FILES),
    )
    for file_name in BENCHMARK_INPUT_FILES:
        value = input_artifacts.get(file_name)
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            raise BenchmarkValidationError(
                f"report.input_artifacts.{file_name} must be a sha256 hex digest"
            )


def validate_metrics_report(metrics: Mapping[str, object]) -> None:
    for run_id, run_metrics in metrics.items():
        if not isinstance(run_id, str) or not isinstance(run_metrics, dict):
            raise BenchmarkValidationError("report.metrics must map run ids to objects")
        validate_run_metrics_report(run_metrics, f"report.metrics.{run_id}", run_id=run_id)


def validate_run_metrics_report(
    run_metrics: Mapping[str, object],
    label: str,
    *,
    run_id: str,
) -> None:
    required = {
        "run_id",
        "surface",
        "evaluation_mode",
        "query_count",
        "run_row_count",
        "payload_tokens",
        "payload_bytes",
        "latency_ms",
        "query_classes",
    }
    evaluation_mode = run_metrics.get("evaluation_mode")
    if evaluation_mode == RETRIEVAL_EVALUATION_MODE:
        telemetry_only = False
        required |= {
            "judged_query_count",
            "recall_at_5",
            "hit_at_5",
            "mrr",
            "ndcg_at_10",
            "citation_precision",
            "citation_recall",
            "negative_false_positive_rate",
            "context_tokens",
        }
    elif evaluation_mode == TELEMETRY_ONLY_EVALUATION_MODE:
        telemetry_only = True
    else:
        raise BenchmarkValidationError(f"{label}.evaluation_mode is invalid")

    ensure_mapping_fields(run_metrics, label, required=required)
    if require_mapping_string(run_metrics, "run_id", label) != run_id:
        raise BenchmarkValidationError(f"{label}.run_id must match its key")
    surface = require_mapping_string(run_metrics, "surface", label)
    if surface not in ALLOWED_SURFACES:
        raise BenchmarkValidationError(f"{label}.surface is invalid")
    if (surface in TELEMETRY_ONLY_SURFACES) != telemetry_only:
        raise BenchmarkValidationError(f"{label}.evaluation_mode does not match surface")
    validate_metric_body(run_metrics, label, telemetry_only=telemetry_only)
    query_classes = run_metrics.get("query_classes")
    validate_query_class_metrics_report(
        query_classes,
        f"{label}.query_classes",
        telemetry_only=telemetry_only,
    )
    assert isinstance(query_classes, dict)
    class_query_count = sum(
        require_mapping_nonnegative_int(class_metrics, "query_count", f"{label}.{query_class}")
        for query_class, class_metrics in query_classes.items()
        if isinstance(class_metrics, dict)
    )
    class_row_count = sum(
        require_mapping_nonnegative_int(class_metrics, "run_row_count", f"{label}.{query_class}")
        for query_class, class_metrics in query_classes.items()
        if isinstance(class_metrics, dict)
    )
    if class_query_count != require_mapping_nonnegative_int(run_metrics, "query_count", label):
        raise BenchmarkValidationError(f"{label}.query_classes query_count total is inconsistent")
    if class_row_count != require_mapping_nonnegative_int(run_metrics, "run_row_count", label):
        raise BenchmarkValidationError(f"{label}.query_classes run_row_count total is inconsistent")
    if not telemetry_only:
        class_judged_count = sum(
            require_mapping_nonnegative_int(
                class_metrics,
                "judged_query_count",
                f"{label}.{query_class}",
            )
            for query_class, class_metrics in query_classes.items()
            if isinstance(class_metrics, dict)
        )
        if class_judged_count != require_mapping_nonnegative_int(
            run_metrics,
            "judged_query_count",
            label,
        ):
            raise BenchmarkValidationError(
                f"{label}.query_classes judged_query_count total is inconsistent"
            )


def validate_query_class_metrics_report(
    query_classes: object,
    label: str,
    *,
    telemetry_only: bool,
) -> None:
    if not isinstance(query_classes, dict):
        raise BenchmarkValidationError(f"{label} must be an object")
    for query_class, class_metrics in query_classes.items():
        if not isinstance(query_class, str) or query_class not in ALLOWED_QUERY_CLASSES:
            raise BenchmarkValidationError(f"{label} contains an unknown query class")
        if not isinstance(class_metrics, dict):
            raise BenchmarkValidationError(f"{label}.{query_class} must be an object")
        required = {
            "query_class",
            "query_count",
            "run_row_count",
            "payload_tokens",
            "payload_bytes",
            "latency_ms",
        }
        if not telemetry_only:
            required |= {
                "judged_query_count",
                "recall_at_5",
                "hit_at_5",
                "mrr",
                "ndcg_at_10",
                "citation_precision",
                "citation_recall",
                "negative_false_positive_rate",
                "context_tokens",
            }
        ensure_mapping_fields(class_metrics, f"{label}.{query_class}", required=required)
        if require_mapping_string(class_metrics, "query_class", f"{label}.{query_class}") != (
            query_class
        ):
            raise BenchmarkValidationError(f"{label}.{query_class}.query_class must match its key")
        validate_metric_body(
            class_metrics,
            f"{label}.{query_class}",
            telemetry_only=telemetry_only,
        )


def validate_metric_body(
    metric_body: Mapping[str, object],
    label: str,
    *,
    telemetry_only: bool,
) -> None:
    int_fields = ("query_count", "run_row_count")
    if not telemetry_only:
        int_fields = (*int_fields, "judged_query_count")
    for field in int_fields:
        require_mapping_nonnegative_int(metric_body, field, label)
    query_count = require_mapping_nonnegative_int(metric_body, "query_count", label)
    if not telemetry_only:
        for field in QUALITY_METRIC_FIELDS:
            validate_optional_report_number(metric_body.get(field), f"{label}.{field}")
    distribution_fields = (
        TELEMETRY_DISTRIBUTION_FIELDS if telemetry_only else RETRIEVAL_DISTRIBUTION_FIELDS
    )
    for field in distribution_fields:
        distribution_value = metric_body.get(field)
        if not isinstance(distribution_value, dict):
            raise BenchmarkValidationError(f"{label}.{field} must be an object")
        validate_distribution_report(distribution_value, f"{label}.{field}")
        distribution_count = require_mapping_nonnegative_int(
            distribution_value,
            "count",
            f"{label}.{field}",
        )
        if distribution_count != query_count:
            raise BenchmarkValidationError(f"{label}.{field}.count must match {label}.query_count")


def validate_distribution_report(distribution_value: Mapping[str, object], label: str) -> None:
    ensure_mapping_fields(
        distribution_value,
        label,
        required={"count", "mean", "p50", "p95"},
    )
    require_mapping_nonnegative_int(distribution_value, "count", label)
    for field in ("mean", "p50", "p95"):
        validate_optional_report_number(distribution_value.get(field), f"{label}.{field}")


def validate_quality_gates_report(quality_gates: Mapping[str, object]) -> None:
    ensure_mapping_fields(
        quality_gates,
        "report.quality_gates",
        required={
            "overall_status",
            "public_quality_claim",
            "common_failures",
            "thresholds",
            "runs",
        },
    )
    if quality_gates.get("overall_status") not in {"pass", "fail", "hard_fail"}:
        raise BenchmarkValidationError("report.quality_gates.overall_status is invalid")
    if not isinstance(quality_gates.get("public_quality_claim"), bool):
        raise BenchmarkValidationError("report.quality_gates.public_quality_claim is required")
    common_failures = quality_gates.get("common_failures")
    if not isinstance(common_failures, list) or not all(
        isinstance(item, str) for item in common_failures
    ):
        raise BenchmarkValidationError("report.quality_gates.common_failures must be strings")
    if not isinstance(quality_gates.get("thresholds"), dict):
        raise BenchmarkValidationError("report.quality_gates.thresholds must be an object")
    runs = quality_gates.get("runs")
    if not isinstance(runs, dict):
        raise BenchmarkValidationError("report.quality_gates.runs must be an object")
    for run_id, run_gate in runs.items():
        if not isinstance(run_id, str) or not isinstance(run_gate, dict):
            raise BenchmarkValidationError("report.quality_gates.runs must map run ids to objects")
        ensure_mapping_fields(
            run_gate,
            f"report.quality_gates.runs.{run_id}",
            required={"status", "failures", "gate_scope"},
        )
        if run_gate.get("status") not in {"pass", "fail"}:
            raise BenchmarkValidationError(f"report.quality_gates.runs.{run_id}.status is invalid")
        if run_gate.get("gate_scope") not in {
            RETRIEVAL_EVALUATION_MODE,
            TELEMETRY_ONLY_EVALUATION_MODE,
        }:
            raise BenchmarkValidationError(
                f"report.quality_gates.runs.{run_id}.gate_scope is invalid"
            )
        failures = run_gate.get("failures")
        if not isinstance(failures, list) or not all(isinstance(item, str) for item in failures):
            raise BenchmarkValidationError(
                f"report.quality_gates.runs.{run_id}.failures must be strings"
            )


def evaluate_quality_gates(
    metrics_by_run: Mapping[str, Mapping[str, object]],
    *,
    baseline_run_id: str | None,
    tokenizer: TokenizerProvenance,
    hard_failures: Sequence[str],
) -> dict[str, object]:
    common_failures: list[str] = []
    retrieval_metrics_by_run = {
        run_id: metrics
        for run_id, metrics in metrics_by_run.items()
        if not metrics_are_telemetry_only(metrics)
    }
    query_counts = [
        int(metrics["query_count"])
        for metrics in retrieval_metrics_by_run.values()
        if isinstance(metrics.get("query_count"), int)
    ]
    minimum_query_count = min(query_counts, default=0)
    if minimum_query_count < PUBLIC_MIN_QUERY_COUNT:
        common_failures.append(
            "public quality requires at least "
            f"{PUBLIC_MIN_QUERY_COUNT} total queries; observed {minimum_query_count}"
        )
    judged_counts = [
        int(metrics["judged_query_count"])
        for metrics in retrieval_metrics_by_run.values()
        if isinstance(metrics.get("judged_query_count"), int)
    ]
    minimum_judged = min(judged_counts, default=0)
    if minimum_judged < PUBLIC_MIN_JUDGED_QUERY_COUNT:
        common_failures.append(
            "public quality requires at least "
            f"{PUBLIC_MIN_JUDGED_QUERY_COUNT} judged queries; observed {minimum_judged}"
        )
    if tokenizer.evidence_level not in PUBLIC_QUALITY_TOKENIZER_EVIDENCE_LEVELS:
        common_failures.append(
            "tokenizer evidence is declared provenance only; this harness did not "
            "load and verify the Qwen tokenizer"
        )

    baseline_metrics = retrieval_metrics_by_run.get(baseline_run_id or "")
    runs: dict[str, object] = {}
    any_run_failures = False
    for run_id, metrics in sorted(metrics_by_run.items()):
        telemetry_only = metrics_are_telemetry_only(metrics)
        failures = [] if telemetry_only else metric_threshold_failures(metrics)
        if (
            not telemetry_only
            and baseline_metrics is not None
            and run_id != baseline_run_id
            and context_token_regressed_without_quality_gain(baseline_metrics, metrics)
        ):
            failures.append(
                "context_tokens.p95 exceeds baseline by more than "
                f"{int((CONTEXT_TOKEN_P95_BASELINE_RATIO - 1.0) * 100)}% "
                "without a material recall or citation-recall gain"
            )
        any_run_failures = any_run_failures or bool(failures)
        runs[run_id] = {
            "status": "fail" if failures or common_failures or hard_failures else "pass",
            "gate_scope": (
                TELEMETRY_ONLY_EVALUATION_MODE if telemetry_only else RETRIEVAL_EVALUATION_MODE
            ),
            "failures": failures,
        }

    overall_status = "pass"
    if hard_failures:
        overall_status = "hard_fail"
    elif common_failures or any_run_failures:
        overall_status = "fail"
    return {
        "overall_status": overall_status,
        "public_quality_claim": overall_status == "pass",
        "common_failures": common_failures,
        "thresholds": {
            "min_queries": PUBLIC_MIN_QUERY_COUNT,
            "min_judged_queries": PUBLIC_MIN_JUDGED_QUERY_COUNT,
            "metrics": {
                name: {"operator": operator, "threshold": threshold}
                for name, (operator, threshold) in QUALITY_THRESHOLDS.items()
            },
            "context_tokens_p95_baseline_ratio_max": CONTEXT_TOKEN_P95_BASELINE_RATIO,
        },
        "runs": runs,
    }


def metrics_are_telemetry_only(metrics: Mapping[str, object]) -> bool:
    return metrics.get("evaluation_mode") == TELEMETRY_ONLY_EVALUATION_MODE


def metric_threshold_failures(metrics: Mapping[str, object]) -> list[str]:
    failures: list[str] = []
    for metric_name, (operator, threshold) in QUALITY_THRESHOLDS.items():
        value = optional_number(metrics.get(metric_name))
        if value is None:
            failures.append(f"{metric_name} is missing")
        elif operator == ">=" and value < threshold:
            failures.append(f"{metric_name} {value:.6g} is below public threshold {threshold}")
        elif operator == "<=" and value > threshold:
            failures.append(f"{metric_name} {value:.6g} exceeds public threshold {threshold}")
    return failures


def context_token_regressed_without_quality_gain(
    baseline_metrics: Mapping[str, object], candidate_metrics: Mapping[str, object]
) -> bool:
    baseline_p95 = distribution_number(baseline_metrics, "context_tokens", "p95")
    candidate_p95 = distribution_number(candidate_metrics, "context_tokens", "p95")
    if baseline_p95 is None or candidate_p95 is None or baseline_p95 <= 0:
        return False
    if candidate_p95 <= baseline_p95 * CONTEXT_TOKEN_P95_BASELINE_RATIO:
        return False
    return not has_material_quality_gain(baseline_metrics, candidate_metrics)


def has_material_quality_gain(
    baseline_metrics: Mapping[str, object], candidate_metrics: Mapping[str, object]
) -> bool:
    for metric_name in ("recall_at_5", "citation_recall"):
        baseline_value = optional_number(baseline_metrics.get(metric_name))
        candidate_value = optional_number(candidate_metrics.get(metric_name))
        if baseline_value is None or candidate_value is None:
            continue
        if candidate_value - baseline_value >= PUBLIC_MATERIAL_QUALITY_IMPROVEMENT:
            return True
    return False


def optional_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(float(value)):
        return None
    return float(value)


def distribution_number(
    metrics: Mapping[str, object], distribution_name: str, statistic_name: str
) -> float | None:
    distribution_value = metrics.get(distribution_name)
    if not isinstance(distribution_value, dict):
        return None
    return optional_number(distribution_value.get(statistic_name))


def public_environment(hardware_bucket: str) -> dict[str, str]:
    return {
        "hardware_bucket": hardware_bucket,
        "os_family": platform.system().lower() or UNKNOWN_VALUE,
        "os_release_family": (platform.release().split("-", 1)[0] or UNKNOWN_VALUE),
        "machine_class": platform.machine() or UNKNOWN_VALUE,
        "python_version": (
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        "package_version": __version__,
    }


def validate_hardware_bucket(hardware_bucket: str) -> None:
    if hardware_bucket not in ALLOWED_HARDWARE_BUCKETS:
        raise BenchmarkValidationError(
            f"hardware_bucket must be one of {sorted(ALLOWED_HARDWARE_BUCKETS)}"
        )


def compute_tree_digest(root: Path) -> str:
    if not root.exists() or not root.is_dir():
        raise BenchmarkValidationError("source_root must exist and be a directory")
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if any(part in DIGEST_EXCLUDED_DIRS for part in path.relative_to(root).parts):
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def canonical_text_bytes(raw: bytes) -> bytes:
    """Return platform-independent UTF-8 text bytes for public artifact hashing."""
    if raw.startswith(UTF8_BOM):
        raw = raw[len(UTF8_BOM) :]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BenchmarkValidationError("benchmark text artifact must be valid UTF-8") from error
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def canonical_text_sha256(raw: bytes) -> str:
    return hashlib.sha256(canonical_text_bytes(raw)).hexdigest()


def canonical_text_file_sha256(path: Path) -> str:
    return canonical_text_sha256(path.read_bytes())


def canonical_markdown_bytes(raw: bytes) -> bytes:
    """Return platform-independent UTF-8 Markdown bytes for corpus hashing."""
    return canonical_text_bytes(raw)


def canonical_markdown_sha256(raw: bytes) -> str:
    return canonical_text_sha256(raw)


def canonical_markdown_file_sha256(path: Path) -> str:
    return canonical_text_file_sha256(path)


def validate_corpus_source_hashes(corpus: Mapping[str, CorpusRow], source_root: Path) -> None:
    if not source_root.exists() or not source_root.is_dir():
        raise BenchmarkValidationError("source_root must exist and be a directory")
    resolved_root = source_root.resolve()
    for row in corpus.values():
        source_path = (resolved_root / Path(row.path)).resolve()
        try:
            source_path.relative_to(resolved_root)
        except ValueError as error:
            raise BenchmarkValidationError(
                f"corpus.jsonl:{row.doc_id}: path escapes source_root"
            ) from error
        if not source_path.is_file():
            raise BenchmarkValidationError(
                f"corpus.jsonl:{row.doc_id}: source file missing for path {row.path!r}"
            )
        observed = canonical_markdown_file_sha256(source_path)
        if observed != row.sha256:
            raise BenchmarkValidationError(
                f"corpus.jsonl:{row.doc_id}: sha256 does not match canonical Markdown source file"
            )


def compute_input_artifact_digests(input_dir: Path) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for file_name in BENCHMARK_INPUT_FILES:
        path = input_dir / file_name
        if not path.exists() or not path.is_file():
            raise BenchmarkValidationError(f"missing benchmark file: {file_name}")
        artifacts[file_name] = canonical_text_file_sha256(path)
    return artifacts


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise BenchmarkValidationError(f"missing benchmark file: {path.name}")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise BenchmarkValidationError(
                f"{path.name}:{line_number}: invalid JSONL record"
            ) from error
        if not isinstance(record, dict):
            raise BenchmarkValidationError(f"{path.name}:{line_number}: record must be an object")
        assert_public_safe_value(record, f"{path.name}:{line_number}")
        records.append(record)
    return records


def ensure_fields(
    record: Mapping[str, Any],
    path: Path,
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    allowed = required | optional
    missing = sorted(required - set(record))
    unknown = sorted(set(record) - allowed)
    if missing:
        raise BenchmarkValidationError(f"{path.name}: missing required fields: {missing}")
    if unknown:
        raise BenchmarkValidationError(
            f"{path.name}: unknown fields are not public-safe: {unknown}"
        )


def require_string(record: Mapping[str, Any], field: str, path: Path) -> str:
    value = record[field]
    if not isinstance(value, str) or not value:
        raise BenchmarkValidationError(f"{path.name}: {field} must be a non-empty string")
    return value


def require_bool(record: Mapping[str, Any], field: str, path: Path) -> bool:
    value = record[field]
    if not isinstance(value, bool):
        raise BenchmarkValidationError(f"{path.name}: {field} must be a boolean")
    return value


def require_int(record: Mapping[str, Any], field: str, path: Path) -> int:
    value = record[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise BenchmarkValidationError(f"{path.name}: {field} must be an integer")
    return value


def require_float(record: Mapping[str, Any], field: str, path: Path) -> float:
    value = record[field]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BenchmarkValidationError(f"{path.name}: {field} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise BenchmarkValidationError(f"{path.name}: {field} must be a finite number")
    return number


def require_string_tuple(record: Mapping[str, Any], field: str, path: Path) -> tuple[str, ...]:
    value = record[field]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BenchmarkValidationError(f"{path.name}: {field} must be a string list")
    return tuple(value)


def parse_optional_nonnegative_int(value: Any, path: Path, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BenchmarkValidationError(f"{path.name}: {field} must be a non-negative integer")
    return value


def parse_support_spans(value: Any, path: Path, query_id: str) -> tuple[SupportSpan, ...]:
    if not isinstance(value, list):
        raise BenchmarkValidationError(f"{path.name}:{query_id}: support_spans must be a list")
    spans: list[SupportSpan] = []
    for index, span in enumerate(value):
        if not isinstance(span, dict):
            raise BenchmarkValidationError(
                f"{path.name}:{query_id}: support_spans[{index}] must be an object"
            )
        ensure_mapping_fields(
            span,
            f"{path.name}:{query_id}:support_spans[{index}]",
            required={"start", "end"},
        )
        start = span.get("start")
        end = span.get("end")
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end <= start
        ):
            raise BenchmarkValidationError(
                f"{path.name}:{query_id}: support span must have integer start < end"
            )
        spans.append(SupportSpan(start=start, end=end))
    return tuple(spans)


def validate_public_relative_path(value: str, label: str) -> None:
    if "\\" in value or value.startswith("/") or ":" in value:
        raise BenchmarkValidationError(f"{label}: path must be source-root-relative POSIX text")
    parts = value.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise BenchmarkValidationError(
            f"{label}: path must not contain empty, dot, or dot-dot parts"
        )


def group_runs_by_id(rows: Sequence[RunRow]) -> dict[str, list[RunRow]]:
    grouped: dict[str, list[RunRow]] = defaultdict(list)
    for row in rows:
        grouped[row.run_id].append(row)
    return dict(grouped)


def distribution(values: Sequence[float] | Any) -> dict[str, float | int | None]:
    items = list(values)
    if not items:
        return {"count": 0, "mean": None, "p50": None, "p95": None}
    return {
        "count": len(items),
        "mean": mean(items),
        "p50": percentile(items, 50),
        "p95": percentile(items, 95),
    }


def mean_or_none(values: Sequence[float] | Any) -> float | None:
    items = list(values)
    return mean(items) if items else None


def mean(values: Sequence[float]) -> float:
    return statistics.fmean(values)


def percentile(values: Sequence[float], percentile_rank: float) -> float:
    if not values:
        raise BenchmarkValidationError("cannot compute percentile for empty values")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = max(1, math.ceil((percentile_rank / 100.0) * len(ordered)))
    return float(ordered[min(rank, len(ordered)) - 1])


def assert_public_safe_value(value: object, label: str) -> None:
    violations = scan_redaction_violations(value)
    if violations:
        raise BenchmarkValidationError(
            f"{label} contains private or sensitive data: {', '.join(violations)}"
        )


def scan_redaction_violations(value: object) -> list[str]:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return sorted({name for name, pattern in PRIVATE_PATTERNS if pattern.search(text)})


def require_mapping_string(mapping: Mapping[str, object], field: str, label: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value:
        raise BenchmarkValidationError(f"{label}.{field} must be a non-empty string")
    return value


def require_mapping_nonnegative_int(mapping: Mapping[str, object], field: str, label: str) -> int:
    value = mapping.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BenchmarkValidationError(f"{label}.{field} must be a non-negative integer")
    return value


def validate_optional_report_number(value: object, label: str) -> None:
    if value is None:
        return
    if optional_number(value) is None:
        raise BenchmarkValidationError(f"{label} must be null or a finite number")


def ensure_mapping_fields(
    mapping: Mapping[str, object],
    label: str,
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    allowed = required | optional
    missing = sorted(required - set(mapping))
    unknown = sorted(set(mapping) - allowed)
    if missing:
        raise BenchmarkValidationError(f"{label}: missing required fields: {missing}")
    if unknown:
        raise BenchmarkValidationError(f"{label}: unknown fields are not public-safe: {unknown}")


def write_report(report: Mapping[str, object], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate_output_not_inside_source_root(output: Path, source_root: Path | None) -> None:
    if source_root is None:
        return
    try:
        output.resolve().relative_to(source_root.resolve())
    except ValueError:
        return
    raise BenchmarkValidationError(
        "output must be outside source_root so the source mutation guard remains meaningful"
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate deterministic verified-source benchmark JSONL artifacts."
    )
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--hardware-bucket", choices=sorted(ALLOWED_HARDWARE_BUCKETS), required=True
    )
    parser.add_argument("--tokenizer-id", required=True)
    parser.add_argument("--tokenizer-revision", required=True)
    parser.add_argument("--seed", type=int, default=20260730)
    parser.add_argument("--bootstrap-samples", type=int, default=1000)
    parser.add_argument("--baseline-run-id")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument(
        "--verify-tokenizer",
        action="store_true",
        help="Load the configured Qwen tokenizer locally and mark tokenizer evidence as verified.",
    )
    return parser.parse_args(argv)


def tokenizer_provenance_from_args(args: argparse.Namespace) -> TokenizerProvenance:
    if args.verify_tokenizer:
        HuggingFaceQwenTokenizerAdapter.load(args.tokenizer_id, args.tokenizer_revision)
        return TokenizerProvenance(
            args.tokenizer_id,
            args.tokenizer_revision,
            evidence_level=TOKENIZER_EVIDENCE_LOAD_VERIFIED,
            verified_by_harness=True,
        )
    return TokenizerProvenance(args.tokenizer_id, args.tokenizer_revision)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        validate_output_not_inside_source_root(args.output, args.source_root)
        report = run_benchmark(
            args.input_dir,
            hardware_bucket=args.hardware_bucket,
            tokenizer=tokenizer_provenance_from_args(args),
            seed=args.seed,
            bootstrap_samples=args.bootstrap_samples,
            baseline_run_id=args.baseline_run_id,
            source_root=args.source_root,
        )
        write_report(report, args.output)
    except BenchmarkValidationError as error:
        print(f"verified source benchmark failed: {error}", file=sys.stderr)
        return 2
    print(f"verified source benchmark report written: {args.output}")
    if report["hard_failures"]:
        print("verified source benchmark hard failures present", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
