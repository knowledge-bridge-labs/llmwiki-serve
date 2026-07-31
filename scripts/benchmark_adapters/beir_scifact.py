"""Materialize BEIR SciFact JSONL/TSV inputs as an offline benchmark bundle."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from scripts.benchmark_adapters import bundle_validator as validator

ROOT = Path(__file__).resolve().parents[2]
ADAPTER_NAME = "beir-scifact"
ADAPTER_VERSION = "0.1.0"
DATASET_NAME = "beir-scifact"
SOURCE_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"
LICENSE_VERIFIED_DATE = "2026-07-31"
OFFICIAL_SCIFACT_CORPUS_COUNT = 5_183
OFFICIAL_SCIFACT_SOURCE_QUERY_COUNT = 1_109
OFFICIAL_SCIFACT_TEST_QUERY_COUNT = 300
OFFICIAL_SCIFACT_QREL_COUNT = 339
OFFICIAL_SCIFACT_RELEVANCE_VALUES = frozenset({1})


@dataclass(frozen=True)
class _SourceCorpusRow:
    corpus_id: str
    title: str
    text: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class _SourceQueryRow:
    query_id: str
    text: str
    metadata: dict[str, Any]


class BeirScifactMaterializerError(RuntimeError):
    """Raised when BEIR SciFact materialization cannot proceed safely."""


@dataclass(frozen=True)
class MaterializeBeirScifactResult:
    output_dir: Path
    wiki_dir: Path
    bundle_dir: Path
    archive_sha256: str
    corpus_count: int
    query_count: int
    qrel_count: int
    input_before_sha256: str
    input_after_sha256: str

    @property
    def input_mutated(self) -> bool:
        return self.input_before_sha256 != self.input_after_sha256

    def as_json(self) -> dict[str, object]:
        return {
            "bundle_dir": str(self.bundle_dir),
            "archive_sha256": self.archive_sha256,
            "corpus_count": self.corpus_count,
            "input_after_sha256": self.input_after_sha256,
            "input_before_sha256": self.input_before_sha256,
            "input_mutated": self.input_mutated,
            "output_dir": str(self.output_dir),
            "qrel_count": self.qrel_count,
            "query_count": self.query_count,
            "wiki_dir": str(self.wiki_dir),
        }


def materialize_beir_scifact(
    input_dir: Path,
    output_dir: Path,
    archive_sha256: str,
    *,
    enforce_official_canonical_invariants: bool = True,
    repo_root: Path = ROOT,
) -> MaterializeBeirScifactResult:
    """Materialize a local BEIR SciFact archive extract without mutating it."""
    source_revision = f"sha256:{_require_archive_sha256(archive_sha256)}"
    resolved_input = input_dir.expanduser().resolve()
    if not resolved_input.is_dir():
        raise BeirScifactMaterializerError("input_dir must exist and be a directory")
    resolved_output = _resolve_output_dir(
        output_dir,
        input_dir=resolved_input,
        repo_root=repo_root,
    )
    input_before = _compute_tree_digest(resolved_input)

    corpus_rows = _read_corpus(resolved_input / "corpus.jsonl")
    source_queries = _read_source_queries(resolved_input / "queries.jsonl")
    qrel_rows = _read_qrels(
        resolved_input / "qrels" / "test.tsv",
        corpus_ids=frozenset(str(row["corpus_id"]) for row in corpus_rows),
        query_ids=frozenset(source_queries),
    )
    query_rows = _select_test_queries(source_queries, qrel_rows)
    if enforce_official_canonical_invariants:
        _validate_official_canonical_invariants(
            corpus_rows=corpus_rows,
            source_query_count=len(source_queries),
            query_rows=query_rows,
            qrel_rows=qrel_rows,
        )

    wiki_dir = resolved_output / "wiki"
    bundle_dir = resolved_output / "bundle"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    _write_wiki_documents(wiki_dir, corpus_rows)
    _write_jsonl(bundle_dir / "corpus.jsonl", corpus_rows)
    _write_jsonl(bundle_dir / "queries.jsonl", query_rows)
    _write_jsonl(bundle_dir / "qrels.jsonl", qrel_rows)
    (bundle_dir / "evidence.jsonl").write_text("", encoding="utf-8")
    _write_json(bundle_dir / "provenance.json", _provenance_record(bundle_dir, source_revision))

    validator.validate_bundle(bundle_dir)
    input_after = _compute_tree_digest(resolved_input)
    if input_after != input_before:
        raise BeirScifactMaterializerError("input tree mutated during materialization")

    return MaterializeBeirScifactResult(
        output_dir=resolved_output,
        wiki_dir=wiki_dir,
        bundle_dir=bundle_dir,
        archive_sha256=source_revision.removeprefix("sha256:"),
        corpus_count=len(corpus_rows),
        query_count=len(query_rows),
        qrel_count=len(qrel_rows),
        input_before_sha256=input_before,
        input_after_sha256=input_after,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize an official BEIR SciFact extract as a local benchmark bundle."
    )
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    return parser.parse_args(argv)


def run(
    argv: Sequence[str] | None = None,
    *,
    enforce_official_canonical_invariants: bool = True,
) -> int:
    args = parse_args(argv)
    try:
        output_dir = _resolve_cli_output_dir(
            cast(Path, args.output_dir),
            repo_root=cast(Path, args.repo_root),
        )
        result = materialize_beir_scifact(
            cast(Path, args.input_dir),
            output_dir,
            cast(str, args.archive_sha256),
            enforce_official_canonical_invariants=enforce_official_canonical_invariants,
            repo_root=cast(Path, args.repo_root),
        )
    except (
        BeirScifactMaterializerError,
        OSError,
        ValueError,
        validator.BundleValidationError,
    ) as error:
        print(f"beir scifact materialization failed: {error}", file=sys.stderr)
        return 2

    print(json.dumps(_public_cli_success_metadata(result), indent=2, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv)


def _resolve_cli_output_dir(output_dir: Path, *, repo_root: Path) -> Path:
    resolved_output = output_dir.expanduser().resolve()
    resolved_repo = repo_root.expanduser().resolve()
    try:
        relative_to_repo = resolved_output.relative_to(resolved_repo)
    except ValueError as error:
        raise BeirScifactMaterializerError(
            "output_dir must be under the repository local benchmark workspace"
        ) from error
    if relative_to_repo.parts[:2] != (validator.DEFAULT_WORKSPACE_NAME, "benchmark-adapters"):
        raise BeirScifactMaterializerError(
            f"output_dir must stay under {validator.DEFAULT_BENCHMARK_WORKSPACE.as_posix()}/"
        )
    return resolved_output


def _public_cli_success_metadata(result: MaterializeBeirScifactResult) -> dict[str, object]:
    return {
        "adapter": {
            "name": ADAPTER_NAME,
            "version": ADAPTER_VERSION,
        },
        "archive_sha256": result.archive_sha256,
        "bundle_created": True,
        "corpus_count": result.corpus_count,
        "dataset": DATASET_NAME,
        "input_mutated": result.input_mutated,
        "local_outputs": {
            "bundle_dir": "local-only",
            "output_dir": "local-only",
            "wiki_dir": "local-only",
        },
        "qrel_count": result.qrel_count,
        "query_count": result.query_count,
    }


def _read_corpus(path: Path) -> list[dict[str, object]]:
    source_rows = _load_jsonl(path, required=("_id", "title", "text", "metadata"))
    seen: set[str] = set()
    corpus_sources: list[_SourceCorpusRow] = []
    for label, source in source_rows:
        corpus_id = _require_source_string(source, "_id", label)
        if corpus_id in seen:
            raise BeirScifactMaterializerError(f"{label}: duplicate corpus _id {corpus_id!r}")
        seen.add(corpus_id)
        title = _require_source_string(source, "title", label, allow_empty=True)
        text = _require_source_string(source, "text", label)
        metadata = _require_source_metadata(source, label)
        corpus_sources.append(
            _SourceCorpusRow(corpus_id=corpus_id, title=title, text=text, metadata=metadata)
        )

    rows: list[dict[str, object]] = []
    for corpus_source in sorted(corpus_sources, key=lambda row: row.corpus_id):
        rows.append(
            {
                "corpus_id": corpus_source.corpus_id,
                "metadata": {
                    "beir_id": corpus_source.corpus_id,
                    "beir_metadata": corpus_source.metadata,
                },
                "text": corpus_source.text,
                "title": corpus_source.title,
            }
        )
    if not rows:
        raise BeirScifactMaterializerError("corpus.jsonl must contain at least one row")
    return rows


def _read_source_queries(path: Path) -> dict[str, _SourceQueryRow]:
    source_rows = _load_jsonl(path, required=("_id", "text", "metadata"))
    seen: set[str] = set()
    rows: dict[str, _SourceQueryRow] = {}
    for label, source in source_rows:
        query_id = _require_source_string(source, "_id", label)
        if query_id in seen:
            raise BeirScifactMaterializerError(f"{label}: duplicate query _id {query_id!r}")
        seen.add(query_id)
        text = _require_source_string(source, "text", label)
        metadata = _require_source_metadata(source, label)
        rows[query_id] = _SourceQueryRow(query_id=query_id, text=text, metadata=metadata)
    if not rows:
        raise BeirScifactMaterializerError("queries.jsonl must contain at least one row")
    return rows


def _select_test_queries(
    source_queries: Mapping[str, _SourceQueryRow],
    qrel_rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    selected_query_ids = sorted({str(row["query_id"]) for row in qrel_rows})
    rows: list[dict[str, object]] = []
    for query_id in selected_query_ids:
        source = source_queries[query_id]
        rows.append(
            {
                "answerability": "unknown",
                "answers": [],
                "evaluation_split": "holdout",
                "label_source": "beir-scifact-test-qrels",
                "query": source.text,
                "query_id": query_id,
                "source_split": "test",
                "tags": ["retrieval"],
            }
        )
    if not rows:
        raise BeirScifactMaterializerError("qrels/test.tsv must identify at least one test query")
    return rows


def _read_qrels(
    path: Path,
    *,
    corpus_ids: frozenset[str],
    query_ids: frozenset[str],
) -> list[dict[str, object]]:
    if not path.is_file():
        raise BeirScifactMaterializerError("qrels/test.tsv must exist")

    qrels: list[dict[str, object]] = []
    seen_pairs: set[tuple[str, str]] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if reader.fieldnames != ["query-id", "corpus-id", "score"]:
            raise BeirScifactMaterializerError(
                "qrels/test.tsv header must be: query-id, corpus-id, score"
            )
        for line_number, row in enumerate(reader, start=2):
            label = f"qrels/test.tsv:{line_number}"
            if None in row or any(value is None for value in row.values()):
                raise BeirScifactMaterializerError(f"{label}: malformed TSV row")
            query_id = row["query-id"]
            corpus_id = row["corpus-id"]
            if not query_id:
                raise BeirScifactMaterializerError(f"{label}: query-id must be non-empty")
            if not corpus_id:
                raise BeirScifactMaterializerError(f"{label}: corpus-id must be non-empty")
            if query_id not in query_ids:
                raise BeirScifactMaterializerError(
                    f"{label}: qrel references unknown query_id {query_id!r}"
                )
            if corpus_id not in corpus_ids:
                raise BeirScifactMaterializerError(
                    f"{label}: qrel references unknown corpus_id {corpus_id!r}"
                )
            pair = (query_id, corpus_id)
            if pair in seen_pairs:
                raise BeirScifactMaterializerError(
                    f"{label}: duplicate qrel for query_id={query_id!r} corpus_id={corpus_id!r}"
                )
            seen_pairs.add(pair)
            relevance = _parse_relevance(row["score"], label)
            qrels.append(
                {
                    "corpus_id": corpus_id,
                    "query_id": query_id,
                    "relevance": relevance,
                }
            )
    if not qrels:
        raise BeirScifactMaterializerError("qrels/test.tsv must contain at least one row")
    _require_positive_qrel_per_query(
        qrels,
        query_ids=frozenset(str(qrel["query_id"]) for qrel in qrels),
    )
    return sorted(qrels, key=lambda row: (str(row["query_id"]), str(row["corpus_id"])))


def _require_positive_qrel_per_query(
    qrels: Sequence[Mapping[str, object]],
    *,
    query_ids: frozenset[str],
) -> None:
    positive_query_ids = {
        str(qrel["query_id"])
        for qrel in qrels
        if isinstance(qrel["relevance"], int | float) and qrel["relevance"] > 0
    }
    missing = sorted(query_ids - positive_query_ids)
    if missing:
        raise BeirScifactMaterializerError(
            "every query must have at least one qrel with relevance > 0; "
            f"missing or zero-only query_ids: {missing}"
        )


def _validate_official_canonical_invariants(
    *,
    corpus_rows: Sequence[Mapping[str, object]],
    source_query_count: int,
    query_rows: Sequence[Mapping[str, object]],
    qrel_rows: Sequence[Mapping[str, object]],
) -> None:
    failures: list[str] = []
    if len(corpus_rows) != OFFICIAL_SCIFACT_CORPUS_COUNT:
        failures.append(f"corpus_count={len(corpus_rows)} expected {OFFICIAL_SCIFACT_CORPUS_COUNT}")
    if source_query_count != OFFICIAL_SCIFACT_SOURCE_QUERY_COUNT:
        failures.append(
            "source_query_count="
            f"{source_query_count} expected {OFFICIAL_SCIFACT_SOURCE_QUERY_COUNT}"
        )
    if len(query_rows) != OFFICIAL_SCIFACT_TEST_QUERY_COUNT:
        failures.append(
            "selected_test_query_count="
            f"{len(query_rows)} expected {OFFICIAL_SCIFACT_TEST_QUERY_COUNT}"
        )
    if len(qrel_rows) != OFFICIAL_SCIFACT_QREL_COUNT:
        failures.append(f"qrel_count={len(qrel_rows)} expected {OFFICIAL_SCIFACT_QREL_COUNT}")
    relevance_values = frozenset(row["relevance"] for row in qrel_rows)
    if relevance_values != OFFICIAL_SCIFACT_RELEVANCE_VALUES:
        failures.append(
            "relevance_values="
            f"{_sorted_display(relevance_values)} expected "
            f"{_sorted_display(OFFICIAL_SCIFACT_RELEVANCE_VALUES)}"
        )
    if failures:
        raise BeirScifactMaterializerError(
            "official SciFact canonical invariants failed: " + "; ".join(failures)
        )


def _load_jsonl(path: Path, *, required: tuple[str, ...]) -> list[tuple[str, dict[str, Any]]]:
    if not path.is_file():
        raise BeirScifactMaterializerError(f"{path.name} must exist")
    rows: list[tuple[str, dict[str, Any]]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        label = f"{path.name}:{line_number}"
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as error:
            raise BeirScifactMaterializerError(f"{label}: invalid JSONL") from error
        if not isinstance(parsed, dict):
            raise BeirScifactMaterializerError(f"{label}: JSONL row must be an object")
        missing = sorted(set(required) - set(parsed))
        unknown = sorted(set(parsed) - set(required))
        if missing:
            raise BeirScifactMaterializerError(f"{label}: missing required fields: {missing}")
        if unknown:
            raise BeirScifactMaterializerError(f"{label}: unknown fields: {unknown}")
        rows.append((label, cast(dict[str, Any], parsed)))
    return rows


def _require_source_string(
    record: Mapping[str, Any],
    field: str,
    label: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = record[field]
    if not isinstance(value, str) or (not allow_empty and not value):
        raise BeirScifactMaterializerError(f"{label}.{field} must be a non-empty string")
    return value


def _require_source_metadata(record: Mapping[str, Any], label: str) -> dict[str, Any]:
    value = record["metadata"]
    if not isinstance(value, dict):
        raise BeirScifactMaterializerError(f"{label}.metadata must be an object")
    metadata = cast(dict[str, Any], deepcopy(value))
    try:
        validator.assert_public_safe_value(metadata, f"{label}.metadata")
    except validator.BundleValidationError as error:
        raise BeirScifactMaterializerError(str(error)) from error
    return metadata


def _sorted_display(values: frozenset[object]) -> list[object]:
    return sorted(values, key=lambda value: (type(value).__name__, repr(value)))


def _parse_relevance(value: str, label: str) -> int | float:
    stripped = value.strip()
    if not stripped:
        raise BeirScifactMaterializerError(f"{label}: score must be non-empty")
    try:
        parsed = int(stripped) if re.fullmatch(r"[+-]?\d+", stripped) else float(stripped)
    except ValueError as error:
        raise BeirScifactMaterializerError(f"{label}: score must be numeric") from error
    if isinstance(parsed, float) and not math.isfinite(parsed):
        raise BeirScifactMaterializerError(f"{label}: score must be finite")
    if parsed < 0:
        raise BeirScifactMaterializerError(f"{label}: score must be non-negative")
    return parsed


def _resolve_output_dir(output_dir: Path, *, input_dir: Path, repo_root: Path) -> Path:
    resolved_output = output_dir.expanduser().resolve()
    try:
        resolved_output.relative_to(input_dir)
    except ValueError:
        pass
    else:
        raise BeirScifactMaterializerError("output_dir must be outside input_dir")

    resolved_repo = repo_root.expanduser().resolve()
    try:
        relative_to_repo = resolved_output.relative_to(resolved_repo)
    except ValueError:
        relative_to_repo = None
    if relative_to_repo is not None and relative_to_repo.parts[:2] != (
        validator.DEFAULT_WORKSPACE_NAME,
        "benchmark-adapters",
    ):
        raise BeirScifactMaterializerError(
            "output_dir inside the repository must be under "
            f"{validator.DEFAULT_BENCHMARK_WORKSPACE.as_posix()}/"
        )

    if resolved_output.exists() and any(resolved_output.iterdir()):
        raise BeirScifactMaterializerError("output_dir already exists and is not empty")
    return resolved_output


def _require_archive_sha256(value: str) -> str:
    if not re.fullmatch(r"[a-fA-F0-9]{64}", value):
        raise BeirScifactMaterializerError("archive_sha256 must be 64 hexadecimal characters")
    return value.lower()


def _write_wiki_documents(wiki_dir: Path, corpus_rows: Sequence[Mapping[str, object]]) -> None:
    used_names: set[str] = set()
    for row in corpus_rows:
        corpus_id = str(row["corpus_id"])
        title = str(row["title"])
        file_name = _wiki_file_name(corpus_id, title, used_names)
        used_names.add(file_name.casefold())
        (wiki_dir / file_name).write_text(
            _wiki_markdown(corpus_id=corpus_id, title=title, text=str(row["text"])),
            encoding="utf-8",
        )


def _wiki_file_name(corpus_id: str, title: str, used_names: set[str]) -> str:
    stem = _safe_file_stem(title) or _safe_file_stem(corpus_id) or "document"
    stem = stem[:80].strip("-") or "document"
    candidate = f"{stem}.md"
    if candidate.casefold() not in used_names:
        return candidate

    digest = hashlib.sha256(corpus_id.encode("utf-8")).hexdigest()[:12]
    candidate = f"{stem}-{digest}.md"
    suffix = 1
    while candidate.casefold() in used_names:
        candidate = f"{stem}-{digest}-{suffix}.md"
        suffix += 1
    return candidate


def _safe_file_stem(value: str) -> str:
    safe = "".join(char.lower() if char.isascii() and char.isalnum() else "-" for char in value)
    return "-".join(part for part in safe.split("-") if part)


def _wiki_markdown(*, corpus_id: str, title: str, text: str) -> str:
    heading = _markdown_heading(title)
    return (
        "---\n"
        f"original_id: {json.dumps(corpus_id)}\n"
        f"title: {json.dumps(title)}\n"
        "review_state: approved\n"
        "---\n\n"
        f"# {heading}\n\n"
        f"{text.rstrip()}\n"
    )


def _markdown_heading(title: str) -> str:
    return " ".join(title.split()) or "Untitled"


def _provenance_record(bundle_dir: Path, source_revision: str) -> dict[str, object]:
    return {
        "adapter": {
            "name": ADAPTER_NAME,
            "version": ADAPTER_VERSION,
        },
        "bundle_id": DATASET_NAME,
        "checksums": {
            file_name: f"sha256:{validator.canonical_text_file_sha256(bundle_dir / file_name)}"
            for file_name in validator.BUNDLE_JSONL_FILES
        },
        "component_licenses": [
            _component_license(
                component="BEIR code/format",
                attribution="BEIR (Thakur et al.)",
                license_spdx="Apache-2.0",
                license_url="https://github.com/beir-cellar/beir/blob/main/LICENSE",
                public_report_policy="allowed-with-attribution",
                redistribution_policy="redistributable",
            ),
            _component_license(
                component="SciFact claims/queries and evidence/qrels",
                attribution="SciFact (Wadden et al.)",
                license_spdx="CC-BY-4.0",
                license_url="https://github.com/allenai/scifact/blob/master/LICENSE.md",
                public_report_policy="allowed-with-attribution",
                redistribution_policy="allowed-with-attribution",
            ),
            _component_license(
                component="SciFact corpus abstracts",
                attribution="SciFact/S2ORC",
                license_spdx="ODC-By-1.0",
                license_url="https://github.com/allenai/scifact/blob/master/LICENSE.md",
                public_report_policy="allowed-with-attribution",
                redistribution_policy="allowed-with-attribution",
            ),
        ],
        "dataset": DATASET_NAME,
        "schema_id": validator.SCHEMA_ID,
        "source_revision": source_revision,
        "source_url": SOURCE_URL,
    }


def _component_license(
    *,
    component: str,
    attribution: str,
    license_spdx: str,
    license_url: str,
    public_report_policy: str,
    redistribution_policy: str,
) -> dict[str, str]:
    return {
        "attribution": attribution,
        "component": component,
        "license_spdx": license_spdx,
        "license_url": license_url,
        "license_verified_date": LICENSE_VERIFIED_DATE,
        "public_report_policy": public_report_policy,
        "redistribution_policy": redistribution_policy,
    }


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _compute_tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root)
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BeirScifactMaterializerError",
    "MaterializeBeirScifactResult",
    "main",
    "materialize_beir_scifact",
    "parse_args",
    "run",
]
