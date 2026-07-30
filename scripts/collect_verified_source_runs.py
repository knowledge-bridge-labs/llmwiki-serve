"""Collect verified-source benchmark runs through the actual service APIs.

This script produces evaluator-compatible ``runs.jsonl`` rows by exercising
``LlmWikiService.context()``, ``search()``, and ``read()`` against either an
explicit source root or a pinned upstream smoke-registry case.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from llmwiki_serve.managed_context import ManagedContextConfig  # noqa: E402
from llmwiki_serve.parser import split_frontmatter  # noqa: E402
from llmwiki_serve.service import LlmWikiService  # noqa: E402
from scripts import upstream_candidate_smoke as upstream_smoke  # noqa: E402
from scripts import verified_source_benchmark as benchmark  # noqa: E402

COLLECTION_SCHEMA = "llmwiki-serve-verified-source-run-collection-v1"
CASE_MANIFEST_SCHEMA = "llmwiki-serve-verified-source-collector-case-v1"
VERIFIED_SOURCE_CASE_MANIFEST_SCHEMA = "llmwiki-serve-verified-source-case-manifest-v1"
CASE_METADATA_FIELDS = (
    "case_id",
    "product",
    "official_link",
    "source_kind",
    "evidence_label",
    "pinned_commit",
    "license",
)
DEFAULT_PHASES = ("cold", "warm", "primed")
DEFAULT_VARIANTS = (
    "native",
    "native-managed-on",
    "generic-shadow-managed-off",
    "generic-shadow-managed-on",
)
GENERIC_SHADOW_VARIANTS = frozenset(
    {
        "generic-shadow-managed-off",
        "generic-shadow-managed-on",
    }
)
DETERMINISTIC_PUBLIC_PATH_CITATION_MODE = "deterministic-public-path-id"
SERVICE_CONTEXT_SURFACES = (
    "service-context",
    "service-context-orientation",
    "service-context-bundle",
)
SERVICE_SURFACES = (*SERVICE_CONTEXT_SURFACES, "service-search-read")
SEARCH_RESULT_FIELDS = (
    "page_id",
    "title",
    "score",
    "snippet",
    "role",
    "source_refs",
    "route",
)
READ_FIELDS = ("id", "title", "role", "text", "source_refs")
RUN_ID_PART_RE = re.compile(r"[^A-Za-z0-9]+")
SHADOW_SEGMENT_SAFE_RE = re.compile(r"[^A-Za-z0-9._ -]+")
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
ROOT_AUTHORED_ORIENTATION_HUBS = {
    "hot.md",
    "index.md",
    "overview.md",
    "quickstart.md",
    "readme.md",
}
ORIENTATION_HUB_ROLES = {"hot", "index", "overview"}
SCRATCH_SENTINEL = ".llmwiki-bench-owned"


class CollectorError(RuntimeError):
    """Raised when collection cannot safely produce public benchmark artifacts."""


class CountingTokenizer(Protocol):
    tokenizer_id: str
    tokenizer_revision: str

    def count_tokens(self, text: str) -> int:
        """Return a real token count for a serialized benchmark payload."""


@dataclass(frozen=True)
class SourceResolution:
    root: Path
    checkout_root: Path | None
    source_report: dict[str, object]
    case_metadata: dict[str, object] | None


@dataclass(frozen=True)
class CitationEvidencePolicy:
    mode: str
    deterministic_public_path_ids: bool
    declared_citation_mode: str | None
    policy: str

    def as_report(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "declared_citation_mode": self.declared_citation_mode,
            "policy": self.policy,
        }


@dataclass(frozen=True)
class CaseManifest:
    path: Path
    payload: dict[str, object] | None
    public_metadata: dict[str, object] | None = None

    @property
    def base_dir(self) -> Path:
        return self.path.parent

    @property
    def is_legacy_corpus_manifest(self) -> bool:
        return self.payload is None


@dataclass(frozen=True)
class BenchmarkInputPaths:
    corpus: Path
    queries: Path
    qrels: Path
    public_case_manifest: dict[str, object]


@dataclass(frozen=True)
class BenchmarkInputs:
    corpus: dict[str, benchmark.CorpusRow]
    queries: dict[str, benchmark.QueryRow]
    qrels_by_query: dict[str, list[benchmark.QrelRow]]

    @property
    def qrel_count(self) -> int:
        return sum(len(rows) for rows in self.qrels_by_query.values())


@dataclass(frozen=True)
class Variant:
    id: str
    root: Path
    managed_context: ManagedContextConfig | bool
    materialization: dict[str, object]


@dataclass(frozen=True)
class SurfaceResult:
    rows: list[dict[str, object]]
    summary: dict[str, object]


@dataclass(frozen=True)
class FixtureTokenizer:
    tokenizer_id: str
    tokenizer_revision: str

    def count_tokens(self, text: str) -> int:
        benchmark.validate_tokenizer_provenance(self.tokenizer_id, self.tokenizer_revision)
        return len(re.findall(r"\S+", text))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect verified-source benchmark runs through llmwiki-serve's actual "
            "context/search/read service APIs."
        )
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source-root", type=Path, help="Explicit source root to benchmark.")
    source.add_argument(
        "--upstream-case",
        help=(
            "Pinned case id or alias from scripts/upstream_candidate_smoke.py. "
            "The case is cloned into --checkout-cache if needed."
        ),
    )
    parser.add_argument("--checkout-cache", type=Path, help="Cache root for upstream checkouts.")
    parser.add_argument(
        "--case-manifest",
        type=Path,
        required=True,
        help=(
            "Collector case manifest JSON. Legacy callers may still pass the corpus "
            "JSONL here with explicit --queries and --qrels."
        ),
    )
    parser.add_argument("--corpus", type=Path, help="Override corpus JSONL from case manifest.")
    parser.add_argument("--queries", type=Path, help="Override benchmark queries JSONL.")
    parser.add_argument("--qrels", type=Path, help="Override benchmark qrels JSONL.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tokenizer-id", required=True)
    parser.add_argument("--tokenizer-revision", required=True)
    parser.add_argument(
        "--hardware-bucket",
        choices=sorted(benchmark.ALLOWED_HARDWARE_BUCKETS),
        help="When set, also run the verified benchmark evaluator and write report.json.",
    )
    parser.add_argument("--seed", type=int, default=20260730)
    parser.add_argument("--bootstrap-samples", type=positive_int, default=1000)
    parser.add_argument("--baseline-run-id")
    parser.add_argument(
        "--verify-tokenizer",
        action="store_true",
        help=(
            "Load the configured Qwen tokenizer with transformers. Required for "
            "non-dry-run collection unless --allow-fixture-tokenizer is used in tests."
        ),
    )
    parser.add_argument(
        "--allow-fixture-tokenizer",
        action="store_true",
        help=(
            "Use a deterministic test-only tokenizer. This is not public quality evidence "
            "and never uses a byte-length proxy."
        ),
    )
    parser.add_argument(
        "--variant",
        action="append",
        choices=DEFAULT_VARIANTS,
        help="Variant to collect. Repeat to select several. Defaults to all variants.",
    )
    parser.add_argument(
        "--phase",
        action="append",
        choices=DEFAULT_PHASES,
        help="Phase to collect. Repeat to select several. Defaults to cold, warm, primed.",
    )
    parser.add_argument("--limit", type=positive_int, default=5)
    parser.add_argument("--scratch-dir", type=Path)
    parser.add_argument("--keep-scratch", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs without running.")
    parser.add_argument("--timeout", type=positive_int, default=120, help="Git timeout seconds.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        case_manifest = load_case_manifest(args.case_manifest)
        citation_policy = citation_policy_from_case_manifest(case_manifest)
        source = resolve_source(args, case_manifest)
        benchmark.validate_output_not_inside_source_root(args.output_dir, source.root)
        input_paths = resolve_input_paths(args, case_manifest, source)
        inputs = load_inputs(
            input_paths.corpus,
            input_paths.queries,
            input_paths.qrels,
            source.root,
        )
        output_dir = args.output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        if args.dry_run:
            report = build_collection_report(
                mode="dry-run",
                source=source,
                inputs=inputs,
                tokenizer=None,
                variant_summaries=[],
                run_summaries=[],
                output_files=[],
                source_digest=None,
                citation_policy=citation_policy,
            )
            write_json(output_dir / "collection-report.json", report)
            print(f"verified source run collection dry-run report written: {output_dir}")
            return 0

        validate_tokenizer_args_match_manifest(args, case_manifest)
        tokenizer = load_counting_tokenizer(args)
        source_before = benchmark.compute_tree_digest(source.root)
        all_rows: list[dict[str, object]] = []
        run_summaries: list[dict[str, object]] = []
        selected_variants = unique_cli_values(
            args.variant or DEFAULT_VARIANTS,
            option="--variant",
        )
        selected_phases = unique_cli_values(
            args.phase or DEFAULT_PHASES,
            option="--phase",
        )

        with materialization_stack(
            args,
            source.root,
            inputs,
            selected_variant_ids=selected_variants,
        ) as variants:
            variant_lookup = {variant.id: variant for variant in variants}
            variant_summaries = [
                variant_summary(variant_lookup[variant_id]) for variant_id in selected_variants
            ]
            for variant_id in selected_variants:
                variant = variant_lookup[variant_id]
                for phase in selected_phases:
                    for surface in SERVICE_SURFACES:
                        result = collect_surface(
                            variant,
                            phase=phase,
                            surface=surface,
                            queries=inputs.queries,
                            corpus=inputs.corpus,
                            tokenizer=tokenizer,
                            limit=args.limit,
                            citation_policy=citation_policy,
                        )
                        all_rows.extend(result.rows)
                        run_summaries.append(result.summary)

            source_after = benchmark.compute_tree_digest(source.root)
            source_digest = benchmark.SourceMutationDigest(
                before_sha256=source_before,
                after_sha256=source_after,
                mutated=source_before != source_after,
            )
            if source_digest.mutated:
                raise CollectorError("source tree mutated during run collection")

            validate_run_rows_before_publish(
                all_rows,
                corpus=inputs.corpus,
                queries=inputs.queries,
                tokenizer=tokenizer,
            )
            copy_inputs(input_paths, output_dir)
            write_jsonl(output_dir / "runs.jsonl", all_rows)
            output_files = [
                "case-manifest.json",
                "corpus.jsonl",
                "queries.jsonl",
                "qrels.jsonl",
                "runs.jsonl",
            ]
            if args.hardware_bucket is not None:
                benchmark_report = benchmark.run_benchmark(
                    output_dir,
                    hardware_bucket=args.hardware_bucket,
                    tokenizer=tokenizer_provenance(tokenizer),
                    seed=args.seed,
                    bootstrap_samples=args.bootstrap_samples,
                    baseline_run_id=args.baseline_run_id,
                    source_root=source.root,
                )
                benchmark.write_report(benchmark_report, output_dir / "report.json")
                output_files.append("report.json")
            report = build_collection_report(
                mode="collect",
                source=source,
                inputs=inputs,
                tokenizer=tokenizer,
                variant_summaries=variant_summaries,
                run_summaries=run_summaries,
                output_files=output_files,
                source_digest=source_digest,
                citation_policy=citation_policy,
            )
            write_json(output_dir / "collection-report.json", report)

        print(f"verified source runs written: {output_dir / 'runs.jsonl'}")
        return 0
    except (
        CollectorError,
        benchmark.BenchmarkValidationError,
        upstream_smoke.SmokeFailure,
        ValueError,
    ) as error:
        print(f"verified source run collection failed: {error}", file=sys.stderr)
        return 2


def resolve_source(args: argparse.Namespace, case_manifest: CaseManifest) -> SourceResolution:
    manifest_metadata = case_metadata_from_manifest(case_manifest)
    if args.source_root is not None:
        root = args.source_root.expanduser().resolve()
        if not root.is_dir():
            raise CollectorError("source_root must exist and be a directory")
        source_report = {
            "mode": "explicit-source-root",
            "source_kind": "operator-provided",
        }
        case_metadata = manifest_metadata
        if case_metadata is not None:
            source_report = {"mode": "explicit-source-root", **case_metadata}
        return SourceResolution(
            root=root,
            checkout_root=None,
            source_report=source_report,
            case_metadata=case_metadata,
        )

    if args.upstream_case is None:
        manifest_source_root = manifest_relative_path(case_manifest, "source_root", required=False)
        if manifest_source_root is None:
            raise CollectorError(
                "one of --source-root, --upstream-case, or case_manifest.source_root is required"
            )
        if not manifest_source_root.is_dir():
            raise CollectorError("case_manifest.source_root must exist and be a directory")
        case_metadata = manifest_metadata
        source_report = {"mode": "case-manifest-source-root"}
        if case_metadata is not None:
            source_report = {**source_report, **case_metadata}
        return SourceResolution(
            root=manifest_source_root,
            checkout_root=None,
            source_report=source_report,
            case_metadata=case_metadata,
        )

    case = select_upstream_case(str(args.upstream_case))
    validate_upstream_case_manifest(case_manifest, case, manifest_metadata)
    checkout_cache = args.checkout_cache or ROOT / ".llmwiki-work" / "verified-source-checkouts"
    checkout_dir = checkout_cache.expanduser().resolve() / case.id / case.ref
    if not checkout_dir.exists():
        checkout_dir.parent.mkdir(parents=True, exist_ok=True)
        upstream_smoke.checkout_case(case, checkout_dir, timeout=args.timeout)
    upstream_smoke.require_clean_checkout(case, checkout_dir, timeout=args.timeout)
    validate_checkout_commit(case, checkout_dir, timeout=args.timeout)
    manifest_source_path = case_manifest_source_path(case_manifest)
    effective_source_path = manifest_source_path or case.source_path
    source_root = checkout_source_root(
        checkout_dir,
        effective_source_path,
        label="case_manifest.source_path"
        if manifest_source_path is not None
        else "upstream_case.source_path",
    )
    if not source_root.is_dir():
        raise CollectorError("upstream case source path is not a directory")
    case_metadata = manifest_metadata or case_metadata_from_upstream_case(case)
    return SourceResolution(
        root=source_root,
        checkout_root=checkout_dir,
        source_report={"mode": "upstream-smoke-registry", **case_metadata},
        case_metadata=case_metadata,
    )


def select_upstream_case(case_id: str) -> upstream_smoke.UpstreamSmokeCase:
    lookup = upstream_smoke.case_lookup(upstream_smoke.CASES)
    case = lookup.get(case_id)
    if case is None:
        known = ", ".join(sorted(lookup))
        raise CollectorError(f"unknown upstream case {case_id!r}; known cases: {known}")
    upstream_smoke.validate_case_refs((case,))
    upstream_smoke.validate_case_metadata((case,))
    return case


def validate_upstream_case_manifest(
    case_manifest: CaseManifest,
    case: upstream_smoke.UpstreamSmokeCase,
    manifest_metadata: Mapping[str, object] | None,
) -> None:
    if manifest_metadata is None:
        return
    manifest_case_id = str(manifest_metadata["case_id"])
    if manifest_case_id != case.id:
        raise CollectorError(
            "case_manifest.case_id does not match selected upstream case: "
            f"{manifest_case_id!r} != {case.id!r}"
        )
    manifest_commit = str(manifest_metadata["pinned_commit"])
    if manifest_commit != case.ref:
        raise CollectorError(
            "case_manifest.pinned_commit does not match selected upstream case commit: "
            f"{manifest_commit!r} != {case.ref!r}"
        )


def validate_checkout_commit(
    case: upstream_smoke.UpstreamSmokeCase,
    checkout_dir: Path,
    *,
    timeout: int,
) -> None:
    head = upstream_smoke.run_command(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout_dir,
        timeout=timeout,
    ).stdout.strip()
    if head != case.ref:
        raise CollectorError(
            f"{case.id}: checkout HEAD does not match expected commit: {head!r} != {case.ref!r}"
        )


def checkout_source_root(checkout_dir: Path, source_path: str, *, label: str) -> Path:
    validate_checkout_relative_source_path(source_path, label)
    return upstream_smoke.case_source_root(
        checkout_dir, normalize_checkout_source_path(source_path)
    )


def validate_checkout_relative_source_path(value: str, label: str) -> None:
    if not value:
        raise CollectorError(f"{label} must be a non-empty relative path")
    if "\\" in value or ":" in value or value.startswith("/"):
        raise CollectorError(f"{label} must be a safe relative POSIX path")
    if value == ".":
        return
    stripped = value.rstrip("/")
    if not stripped:
        raise CollectorError(f"{label} must be a non-empty relative path")
    parts = stripped.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise CollectorError(f"{label} must not contain empty, dot, or dot-dot parts")


def normalize_checkout_source_path(value: str) -> str:
    if value == ".":
        return value
    return value.rstrip("/")


def load_case_manifest(path: Path) -> CaseManifest:
    manifest_path = path.expanduser().resolve()
    if not manifest_path.is_file():
        raise CollectorError("case_manifest must exist and be a file")
    text = manifest_path.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return CaseManifest(path=manifest_path, payload=None)
    if not isinstance(payload, dict):
        raise CollectorError("case_manifest JSON must be an object")
    if "inputs" not in payload:
        if "doc_id" in payload:
            return CaseManifest(path=manifest_path, payload=None)
        schema = payload.get("schema")
        if schema != VERIFIED_SOURCE_CASE_MANIFEST_SCHEMA:
            raise CollectorError(
                "case_manifest must include inputs.corpus, inputs.queries, inputs.qrels"
            )
        public_metadata = public_case_metadata_from_mapping(
            manifest_metadata_mapping(payload),
            label="verified-source-case-manifest",
            required=True,
        )
        return CaseManifest(path=manifest_path, payload=payload, public_metadata=public_metadata)
    schema = payload.get("schema")
    if schema != CASE_MANIFEST_SCHEMA:
        raise CollectorError(
            f"case_manifest.schema must be {CASE_MANIFEST_SCHEMA!r}, got {schema!r}"
        )
    public_metadata = public_case_metadata_from_mapping(
        manifest_metadata_mapping(payload),
        label="case-manifest",
        required=True,
    )
    return CaseManifest(path=manifest_path, payload=payload, public_metadata=public_metadata)


def resolve_input_paths(
    args: argparse.Namespace,
    case_manifest: CaseManifest,
    source: SourceResolution,
) -> BenchmarkInputPaths:
    corpus = args.corpus
    queries = args.queries
    qrels = args.qrels
    if case_manifest.is_legacy_corpus_manifest:
        corpus = corpus or case_manifest.path
        if queries is None or qrels is None:
            raise CollectorError(
                "legacy corpus JSONL passed to --case-manifest requires explicit "
                "--queries and --qrels"
            )
    else:
        corpus = corpus or manifest_input_path(case_manifest, "corpus")
        queries = queries or manifest_input_path(case_manifest, "queries")
        qrels = qrels or manifest_input_path(case_manifest, "qrels")

    assert corpus is not None
    assert queries is not None
    assert qrels is not None
    return BenchmarkInputPaths(
        corpus=corpus.expanduser().resolve(),
        queries=queries.expanduser().resolve(),
        qrels=qrels.expanduser().resolve(),
        public_case_manifest=public_case_manifest(source_public_case_metadata(source)),
    )


def manifest_input_path(case_manifest: CaseManifest, field: str) -> Path:
    payload = require_case_manifest_payload(case_manifest)
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise CollectorError("case_manifest.inputs must be an object")
    raw_path = inputs.get(field)
    if not isinstance(raw_path, str) or not raw_path:
        raise CollectorError(f"case_manifest.inputs.{field} must be a non-empty string")
    benchmark.validate_public_relative_path(raw_path, f"{case_manifest.path.name}:inputs.{field}")
    return (case_manifest.base_dir / raw_path).resolve()


def manifest_relative_path(
    case_manifest: CaseManifest,
    field: str,
    *,
    required: bool,
) -> Path | None:
    if case_manifest.is_legacy_corpus_manifest:
        if required:
            raise CollectorError(f"legacy corpus manifest does not include {field}")
        return None
    payload = require_case_manifest_payload(case_manifest)
    raw_path = payload.get(field)
    if raw_path is None:
        source = payload.get("source")
        if isinstance(source, dict):
            raw_path = source.get(field)
    if raw_path is None:
        if required:
            raise CollectorError(f"case_manifest.{field} is required")
        return None
    if not isinstance(raw_path, str) or not raw_path:
        raise CollectorError(f"case_manifest.{field} must be a non-empty string")
    benchmark.validate_public_relative_path(raw_path, f"{case_manifest.path.name}:{field}")
    return (case_manifest.base_dir / raw_path).resolve()


def case_manifest_source_path(case_manifest: CaseManifest) -> str | None:
    if case_manifest.payload is None:
        return None
    raw_path = case_manifest.payload.get("source_path")
    if raw_path is None:
        source = case_manifest.payload.get("source")
        if isinstance(source, dict):
            raw_path = source.get("source_path")
    if raw_path is None:
        return None
    if not isinstance(raw_path, str) or not raw_path:
        raise CollectorError("case_manifest.source_path must be a non-empty string")
    validate_checkout_relative_source_path(raw_path, "case_manifest.source_path")
    return raw_path


def require_case_manifest_payload(case_manifest: CaseManifest) -> dict[str, object]:
    if case_manifest.payload is None:
        raise CollectorError("legacy corpus manifest does not include JSON case metadata")
    return case_manifest.payload


def citation_policy_from_case_manifest(case_manifest: CaseManifest) -> CitationEvidencePolicy:
    citation_mode = case_manifest_citation_mode(case_manifest)
    if citation_mode == DETERMINISTIC_PUBLIC_PATH_CITATION_MODE:
        return CitationEvidencePolicy(
            mode="path-derived-deterministic",
            deterministic_public_path_ids=True,
            declared_citation_mode=citation_mode,
            policy=(
                "When service source_refs are empty, citation_ids are derived from the "
                "returned doc_id's corpus.source_ref_ids, which are deterministic public "
                "path IDs. Authored service source_refs remain authoritative and are not "
                "mixed with derived IDs."
            ),
        )
    if citation_mode is not None:
        raise CollectorError(
            "case_manifest citation_mode must be one of "
            f"{[DETERMINISTIC_PUBLIC_PATH_CITATION_MODE]!r}"
        )
    return CitationEvidencePolicy(
        mode="authored-service-source-refs",
        deterministic_public_path_ids=False,
        declared_citation_mode=citation_mode,
        policy=(
            "citation_ids are collected only from authored service/read source_refs; "
            "empty service source_refs stay empty."
        ),
    )


def case_manifest_citation_mode(case_manifest: CaseManifest) -> str | None:
    if case_manifest.payload is None:
        return None
    source_ref_behavior = case_manifest.payload.get("source_ref_behavior")
    raw_mode: object | None = None
    if isinstance(source_ref_behavior, dict):
        raw_mode = source_ref_behavior.get("citation_mode")
    if raw_mode is None:
        raw_mode = case_manifest.payload.get("citation_mode")
    if raw_mode is None:
        return None
    if not isinstance(raw_mode, str) or not raw_mode:
        raise CollectorError("case_manifest citation_mode must be a non-empty string")
    return raw_mode


def case_metadata_from_manifest(case_manifest: CaseManifest) -> dict[str, object] | None:
    if case_manifest.payload is None:
        return None
    assert case_manifest.public_metadata is not None
    return case_manifest.public_metadata


def manifest_metadata_mapping(payload: Mapping[str, object]) -> Mapping[str, object]:
    case = payload.get("case")
    return case if isinstance(case, dict) else payload


def public_case_metadata_from_mapping(
    mapping: Mapping[str, object],
    *,
    label: str,
    required: bool,
) -> dict[str, object]:
    aliases = {
        "case_id": ("case_id",),
        "product": ("product",),
        "official_link": ("official_link", "official_url"),
        "source_kind": ("source_kind",),
        "evidence_label": ("evidence_label", "evidence_type"),
        "pinned_commit": ("pinned_commit", "commit"),
        "license": ("license", "license_evidence"),
    }
    metadata: dict[str, object] = {}
    for field in CASE_METADATA_FIELDS:
        value = next(
            (
                mapping.get(alias)
                for alias in aliases[field]
                if isinstance(mapping.get(alias), str) and mapping.get(alias)
            ),
            None,
        )
        if value is None:
            if required:
                raise CollectorError(f"{label}.{field} must be a non-empty string")
            value = "operator-provided"
        metadata[field] = value
    benchmark.assert_public_safe_value(metadata, label)
    return metadata


def case_metadata_from_upstream_case(case: upstream_smoke.UpstreamSmokeCase) -> dict[str, object]:
    return public_case_metadata_from_mapping(
        {
            "case_id": case.id,
            "product": case.product,
            "official_link": case.official_link,
            "source_kind": case.source_kind,
            "evidence_label": case.evidence_type,
            "pinned_commit": case.ref,
            "license": case.license_evidence,
        },
        label=f"upstream-case:{case.id}",
        required=True,
    )


def source_public_case_metadata(source: SourceResolution) -> dict[str, object]:
    if source.case_metadata is not None:
        return source.case_metadata
    return public_case_metadata_from_mapping(
        source.source_report,
        label="source",
        required=False,
    )


def public_case_manifest(case_metadata: Mapping[str, object]) -> dict[str, object]:
    manifest = {
        "schema": CASE_MANIFEST_SCHEMA,
        **{field: case_metadata[field] for field in CASE_METADATA_FIELDS},
        "inputs": {
            "corpus": "corpus.jsonl",
            "queries": "queries.jsonl",
            "qrels": "qrels.jsonl",
        },
    }
    benchmark.assert_public_safe_value(manifest, "case-manifest")
    return manifest


def load_inputs(
    corpus_path: Path,
    queries_path: Path,
    qrels_path: Path,
    source_root: Path,
) -> BenchmarkInputs:
    corpus = benchmark.load_corpus(corpus_path)
    queries = benchmark.load_queries(queries_path)
    qrels_by_query = benchmark.load_qrels(qrels_path, corpus, queries)
    benchmark.validate_every_query_has_qrel(queries, qrels_by_query)
    benchmark.validate_corpus_source_hashes(corpus, source_root)
    return BenchmarkInputs(corpus=corpus, queries=queries, qrels_by_query=qrels_by_query)


def load_counting_tokenizer(args: argparse.Namespace) -> CountingTokenizer:
    benchmark.validate_tokenizer_provenance(args.tokenizer_id, args.tokenizer_revision)
    if args.allow_fixture_tokenizer:
        if args.verify_tokenizer:
            raise CollectorError(
                "--allow-fixture-tokenizer and --verify-tokenizer are mutually exclusive"
            )
        if "fixture" not in args.tokenizer_id.lower() or "fixture" not in (
            args.tokenizer_revision.lower()
        ):
            raise CollectorError(
                "--allow-fixture-tokenizer requires fixture-labeled tokenizer provenance"
            )
        return FixtureTokenizer(args.tokenizer_id, args.tokenizer_revision)
    if not args.verify_tokenizer:
        raise CollectorError(
            "collection requires --verify-tokenizer so transformers can load the Qwen "
            "tokenizer; use `uv run --with transformers ...`. Tests may use "
            "--allow-fixture-tokenizer."
        )
    return benchmark.HuggingFaceQwenTokenizerAdapter.load(
        args.tokenizer_id,
        args.tokenizer_revision,
    )


def validate_tokenizer_args_match_manifest(
    args: argparse.Namespace, case_manifest: CaseManifest
) -> None:
    payload = case_manifest.payload
    if payload is None:
        return
    tokenizer = payload.get("tokenizer")
    if tokenizer is None:
        return
    if not isinstance(tokenizer, Mapping):
        raise CollectorError("case_manifest.tokenizer must be an object")
    manifest_id = tokenizer.get("id")
    manifest_revision = tokenizer.get("revision")
    if isinstance(manifest_id, str) and manifest_id != args.tokenizer_id:
        raise CollectorError("CLI tokenizer id does not match case_manifest.tokenizer.id")
    if isinstance(manifest_revision, str) and manifest_revision != args.tokenizer_revision:
        raise CollectorError(
            "CLI tokenizer revision does not match case_manifest.tokenizer.revision"
        )


def unique_cli_values(values: Sequence[str], *, option: str) -> tuple[str, ...]:
    selected: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise CollectorError(f"duplicate {option} value: {value}")
        seen.add(value)
        selected.append(value)
    return tuple(selected)


def validate_run_rows_before_publish(
    rows: Sequence[Mapping[str, object]],
    *,
    corpus: Mapping[str, benchmark.CorpusRow],
    queries: Mapping[str, benchmark.QueryRow],
    tokenizer: CountingTokenizer,
) -> None:
    with tempfile.TemporaryDirectory(prefix="llmwiki-runs-validate-") as temp_dir:
        run_path = Path(temp_dir) / "runs.jsonl"
        write_jsonl(run_path, rows)
        benchmark.load_runs(
            run_path,
            corpus,
            queries,
            tokenizer_provenance(tokenizer),
        )


def tokenizer_provenance(tokenizer: CountingTokenizer) -> benchmark.TokenizerProvenance:
    if isinstance(tokenizer, FixtureTokenizer):
        return benchmark.TokenizerProvenance(
            tokenizer.tokenizer_id,
            tokenizer.tokenizer_revision,
            evidence_level=benchmark.TOKENIZER_EVIDENCE_DECLARED,
            verified_by_harness=False,
        )
    return benchmark.TokenizerProvenance(
        tokenizer.tokenizer_id,
        tokenizer.tokenizer_revision,
        evidence_level=benchmark.TOKENIZER_EVIDENCE_LOAD_VERIFIED,
        verified_by_harness=True,
    )


@contextmanager
def materialization_stack(
    args: argparse.Namespace,
    source_root: Path,
    inputs: BenchmarkInputs,
    *,
    selected_variant_ids: Sequence[str],
) -> Iterator[tuple[Variant, ...]]:
    with ExitStack() as stack:
        scratch_parent = scratch_parent_context(args, stack, source_root=source_root)
        selected = set(selected_variant_ids)
        generic_shadow: GenericShadowMaterialization | None = None
        if selected & GENERIC_SHADOW_VARIANTS:
            generic_shadow = materialize_generic_shadow(source_root, inputs, scratch_parent)

        variants: list[Variant] = []
        if "native" in selected:
            variants.append(
                Variant(
                    id="native",
                    root=source_root,
                    managed_context=False,
                    materialization={"kind": "native-source", "managed_context_requested": False},
                )
            )
        if "native-managed-on" in selected:
            variants.append(
                Variant(
                    id="native-managed-on",
                    root=source_root,
                    managed_context=ManagedContextConfig(
                        enabled=True,
                        state_dir=scratch_parent / "native-managed-context-state",
                        namespace="verified-source-native-noop",
                        namespace_secret="verified-source-collector-test-secret",
                        lexical_tie_band=0.05,
                    ),
                    materialization={
                        "kind": "native-source",
                        "managed_context_requested": True,
                        "managed_context_expectation": (
                            "no-op for non-generic adapters or authored orientation hubs"
                        ),
                    },
                )
            )
        if "generic-shadow-managed-off" in selected:
            assert generic_shadow is not None
            variants.append(
                Variant(
                    id="generic-shadow-managed-off",
                    root=generic_shadow.root,
                    managed_context=False,
                    materialization={
                        "kind": "generic-shadow",
                        "adapter_target": "generic-markdown",
                        "managed_context_requested": False,
                        "page_id_mapping": "manifest-doc-id-preserved-in-scratch-frontmatter",
                        "authored_orientation_hubs": "not materialized at scratch root",
                        **generic_shadow.report,
                    },
                )
            )
        if "generic-shadow-managed-on" in selected:
            assert generic_shadow is not None
            variants.append(
                Variant(
                    id="generic-shadow-managed-on",
                    root=generic_shadow.root,
                    managed_context=ManagedContextConfig(
                        enabled=True,
                        state_dir=scratch_parent / "managed-context-state",
                        namespace="verified-source-collector",
                        namespace_secret="verified-source-collector-test-secret",
                        lexical_tie_band=0.05,
                    ),
                    materialization={
                        "kind": "generic-shadow",
                        "adapter_target": "generic-markdown",
                        "managed_context_requested": True,
                        "page_id_mapping": "manifest-doc-id-preserved-in-scratch-frontmatter",
                        "authored_orientation_hubs": "not materialized at scratch root",
                        **generic_shadow.report,
                    },
                )
            )
        yield tuple(variants)


def scratch_parent_context(
    args: argparse.Namespace,
    stack: ExitStack,
    *,
    source_root: Path,
) -> Path:
    if args.scratch_dir is None:
        return Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="llmwiki-bench-")))
    requested_parent = args.scratch_dir.expanduser().resolve()
    resolved_source = source_root.resolve()
    if requested_parent == resolved_source:
        raise CollectorError("--scratch-dir must not be the source root")
    try:
        requested_parent.relative_to(resolved_source)
    except ValueError:
        pass
    else:
        raise CollectorError("--scratch-dir must not be inside the source root")
    requested_parent.mkdir(parents=True, exist_ok=True)
    scratch_parent = Path(tempfile.mkdtemp(prefix="collector-owned-", dir=requested_parent))
    (scratch_parent / SCRATCH_SENTINEL).write_text(
        "owned by collect_verified_source_runs.py\n",
        encoding="utf-8",
    )
    if not args.keep_scratch:
        stack.callback(remove_owned_scratch, scratch_parent)
    return scratch_parent


def remove_owned_scratch(path: Path) -> None:
    if path.exists() and (path / SCRATCH_SENTINEL).is_file():
        shutil.rmtree(path, ignore_errors=True)


@dataclass(frozen=True)
class GenericShadowMaterialization:
    root: Path
    report: dict[str, object]


def materialize_generic_shadow(
    source_root: Path,
    inputs: BenchmarkInputs,
    scratch_parent: Path,
) -> GenericShadowMaterialization:
    shadow_root = scratch_parent / "generic-shadow"
    if shadow_root.exists():
        shutil.rmtree(shadow_root)
    pages_root = shadow_root / "pages"
    pages_root.mkdir(parents=True)
    resolved_source = source_root.resolve()
    skipped_hubs: list[dict[str, object]] = []
    materialized_doc_ids: set[str] = set()
    materialized_paths: set[Path] = set()
    positive_qrel_doc_ids = {
        qrel.doc_id
        for qrels in inputs.qrels_by_query.values()
        for qrel in qrels
        if qrel.relevance > 0
    }
    for row in sorted(inputs.corpus.values(), key=lambda item: item.doc_id):
        source_path = (resolved_source / row.path).resolve()
        try:
            source_path.relative_to(resolved_source)
        except ValueError as error:
            raise CollectorError(f"corpus row escapes source_root: {row.doc_id}") from error
        if is_authored_orientation_hub(row):
            if row.doc_id in positive_qrel_doc_ids:
                raise CollectorError(
                    "generic-shadow cannot remove authored hub doc_id "
                    f"{row.doc_id!r} because a positive qrel depends on it"
                )
            skipped_hubs.append(
                {
                    "doc_id": row.doc_id,
                    "path": row.path,
                    "role": row.role,
                }
            )
            continue
        raw = source_path.read_text(encoding="utf-8")
        _frontmatter, body = split_frontmatter(raw)
        target_path = pages_root / doc_id_to_shadow_path(row.doc_id)
        if target_path in materialized_paths:
            raise CollectorError(f"generic-shadow path collision for doc_id {row.doc_id!r}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        frontmatter = {
            "id": row.doc_id,
            "title": row.title,
            "source_refs": list(row.source_ref_ids),
            "review_state": "approved",
        }
        target_path.write_text(
            "---\n"
            + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False)
            + "---\n"
            + body.lstrip(),
            encoding="utf-8",
        )
        materialized_doc_ids.add(row.doc_id)
        materialized_paths.add(target_path)
    return GenericShadowMaterialization(
        root=shadow_root,
        report={
            "shadow_page_count": len(materialized_doc_ids),
            "skipped_authored_orientation_hub_count": len(skipped_hubs),
            "skipped_authored_orientation_hubs": skipped_hubs,
        },
    )


def is_authored_orientation_hub(row: benchmark.CorpusRow) -> bool:
    role = row.role.lower()
    if role in ORIENTATION_HUB_ROLES:
        return True
    relative_parts = Path(row.path).parts
    return len(relative_parts) == 1 and relative_parts[0].lower() in ROOT_AUTHORED_ORIENTATION_HUBS


def doc_id_to_shadow_path(doc_id: str) -> Path:
    if not doc_id or doc_id.startswith(("/", "\\")) or "\\" in doc_id or ":" in doc_id:
        raise CollectorError(f"doc_id cannot be materialized safely: {doc_id!r}")
    parts = doc_id.split("/")
    if not parts or any(not part or part in {".", ".."} for part in parts):
        raise CollectorError(f"doc_id cannot be materialized safely: {doc_id!r}")
    safe_parts = [safe_shadow_path_segment(part) for part in parts]
    return Path(*safe_parts[:-1], f"{safe_parts[-1]}.md")


def safe_shadow_path_segment(segment: str) -> str:
    safe = SHADOW_SEGMENT_SAFE_RE.sub("_", segment).strip(" .")
    base_name = safe.split(".", 1)[0].lower()
    changed = safe != segment or not safe or base_name in WINDOWS_RESERVED_NAMES
    if changed:
        digest = hashlib.sha256(segment.encode("utf-8")).hexdigest()[:8]
        if safe and base_name in WINDOWS_RESERVED_NAMES and "." in safe:
            safe = safe.replace(".", f"--{digest}.", 1)
        else:
            safe = (safe or "doc") + f"--{digest}"
    return safe


def variant_summary(variant: Variant) -> dict[str, object]:
    service = LlmWikiService(variant.root, managed_context=variant.managed_context)
    manifest = service.manifest()
    return {
        "variant_id": variant.id,
        "adapter": manifest.adapter,
        "implementation": manifest.implementation,
        "page_count": manifest.page_count,
        "approved_page_count": manifest.approved_page_count,
        "materialization": variant.materialization,
        "managed_context_effective": (
            bool(
                isinstance(variant.managed_context, ManagedContextConfig)
                and variant.managed_context.enabled
                and manifest.adapter == "generic-markdown"
            )
        ),
    }


def collect_surface(
    variant: Variant,
    *,
    phase: str,
    surface: str,
    queries: Mapping[str, benchmark.QueryRow],
    corpus: Mapping[str, benchmark.CorpusRow],
    tokenizer: CountingTokenizer,
    limit: int,
    citation_policy: CitationEvidencePolicy,
) -> SurfaceResult:
    run_id = run_id_for(variant.id, phase, surface)
    rows: list[dict[str, object]] = []
    query_summaries: list[dict[str, object]] = []
    service_factory = service_factory_for(variant, run_id=run_id)

    service: LlmWikiService | None = None
    if phase in {"warm", "primed"}:
        service = service_factory()
        service.index()
    if phase == "primed":
        assert service is not None
        prime_service(service, surface=surface, queries=queries.values(), limit=limit)

    for query in queries.values():
        query_service = service_factory(query.query_id) if phase == "cold" else service
        assert query_service is not None
        timed = timed_collect_query(
            query_service,
            query,
            run_id=run_id,
            surface=surface,
            corpus=corpus,
            tokenizer=tokenizer,
            limit=limit,
            citation_policy=citation_policy,
        )
        rows.extend(timed.rows)
        query_summaries.append(timed.summary)

    return SurfaceResult(
        rows=rows,
        summary={
            "run_id": run_id,
            "variant_id": variant.id,
            "phase": phase,
            "surface": surface,
            "query_count": len(queries),
            "run_row_count": len(rows),
            "query_payloads": query_summaries,
        },
    )


def service_factory_for(variant: Variant, *, run_id: str) -> Callable[[str | None], LlmWikiService]:
    def factory(query_id: str | None = None) -> LlmWikiService:
        return LlmWikiService(
            variant.root,
            managed_context=managed_context_for_run(variant, run_id=run_id, query_id=query_id),
        )

    return factory


def managed_context_for_run(
    variant: Variant,
    *,
    run_id: str,
    query_id: str | None,
) -> ManagedContextConfig | bool:
    if not isinstance(variant.managed_context, ManagedContextConfig):
        return variant.managed_context
    if not variant.managed_context.enabled:
        return variant.managed_context
    configured_state = variant.managed_context.state_dir
    state_root = (configured_state or variant.root.parent / "managed-context-state") / slug_part(
        run_id
    )
    if query_id is not None:
        state_root = state_root / slug_part(query_id)
    return replace(variant.managed_context, state_dir=state_root)


def prime_service(
    service: LlmWikiService,
    *,
    surface: str,
    queries: Sequence[benchmark.QueryRow],
    limit: int,
) -> None:
    for query in queries:
        if surface in SERVICE_CONTEXT_SURFACES:
            service.context(
                query.text,
                limit=limit,
                fields=SEARCH_RESULT_FIELDS,
                snippet_chars=240,
            )
        elif surface == "service-search-read":
            results = service.search(
                query.text,
                limit=limit,
                fields=SEARCH_RESULT_FIELDS,
                snippet_chars=240,
            )
            for result in results:
                page_id = str(result.get("page_id") or "")
                if page_id:
                    service.read(page_id, fields=READ_FIELDS)
        else:  # pragma: no cover - guarded by caller constants.
            raise CollectorError(f"unknown service surface: {surface}")


def timed_collect_query(
    service: LlmWikiService,
    query: benchmark.QueryRow,
    *,
    run_id: str,
    surface: str,
    corpus: Mapping[str, benchmark.CorpusRow],
    tokenizer: CountingTokenizer,
    limit: int,
    citation_policy: CitationEvidencePolicy,
) -> SurfaceResult:
    started = time.perf_counter()
    if surface == "service-context":
        payload, result_payloads = collect_context_payload(service, query.text, limit=limit)
    elif surface == "service-context-orientation":
        payload, result_payloads = collect_context_orientation_payload(
            service,
            query.text,
            limit=limit,
        )
    elif surface == "service-context-bundle":
        payload, result_payloads = collect_context_bundle_payload(
            service,
            query.text,
            limit=limit,
        )
    elif surface == "service-search-read":
        payload, result_payloads = collect_search_read_payload(service, query.text, limit=limit)
    else:  # pragma: no cover - guarded by caller constants.
        raise CollectorError(f"unknown service surface: {surface}")
    latency_ms = (time.perf_counter() - started) * 1000.0
    payload_bytes = len(json_bytes(payload))
    payload_tokens = tokenizer.count_tokens(json_text(payload))
    rows: list[dict[str, object]] = []
    seen_doc_ids: set[str] = set()
    for result_payload in result_payloads:
        doc_id = require_doc_id(result_payload, corpus, run_id=run_id, query_id=query.query_id)
        if surface == "service-context-bundle":
            if doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(doc_id)
        citation_id_values = citation_ids(
            result_payload,
            corpus[doc_id],
            citation_policy=citation_policy,
        )
        rows.append(
            {
                "run_id": run_id,
                "query_id": query.query_id,
                "rank": len(rows) + 1,
                "doc_id": doc_id,
                "score": float(result_payload.get("score") or 0.0),
                "citation_ids": citation_id_values,
                "context_tokens": tokenizer.count_tokens(json_text(result_payload)),
                "payload_tokens": payload_tokens,
                "payload_bytes": payload_bytes,
                "latency_ms": latency_ms,
                "surface": surface,
                "tokenizer_id": tokenizer.tokenizer_id,
                "tokenizer_revision": tokenizer.tokenizer_revision,
                "source_bytes_scanned": None,
            }
        )
    return SurfaceResult(
        rows=rows,
        summary={
            "query_id": query.query_id,
            "payload_tokens": payload_tokens,
            "payload_bytes": payload_bytes,
            "latency_ms": latency_ms,
            "row_count": len(rows),
            "orientation_count": orientation_count(payload),
            "evidence_count": query_evidence_count(payload, result_payloads),
        },
    )


def collect_context_payload(
    service: LlmWikiService,
    query_text: str,
    *,
    limit: int,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    context = service.context(
        query_text,
        limit=limit,
        fields=SEARCH_RESULT_FIELDS,
        snippet_chars=240,
    )
    payload = context.model_dump(mode="json")
    evidence = [normalized_mapping(item) for item in payload.get("evidence", [])]
    return payload, evidence


def collect_context_orientation_payload(
    service: LlmWikiService,
    query_text: str,
    *,
    limit: int,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    context = service.context(
        query_text,
        limit=limit,
        fields=SEARCH_RESULT_FIELDS,
        snippet_chars=240,
    )
    payload = context.model_dump(mode="json")
    orientation = [normalized_mapping(item) for item in payload.get("orientation", [])]
    return payload, orientation


def collect_context_bundle_payload(
    service: LlmWikiService,
    query_text: str,
    *,
    limit: int,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    context = service.context(
        query_text,
        limit=limit,
        fields=SEARCH_RESULT_FIELDS,
        snippet_chars=240,
    )
    payload = context.model_dump(mode="json")
    orientation = [normalized_mapping(item) for item in payload.get("orientation", [])]
    evidence = [normalized_mapping(item) for item in payload.get("evidence", [])]
    return payload, [*orientation, *evidence]


def collect_search_read_payload(
    service: LlmWikiService,
    query_text: str,
    *,
    limit: int,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    search_results = service.search(
        query_text,
        limit=limit,
        fields=SEARCH_RESULT_FIELDS,
        snippet_chars=240,
    )
    result_payloads: list[dict[str, object]] = []
    read_payloads: list[dict[str, object]] = []
    for result in search_results:
        page_id = str(result.get("page_id") or "")
        read_payload = service.read(page_id, fields=READ_FIELDS) if page_id else {"found": False}
        read_payloads.append(read_payload)
        merged = {**result, "read": read_payload}
        if not merged.get("source_refs") and isinstance(read_payload.get("source_refs"), list):
            merged["source_refs"] = read_payload["source_refs"]
        result_payloads.append(merged)
    return {"search": search_results, "reads": read_payloads}, result_payloads


def require_doc_id(
    result_payload: Mapping[str, object],
    corpus: Mapping[str, benchmark.CorpusRow],
    *,
    run_id: str,
    query_id: str,
) -> str:
    raw_doc_id = result_payload.get("page_id") or result_payload.get("id")
    if not isinstance(raw_doc_id, str) or not raw_doc_id:
        read_payload = result_payload.get("read")
        if isinstance(read_payload, dict):
            raw_doc_id = read_payload.get("id")
    if not isinstance(raw_doc_id, str) or not raw_doc_id:
        raise CollectorError(f"{run_id}/{query_id}: service result did not include a page id")
    if raw_doc_id not in corpus:
        raise CollectorError(
            f"{run_id}/{query_id}: service returned doc_id {raw_doc_id!r} "
            "that is absent from the corpus manifest"
        )
    return raw_doc_id


def citation_ids(
    result_payload: Mapping[str, object],
    corpus_row: benchmark.CorpusRow,
    *,
    citation_policy: CitationEvidencePolicy,
) -> list[str]:
    service_ids = authored_citation_ids(result_payload)
    if service_ids:
        validate_authored_citation_ids(service_ids, corpus_row)
        return service_ids
    if not citation_policy.deterministic_public_path_ids:
        return service_ids
    return list(corpus_row.source_ref_ids)


def validate_authored_citation_ids(
    citation_id_values: Sequence[str],
    corpus_row: benchmark.CorpusRow,
) -> None:
    allowed_refs = (
        set(corpus_row.source_ref_ids) if corpus_row.source_ref_ids else {corpus_row.doc_id}
    )
    unexpected = sorted(set(citation_id_values) - allowed_refs)
    if unexpected:
        raise CollectorError(
            f"service returned citation ids not attached to doc_id {corpus_row.doc_id!r}: "
            f"{unexpected}"
        )


def authored_citation_ids(result_payload: Mapping[str, object]) -> list[str]:
    source_refs = result_payload.get("source_refs")
    if isinstance(source_refs, list):
        citation_id_values = [str(item) for item in source_refs if isinstance(item, str) and item]
        if citation_id_values:
            return citation_id_values
    read_payload = result_payload.get("read")
    if isinstance(read_payload, dict) and isinstance(read_payload.get("source_refs"), list):
        return [str(item) for item in read_payload["source_refs"] if isinstance(item, str) and item]
    return []


def orientation_count(payload: Mapping[str, object]) -> int:
    orientation = payload.get("orientation")
    return len(orientation) if isinstance(orientation, list) else 0


def query_evidence_count(
    payload: Mapping[str, object],
    result_payloads: Sequence[Mapping[str, object]],
) -> int:
    evidence = payload.get("evidence")
    return len(evidence) if isinstance(evidence, list) else len(result_payloads)


def normalized_mapping(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        if isinstance(dumped, dict):
            return dumped
    raise CollectorError("service payload item was not JSON-object-like")


def run_id_for(variant_id: str, phase: str, surface: str) -> str:
    return "_".join(
        part
        for part in (
            slug_part(variant_id),
            slug_part(phase),
            slug_part(surface),
        )
        if part
    )


def slug_part(value: str) -> str:
    return RUN_ID_PART_RE.sub("_", value).strip("_").lower()


def copy_inputs(input_paths: BenchmarkInputPaths, output_dir: Path) -> None:
    shutil.copyfile(input_paths.corpus, output_dir / "corpus.jsonl")
    shutil.copyfile(input_paths.queries, output_dir / "queries.jsonl")
    shutil.copyfile(input_paths.qrels, output_dir / "qrels.jsonl")
    write_json(output_dir / "case-manifest.json", input_paths.public_case_manifest)


def build_collection_report(
    *,
    mode: str,
    source: SourceResolution,
    inputs: BenchmarkInputs,
    tokenizer: CountingTokenizer | None,
    variant_summaries: Sequence[Mapping[str, object]],
    run_summaries: Sequence[Mapping[str, object]],
    output_files: Sequence[str],
    source_digest: benchmark.SourceMutationDigest | None,
    citation_policy: CitationEvidencePolicy,
) -> dict[str, object]:
    tokenizer_report: dict[str, object] | None = None
    if tokenizer is not None:
        tokenizer_report = {
            "id": tokenizer.tokenizer_id,
            "revision": tokenizer.tokenizer_revision,
            "policy": "qwen-tokenizer-required-no-byte-proxy",
            "collection_evidence": (
                "loaded-by-transformers"
                if not isinstance(tokenizer, FixtureTokenizer)
                else "fixture-tokenizer-test-only"
            ),
        }
    report: dict[str, object] = {
        "schema": COLLECTION_SCHEMA,
        "mode": mode,
        "evidence_track": "quality-benchmark-run-collection",
        "source": source.source_report,
        "case": source.case_metadata,
        "inputs": {
            "corpus_records": len(inputs.corpus),
            "query_records": len(inputs.queries),
            "qrel_records": inputs.qrel_count,
        },
        "tokenizer": tokenizer_report,
        "citation_evidence": citation_policy.as_report(),
        "variants": list(variant_summaries),
        "runs": list(run_summaries),
        "output_files": list(output_files),
        "source_mutation": source_digest.as_report() if source_digest is not None else None,
        "redaction_status": "pass",
    }
    benchmark.assert_public_safe_value(report, "collection-report")
    return report


def write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def json_text(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def json_bytes(payload: object) -> bytes:
    return json_text(payload).encode("utf-8")


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
