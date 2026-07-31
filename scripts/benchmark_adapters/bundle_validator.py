"""Validate offline llmwiki benchmark bundle artifacts.

This module intentionally stays under ``scripts``. It validates local benchmark
adapter output without changing runtime service code or source adapter behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

SCHEMA_ID = "llmwiki-benchmark-bundle-v1"
BUNDLE_JSONL_FILES = ("corpus.jsonl", "queries.jsonl", "qrels.jsonl", "evidence.jsonl")
REQUIRED_BUNDLE_FILES = (*BUNDLE_JSONL_FILES, "provenance.json")
ANSWERABILITY_VALUES = {"answerable", "unanswerable", "unknown"}
EVALUATION_SPLITS = {"calibration", "holdout", "smoke"}
LOCATOR_GRANULARITIES = {
    "document",
    "section",
    "paragraph",
    "char_span",
    "token_span",
    "passage",
}
SPAN_GRANULARITIES = {"char_span", "token_span"}
REQUIRED_CHECKSUMS = frozenset(BUNDLE_JSONL_FILES)
DEFAULT_WORKSPACE_NAME = ".llmwiki-work"
DEFAULT_BENCHMARK_WORKSPACE = Path(DEFAULT_WORKSPACE_NAME) / "benchmark-adapters"

GateMode = Literal["public-report", "bundle-release"]
JsonObject = dict[str, Any]


PRIVATE_PATTERNS = (
    ("windows-absolute-path", re.compile(r"\b[A-Za-z]:[\\/][^\s\"']*")),
    ("windows-unc-path", re.compile(r"\\\\[A-Za-z0-9._$-]+\\[^\s\"']+")),
    ("file-url", re.compile(r"\bfile://[^\s\"']+", re.IGNORECASE)),
    (
        "posix-local-path",
        re.compile(
            r"(?<![A-Za-z0-9.:/\\-])/"
            r"(?:Users|home|root|tmp|var|mnt|media|workspace|raid|data|opt|srv)"
            r"(?:[\\/][^\s\"']*)?"
        ),
    ),
    (
        "private-workspace-path",
        re.compile(r"(?<![A-Za-z0-9._-])(?:\.llmwiki-work|\.runtime-logs|\.codex)(?:[\\/]|$)"),
    ),
    (
        "private-url",
        re.compile(
            r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|10\.\d+\.\d+\.\d+|"
            r"192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)"
            r"(?:[:/][^\s\"']*)?",
            re.IGNORECASE,
        ),
    ),
    (
        "private-host",
        re.compile(
            r"(?<![A-Za-z0-9.-])(?:localhost|127\.0\.0\.1|0\.0\.0\.0|10\.\d+\.\d+\.\d+|"
            r"192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+):\d{2,5}",
            re.IGNORECASE,
        ),
    ),
    ("private-domain", re.compile(r"https?://[A-Za-z0-9.-]+\.(?:local|lan|internal)\b")),
    ("bearer-token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")),
    (
        "secret-assignment",
        re.compile(
            r"\b[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)\s*[:=]\s*"
            r"['\"]?[A-Za-z0-9._~+/=-]{8,}",
            re.IGNORECASE,
        ),
    ),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}")),
)
MUTABLE_SOURCE_REVISIONS = {
    "head",
    "latest",
    "main",
    "master",
    "tip",
    "trunk",
    "develop",
    "dev",
}
MUTABLE_REF_PREFIXES = ("refs/heads/", "origin/", "branch:", "tag:", "release:")
UNKNOWN_LICENSE_MARKERS = {
    "",
    "unknown",
    "unclear",
    "noassertion",
    "none",
    "tbd",
    "todo",
    "license-ref-unknown",
}
UNKNOWN_LICENSE_TOKEN_PATTERN = re.compile(
    r"(?:^|[^A-Za-z0-9])(?:noassertion|unknown|unclear)(?:$|[^A-Za-z0-9])|"
    r"(?:^|[^A-Za-z0-9])licenseref-[A-Za-z0-9._-]+",
    re.IGNORECASE,
)
NONCOMMERCIAL_MARKERS = ("cc-by-nc", "cc-by-nc-sa", "non-commercial", "noncommercial")
UNCLEAR_POLICY_MARKERS = ("unknown", "unclear", "tbd", "todo", "not-reviewed")
NONCOMMERCIAL_POLICY_MARKERS = ("non-commercial", "noncommercial", "cc-by-nc")
PUBLIC_REPORT_ALLOWED_POLICIES = {
    "allowed",
    "allowed-with-attribution",
    "derived-metrics-only",
    "sanitized-aggregate-only",
    "aggregate-metrics-only",
}
DISTRIBUTABLE_POLICIES = {
    "redistributable",
    "allowed",
    "allowed-with-attribution",
    "public-domain",
    "bundle-redistributable",
}
LEGAL_DISCLAIMER = (
    "This gate enforces repository benchmark publication policy only and does not make "
    "legal determinations."
)


class BundleValidationError(ValueError):
    """Raised when a benchmark bundle violates schema or publication policy."""


@dataclass(frozen=True)
class QueryMetricEligibility:
    query_id: str
    retrieval: bool
    answerability: bool
    abstention: bool
    negative_final_answer_false_positive: bool

    def as_json(self) -> dict[str, object]:
        return {
            "query_id": self.query_id,
            "retrieval": self.retrieval,
            "answerability": self.answerability,
            "abstention": self.abstention,
            "negative_final_answer_false_positive": self.negative_final_answer_false_positive,
        }


@dataclass(frozen=True)
class BundleValidationResult:
    bundle_dir: Path
    corpus_ids: frozenset[str]
    query_ids: frozenset[str]
    evidence_ids: frozenset[str]
    qrel_count: int
    evidence_count: int
    query_metric_eligibility: Mapping[str, QueryMetricEligibility]
    provenance: Mapping[str, Any]

    def as_json(self) -> dict[str, object]:
        return {
            "schema_id": SCHEMA_ID,
            "bundle_dir": str(self.bundle_dir),
            "corpus_count": len(self.corpus_ids),
            "query_count": len(self.query_ids),
            "qrel_count": self.qrel_count,
            "evidence_count": self.evidence_count,
            "query_metric_eligibility": {
                query_id: eligibility.as_json()
                for query_id, eligibility in sorted(self.query_metric_eligibility.items())
            },
            "bundle_id": self.provenance.get("bundle_id"),
            "dataset": self.provenance.get("dataset"),
        }


@dataclass(frozen=True)
class PublicReleaseGateResult:
    mode: GateMode
    passed: bool
    blockers: tuple[str, ...]
    disclaimer: str = LEGAL_DISCLAIMER

    def as_json(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "passed": self.passed,
            "blockers": list(self.blockers),
            "disclaimer": self.disclaimer,
        }


@dataclass(frozen=True)
class QrelRef:
    query_id: str
    corpus_id: str
    relevance: float


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    query_id: str
    depends_on: tuple[str, ...]
    supports_claim_ids: tuple[str, ...]


def validate_bundle(bundle_dir: Path) -> BundleValidationResult:
    """Validate a normalized benchmark bundle and return schema-derived metadata."""
    resolved_bundle = bundle_dir.resolve()
    if not resolved_bundle.is_dir():
        raise BundleValidationError(f"bundle_dir must be a directory: {bundle_dir}")
    for file_name in REQUIRED_BUNDLE_FILES:
        path = resolved_bundle / file_name
        if not path.is_file():
            raise BundleValidationError(f"missing normalized bundle file: {file_name}")

    corpus_text_lengths = _validate_corpus_rows(resolved_bundle / "corpus.jsonl")
    corpus_ids = frozenset(corpus_text_lengths)
    query_claim_ids, query_answerability = _validate_query_rows(resolved_bundle / "queries.jsonl")
    qrels = _validate_qrel_rows(
        resolved_bundle / "qrels.jsonl",
        corpus_ids=corpus_ids,
        query_ids=frozenset(query_claim_ids),
    )
    evidence_refs = _validate_evidence_rows(
        resolved_bundle / "evidence.jsonl",
        corpus_text_lengths=corpus_text_lengths,
        query_claim_ids=query_claim_ids,
    )
    _validate_evidence_dependencies(evidence_refs)
    provenance = validate_provenance(
        resolved_bundle / "provenance.json",
        bundle_dir=resolved_bundle,
    )
    eligibility = _query_metric_eligibility(query_answerability, qrels)

    return BundleValidationResult(
        bundle_dir=resolved_bundle,
        corpus_ids=corpus_ids,
        query_ids=frozenset(query_claim_ids),
        evidence_ids=frozenset(ref.evidence_id for ref in evidence_refs),
        qrel_count=len(qrels),
        evidence_count=len(evidence_refs),
        query_metric_eligibility=eligibility,
        provenance=provenance,
    )


def validate_provenance(path: Path, *, bundle_dir: Path) -> JsonObject:
    """Validate path-free public provenance and component file checksums."""
    record = load_json_object(path)
    assert_public_safe_value(record, "provenance.json")
    ensure_mapping_fields(
        record,
        "provenance.json",
        required={
            "schema_id",
            "bundle_id",
            "dataset",
            "source_url",
            "source_revision",
            "adapter",
            "checksums",
            "component_licenses",
        },
        optional={"source_release"},
    )
    schema_id = require_string(record, "schema_id", "provenance.json")
    if schema_id != SCHEMA_ID:
        raise BundleValidationError(f"provenance.json.schema_id must be {SCHEMA_ID!r}")
    require_string(record, "bundle_id", "provenance.json")
    require_string(record, "dataset", "provenance.json")
    source_url = require_string(record, "source_url", "provenance.json")
    validate_public_url(source_url, "provenance.json.source_url")
    source_revision = require_string(record, "source_revision", "provenance.json")
    validate_immutable_source_revision(source_revision, "provenance.json.source_revision")
    if "source_release" in record:
        require_string(record, "source_release", "provenance.json")
    validate_adapter_info(require_mapping(record, "adapter", "provenance.json"))
    validate_checksums(
        require_mapping(record, "checksums", "provenance.json"),
        bundle_dir=bundle_dir,
    )
    validate_component_licenses(
        require_object_list(record.get("component_licenses"), "provenance.json.component_licenses")
    )
    return record


def evaluate_public_release_gate(
    bundle_dir: Path,
    *,
    mode: GateMode = "public-report",
) -> PublicReleaseGateResult:
    """Run policy gates for public reporting or distributable bundle release."""
    if mode not in {"public-report", "bundle-release"}:
        raise BundleValidationError("mode must be 'public-report' or 'bundle-release'")
    result = validate_bundle(bundle_dir)
    component_licenses = require_object_list(
        result.provenance.get("component_licenses"),
        "provenance.json.component_licenses",
    )
    blockers: list[str] = []
    for component in component_licenses:
        label = f"component {component.get('component', '<unknown>')!r}"
        license_spdx = str(component.get("license_spdx", ""))
        if is_unknown_license(license_spdx):
            blockers.append(f"{label}: unknown or unclear license blocks public release")
        if is_noncommercial_text(license_spdx):
            blockers.append(f"{label}: non-commercial license blocks public release")

        redistribution_policy = str(component.get("redistribution_policy", ""))
        public_report_policy = str(component.get("public_report_policy", ""))
        if is_unclear_policy(redistribution_policy):
            blockers.append(f"{label}: redistribution policy is unknown or unclear")
        if is_noncommercial_text(redistribution_policy):
            blockers.append(f"{label}: non-commercial redistribution policy blocks release")
        if is_unclear_policy(public_report_policy):
            blockers.append(f"{label}: public-report policy is unknown or unclear")
        if is_noncommercial_text(public_report_policy):
            blockers.append(f"{label}: non-commercial public-report policy blocks release")
        if (
            mode == "public-report"
            and normalized_policy(public_report_policy) not in PUBLIC_REPORT_ALLOWED_POLICIES
        ):
            blockers.append(f"{label}: public-report policy does not allow public reporting")
        if (
            mode == "bundle-release"
            and normalized_policy(redistribution_policy) not in DISTRIBUTABLE_POLICIES
        ):
            blockers.append(f"{label}: redistribution policy does not allow bundle release")

    unique_blockers = tuple(dict.fromkeys(blockers))
    return PublicReleaseGateResult(
        mode=mode,
        passed=not unique_blockers,
        blockers=unique_blockers,
    )


def validate_local_run_manifest(path: Path, *, repo_root: Path) -> None:
    """Validate that a run manifest is local-only and not in a commit-ready path."""
    if path.name != "run-manifest.json":
        raise BundleValidationError("local run evidence must be named run-manifest.json")
    if path.exists():
        load_json_object(path)

    resolved_manifest = path.resolve()
    resolved_repo = repo_root.resolve()
    try:
        relative = resolved_manifest.relative_to(resolved_repo)
    except ValueError:
        return
    if not relative.parts or relative.parts[0] != DEFAULT_WORKSPACE_NAME:
        raise BundleValidationError(
            f"run-manifest.json must stay outside the repository or under {DEFAULT_WORKSPACE_NAME}/"
        )


def default_benchmark_workspace(repo_root: Path) -> Path:
    """Return the ignored local workspace for generated benchmark artifacts."""
    return repo_root / DEFAULT_BENCHMARK_WORKSPACE


def _validate_corpus_rows(path: Path) -> dict[str, int]:
    corpus_text_lengths: dict[str, int] = {}
    for line_number, record in load_jsonl(path):
        label = f"{path.name}:{line_number}"
        ensure_mapping_fields(record, label, required={"corpus_id", "text", "title", "metadata"})
        corpus_id = require_string(record, "corpus_id", label)
        if corpus_id in corpus_text_lengths:
            raise BundleValidationError(f"{label}: duplicate corpus_id {corpus_id!r}")
        text = require_string(record, "text", label)
        corpus_text_lengths[corpus_id] = len(text)
        require_string(record, "title", label, allow_empty=True)
        metadata = require_mapping(record, "metadata", label)
        assert_public_safe_value(metadata, f"{label}.metadata")
    if not corpus_text_lengths:
        raise BundleValidationError("corpus.jsonl must contain at least one row")
    return corpus_text_lengths


def _validate_query_rows(path: Path) -> tuple[dict[str, frozenset[str]], dict[str, str]]:
    query_claim_ids: dict[str, frozenset[str]] = {}
    query_answerability: dict[str, str] = {}
    for line_number, record in load_jsonl(path):
        label = f"{path.name}:{line_number}"
        ensure_mapping_fields(
            record,
            label,
            required={
                "query_id",
                "query",
                "answerability",
                "label_source",
                "answers",
                "source_split",
                "evaluation_split",
                "tags",
            },
        )
        query_id = require_string(record, "query_id", label)
        if query_id in query_claim_ids:
            raise BundleValidationError(f"{label}: duplicate query_id {query_id!r}")
        require_string(record, "query", label)
        answerability = require_string(record, "answerability", label)
        if answerability not in ANSWERABILITY_VALUES:
            raise BundleValidationError(
                f"{label}: answerability must be one of {sorted(ANSWERABILITY_VALUES)}"
            )
        require_string(record, "label_source", label)
        require_string(record, "source_split", label)
        evaluation_split = require_string(record, "evaluation_split", label)
        if evaluation_split not in EVALUATION_SPLITS:
            raise BundleValidationError(
                f"{label}: evaluation_split must be one of {sorted(EVALUATION_SPLITS)}"
            )
        require_string_list(record.get("tags"), f"{label}.tags")
        query_claim_ids[query_id] = frozenset(parse_answers_claim_ids(record.get("answers"), label))
        query_answerability[query_id] = answerability
    return query_claim_ids, query_answerability


def _validate_qrel_rows(
    path: Path,
    *,
    corpus_ids: frozenset[str],
    query_ids: frozenset[str],
) -> list[QrelRef]:
    qrels: list[QrelRef] = []
    seen_pairs: set[tuple[str, str]] = set()
    for line_number, record in load_jsonl(path):
        label = f"{path.name}:{line_number}"
        ensure_mapping_fields(record, label, required={"query_id", "corpus_id", "relevance"})
        query_id = require_string(record, "query_id", label)
        corpus_id = require_string(record, "corpus_id", label)
        if query_id not in query_ids:
            raise BundleValidationError(f"{label}: qrel references unknown query_id {query_id!r}")
        if corpus_id not in corpus_ids:
            raise BundleValidationError(f"{label}: qrel references unknown corpus_id {corpus_id!r}")
        if (query_id, corpus_id) in seen_pairs:
            raise BundleValidationError(
                f"{label}: duplicate qrel for query_id={query_id!r} corpus_id={corpus_id!r}"
            )
        seen_pairs.add((query_id, corpus_id))
        relevance = require_number(record, "relevance", label)
        if relevance < 0:
            raise BundleValidationError(f"{label}: relevance must be non-negative")
        qrels.append(QrelRef(query_id=query_id, corpus_id=corpus_id, relevance=relevance))
    return qrels


def _validate_evidence_rows(
    path: Path,
    *,
    corpus_text_lengths: Mapping[str, int],
    query_claim_ids: Mapping[str, frozenset[str]],
) -> list[EvidenceRef]:
    evidence_refs: list[EvidenceRef] = []
    evidence_ids: set[str] = set()
    for line_number, record in load_jsonl(path):
        label = f"{path.name}:{line_number}"
        ensure_mapping_fields(
            record,
            label,
            required={
                "evidence_id",
                "query_id",
                "corpus_id",
                "locator",
                "required_group",
                "hop_index",
                "depends_on",
                "supports_claim_ids",
            },
        )
        evidence_id = require_string(record, "evidence_id", label)
        if evidence_id in evidence_ids:
            raise BundleValidationError(f"{label}: duplicate evidence_id {evidence_id!r}")
        evidence_ids.add(evidence_id)
        query_id = require_string(record, "query_id", label)
        corpus_id = require_string(record, "corpus_id", label)
        if query_id not in query_claim_ids:
            raise BundleValidationError(
                f"{label}: evidence references unknown query_id {query_id!r}"
            )
        if corpus_id not in corpus_text_lengths:
            raise BundleValidationError(
                f"{label}: evidence references unknown corpus_id {corpus_id!r}"
            )
        validate_locator(
            require_mapping(record, "locator", label),
            label,
            corpus_text_length=corpus_text_lengths[corpus_id],
        )
        require_string(record, "required_group", label)
        hop_index = require_int(record, "hop_index", label)
        if hop_index < 0:
            raise BundleValidationError(f"{label}: hop_index must be non-negative")
        depends_on = tuple(require_string_list(record.get("depends_on"), f"{label}.depends_on"))
        supported_claims = tuple(
            require_string_list(record.get("supports_claim_ids"), f"{label}.supports_claim_ids")
        )
        known_claims = query_claim_ids[query_id]
        for claim_id in supported_claims:
            if claim_id not in known_claims:
                raise BundleValidationError(
                    f"{label}: supports_claim_ids references unknown claim id {claim_id!r}"
                )
        evidence_refs.append(
            EvidenceRef(
                evidence_id=evidence_id,
                query_id=query_id,
                depends_on=depends_on,
                supports_claim_ids=supported_claims,
            )
        )
    return evidence_refs


def _validate_evidence_dependencies(evidence_refs: Sequence[EvidenceRef]) -> None:
    evidence_by_id = {ref.evidence_id: ref for ref in evidence_refs}
    for ref in evidence_refs:
        for dependency_id in ref.depends_on:
            if dependency_id == ref.evidence_id:
                raise BundleValidationError(
                    f"evidence.jsonl:{ref.evidence_id}: depends_on must not reference itself"
                )
            dependency = evidence_by_id.get(dependency_id)
            if dependency is None:
                raise BundleValidationError(
                    f"evidence.jsonl:{ref.evidence_id}: depends_on references unknown "
                    f"evidence_id {dependency_id!r}"
                )
            if dependency.query_id != ref.query_id:
                raise BundleValidationError(
                    f"evidence.jsonl:{ref.evidence_id}: depends_on references evidence "
                    "for a different query"
                )

    visit_state: dict[str, int] = {}

    def visit(ref: EvidenceRef) -> None:
        state = visit_state.get(ref.evidence_id, 0)
        if state == 1:
            raise BundleValidationError("evidence.jsonl: depends_on contains a cycle")
        if state == 2:
            return
        visit_state[ref.evidence_id] = 1
        for dependency_id in ref.depends_on:
            visit(evidence_by_id[dependency_id])
        visit_state[ref.evidence_id] = 2

    for ref in evidence_refs:
        visit(ref)


def _query_metric_eligibility(
    query_answerability: Mapping[str, str],
    qrels: Sequence[QrelRef],
) -> dict[str, QueryMetricEligibility]:
    qrels_by_query: dict[str, list[QrelRef]] = defaultdict(list)
    for qrel in qrels:
        qrels_by_query[qrel.query_id].append(qrel)

    eligibility: dict[str, QueryMetricEligibility] = {}
    for query_id, answerability in query_answerability.items():
        known_answerability = answerability != "unknown"
        eligibility[query_id] = QueryMetricEligibility(
            query_id=query_id,
            retrieval=any(qrel.relevance > 0 for qrel in qrels_by_query.get(query_id, [])),
            answerability=known_answerability,
            abstention=known_answerability,
            negative_final_answer_false_positive=answerability == "unanswerable",
        )
    return eligibility


def parse_answers_claim_ids(value: object, label: str) -> set[str]:
    if not isinstance(value, list):
        raise BundleValidationError(f"{label}.answers must be a list")
    claim_ids: set[str] = set()
    for index, item in enumerate(value):
        answer_label = f"{label}.answers[{index}]"
        if not isinstance(item, dict):
            raise BundleValidationError(f"{answer_label} must be an object")
        ensure_mapping_fields(
            item,
            answer_label,
            required={"answer", "aliases", "claim_ids"},
        )
        require_string(item, "answer", answer_label)
        require_string_list(item.get("aliases"), f"{answer_label}.aliases")
        for claim_id in require_string_list(item.get("claim_ids"), f"{answer_label}.claim_ids"):
            if claim_id in claim_ids:
                raise BundleValidationError(f"{answer_label}: duplicate claim id {claim_id!r}")
            claim_ids.add(claim_id)
    return claim_ids


def validate_locator(
    locator: Mapping[str, Any],
    label: str,
    *,
    corpus_text_length: int,
) -> None:
    ensure_mapping_fields(
        locator,
        f"{label}.locator",
        required={"granularity"},
        optional={"start", "end", "section", "paragraph", "passage"},
    )
    granularity = require_string(locator, "granularity", f"{label}.locator")
    if granularity not in LOCATOR_GRANULARITIES:
        raise BundleValidationError(
            f"{label}.locator.granularity must be one of {sorted(LOCATOR_GRANULARITIES)}"
        )
    start = locator.get("start")
    end = locator.get("end")
    if granularity in SPAN_GRANULARITIES:
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end <= start
        ):
            raise BundleValidationError(
                f"{label}.locator span granularity requires integer bounds start < end"
            )
        if granularity == "char_span" and end > corpus_text_length:
            raise BundleValidationError(f"{label}.locator char_span end exceeds corpus text length")
        return
    if start is not None or end is not None:
        raise BundleValidationError(f"{label}.locator bounds are only valid for span locators")
    if granularity == "section":
        require_string(locator, "section", f"{label}.locator")
    if granularity == "paragraph":
        paragraph = locator.get("paragraph")
        if (
            isinstance(paragraph, bool)
            or not isinstance(paragraph, str | int)
            or paragraph == ""
            or (isinstance(paragraph, int) and paragraph < 0)
        ):
            raise BundleValidationError(
                f"{label}.locator.paragraph must be a non-empty string or non-negative integer"
            )
    if granularity == "passage":
        require_string(locator, "passage", f"{label}.locator")


def validate_adapter_info(adapter: Mapping[str, Any]) -> None:
    ensure_mapping_fields(adapter, "provenance.json.adapter", required={"name", "version"})
    require_string(adapter, "name", "provenance.json.adapter")
    require_string(adapter, "version", "provenance.json.adapter")


def validate_checksums(checksums: Mapping[str, Any], *, bundle_dir: Path) -> None:
    missing = sorted(REQUIRED_CHECKSUMS - set(checksums))
    unknown = sorted(set(checksums) - REQUIRED_CHECKSUMS)
    if missing:
        raise BundleValidationError(f"provenance.json.checksums missing entries: {missing}")
    if unknown:
        raise BundleValidationError(f"provenance.json.checksums unknown entries: {unknown}")
    for file_name in BUNDLE_JSONL_FILES:
        expected = require_string(checksums, file_name, "provenance.json.checksums")
        if not re.fullmatch(r"sha256:[a-fA-F0-9]{64}", expected):
            raise BundleValidationError(
                f"provenance.json.checksums.{file_name} must be sha256:<64 hex chars>"
            )
        observed = "sha256:" + canonical_text_file_sha256(bundle_dir / file_name)
        if observed.lower() != expected.lower():
            raise BundleValidationError(f"provenance.json.checksums.{file_name} mismatch")


def validate_component_licenses(components: Sequence[JsonObject]) -> None:
    if not components:
        raise BundleValidationError("provenance.json.component_licenses must not be empty")
    for index, component in enumerate(components):
        label = f"provenance.json.component_licenses[{index}]"
        ensure_mapping_fields(
            component,
            label,
            required={
                "component",
                "license_spdx",
                "license_url",
                "attribution",
                "redistribution_policy",
                "public_report_policy",
            },
            optional={"license_verified_date", "verified_at"},
        )
        require_string(component, "component", label)
        require_string(component, "license_spdx", label)
        validate_public_url(require_string(component, "license_url", label), f"{label}.license_url")
        require_license_verification(component, label)
        require_string(component, "attribution", label)
        require_string(component, "redistribution_policy", label)
        require_string(component, "public_report_policy", label)


def require_license_verification(component: Mapping[str, Any], label: str) -> None:
    value = component.get("license_verified_date", component.get("verified_at"))
    if not isinstance(value, str) or not value:
        raise BundleValidationError(
            f"{label} must include license_verified_date or verified_at metadata"
        )
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T[0-9:._+-]+Z?)?", value):
        raise BundleValidationError(f"{label}: license verification timestamp must be ISO-like")


def validate_immutable_source_revision(value: str, label: str) -> None:
    normalized = value.strip().lower()
    if normalized in MUTABLE_SOURCE_REVISIONS or any(
        normalized.startswith(prefix) for prefix in MUTABLE_REF_PREFIXES
    ):
        raise BundleValidationError(f"{label} must be a resolved immutable revision")
    if re.fullmatch(r"(?:git:|sha1:)?[a-f0-9]{40}", normalized):
        return
    if re.fullmatch(r"sha256:[a-f0-9]{64}", normalized) or re.fullmatch(
        r"[a-f0-9]{64}", normalized
    ):
        return
    raise BundleValidationError(
        f"{label} must be a commit/content hash, not a branch, tag, release, or label"
    )


def is_unknown_license(value: str) -> bool:
    normalized = normalized_policy(value)
    return normalized in UNKNOWN_LICENSE_MARKERS or bool(
        UNKNOWN_LICENSE_TOKEN_PATTERN.search(value)
    )


def is_noncommercial_text(value: str) -> bool:
    normalized = normalized_policy(value)
    return any(marker in normalized for marker in NONCOMMERCIAL_MARKERS)


def is_unclear_policy(value: str) -> bool:
    return normalized_policy(value) in UNCLEAR_POLICY_MARKERS


def normalized_policy(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def load_jsonl(path: Path) -> list[tuple[int, JsonObject]]:
    rows: list[tuple[int, JsonObject]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as error:
            raise BundleValidationError(f"{path.name}:{line_number}: invalid JSONL") from error
        if not isinstance(parsed, dict):
            raise BundleValidationError(f"{path.name}:{line_number}: JSONL row must be an object")
        rows.append((line_number, parsed))
    return rows


def load_json_object(path: Path) -> JsonObject:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise BundleValidationError(f"{path.name}: invalid JSON") from error
    if not isinstance(parsed, dict):
        raise BundleValidationError(f"{path.name}: JSON document must be an object")
    return parsed


def canonical_text_bytes(raw: bytes) -> bytes:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BundleValidationError("benchmark bundle text files must be valid UTF-8") from error
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def canonical_text_file_sha256(path: Path) -> str:
    return hashlib.sha256(canonical_text_bytes(path.read_bytes())).hexdigest()


def ensure_mapping_fields(
    mapping: Mapping[str, Any],
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
        raise BundleValidationError(f"{label}: missing required fields: {missing}")
    if unknown:
        raise BundleValidationError(f"{label}: unknown fields are not public-safe: {unknown}")


def require_mapping(record: Mapping[str, Any], field: str, label: str) -> Mapping[str, Any]:
    value = record.get(field)
    if not isinstance(value, dict):
        raise BundleValidationError(f"{label}.{field} must be an object")
    return value


def require_object_list(value: object, label: str) -> list[JsonObject]:
    if not isinstance(value, list):
        raise BundleValidationError(f"{label} must be a list")
    objects: list[JsonObject] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise BundleValidationError(f"{label}[{index}] must be an object")
        objects.append(item)
    return objects


def require_string(
    record: Mapping[str, Any],
    field: str,
    label: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = record.get(field)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise BundleValidationError(f"{label}.{field} must be a non-empty string")
    return value


def require_string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise BundleValidationError(f"{label} must be a list of non-empty strings")
    return list(value)


def require_int(record: Mapping[str, Any], field: str, label: str) -> int:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise BundleValidationError(f"{label}.{field} must be an integer")
    return value


def require_number(record: Mapping[str, Any], field: str, label: str) -> float:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BundleValidationError(f"{label}.{field} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise BundleValidationError(f"{label}.{field} must be finite")
    return number


def validate_public_url(value: str, label: str) -> None:
    if not re.fullmatch(r"https?://[^\s\"']+", value, flags=re.IGNORECASE):
        raise BundleValidationError(f"{label} must be an http(s) URL")
    assert_public_safe_value(value, label)


def assert_public_safe_value(value: object, label: str) -> None:
    violations = scan_public_safety_violations(value)
    if violations:
        raise BundleValidationError(
            f"{label} contains local paths, private URLs, or sensitive values: "
            f"{', '.join(violations)}"
        )


def scan_public_safety_violations(value: object) -> list[str]:
    text = (
        value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    )
    return sorted({name for name, pattern in PRIVATE_PATTERNS if pattern.search(text)})


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate llmwiki-benchmark-bundle-v1 offline artifacts."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="validate schema and provenance")
    validate_parser.add_argument("--bundle-dir", type=Path, required=True)

    gate_parser = subparsers.add_parser("release-gate", help="evaluate public report/release gate")
    gate_parser.add_argument("--bundle-dir", type=Path, required=True)
    gate_parser.add_argument(
        "--mode",
        choices=("public-report", "bundle-release"),
        default="public-report",
    )

    manifest_parser = subparsers.add_parser(
        "validate-run-manifest",
        help="validate local-only run-manifest placement",
    )
    manifest_parser.add_argument("--path", type=Path, required=True)
    manifest_parser.add_argument("--repo-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "validate":
            result = validate_bundle(args.bundle_dir)
            print(json.dumps(result.as_json(), indent=2, sort_keys=True))
            return 0
        if args.command == "release-gate":
            mode = cast(GateMode, args.mode)
            gate = evaluate_public_release_gate(args.bundle_dir, mode=mode)
            print(json.dumps(gate.as_json(), indent=2, sort_keys=True))
            return 0 if gate.passed else 1
        if args.command == "validate-run-manifest":
            validate_local_run_manifest(args.path, repo_root=args.repo_root)
            print(json.dumps({"path": str(args.path), "local_only": True}, sort_keys=True))
            return 0
    except BundleValidationError as error:
        print(f"benchmark bundle validation failed: {error}", file=sys.stderr)
        return 2
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
