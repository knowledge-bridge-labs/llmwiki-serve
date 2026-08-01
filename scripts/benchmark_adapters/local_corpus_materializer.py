"""Materialize a local Markdown/LLMWiki root as a corpus-only benchmark bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from llmwiki_serve.service import LlmWikiService  # noqa: E402
from scripts.benchmark_adapters import bundle_validator as validator  # noqa: E402

MATERIALIZER_NAME = "local-llmwiki-corpus"
MATERIALIZER_VERSION = "0.1.0"
IGNORED_TREE_DIGEST_PARTS = {
    ".git",
    ".llmwiki-work",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".runtime-logs",
    "__pycache__",
}


class CorpusMaterializerError(RuntimeError):
    """Raised when corpus-only bundle materialization cannot proceed safely."""


@dataclass(frozen=True)
class MaterializeCorpusConfig:
    source_root: Path
    output_dir: Path
    bundle_id: str
    dataset: str
    source_url: str
    source_revision: str
    source_release: str | None
    license_spdx: str
    license_url: str
    license_verified_date: str
    attribution: str
    redistribution_policy: str
    public_report_policy: str
    repo_root: Path = ROOT


@dataclass(frozen=True)
class MaterializeCorpusResult:
    output_dir: Path
    corpus_count: int
    source_before_sha256: str
    source_after_sha256: str

    @property
    def source_mutated(self) -> bool:
        return self.source_before_sha256 != self.source_after_sha256

    def as_json(self) -> dict[str, object]:
        return {
            "output_dir": str(self.output_dir),
            "corpus_count": self.corpus_count,
            "source_before_sha256": self.source_before_sha256,
            "source_after_sha256": self.source_after_sha256,
            "source_mutated": self.source_mutated,
        }


def materialize_corpus_bundle(config: MaterializeCorpusConfig) -> MaterializeCorpusResult:
    source_root = config.source_root.expanduser().resolve()
    if not source_root.is_dir():
        raise CorpusMaterializerError("source_root must exist and be a directory")
    output_dir = prepare_output_dir(
        config.output_dir,
        source_root=source_root,
        repo_root=config.repo_root,
    )
    source_before = compute_tree_digest(source_root)

    service = LlmWikiService(source_root)
    index = service.index()
    corpus_rows = [
        corpus_row(page, adapter=index.adapter, implementation=index.implementation)
        for page in sorted(index.pages, key=lambda item: (item.path, item.id))
        if page.approved_for_serving
    ]
    if not corpus_rows:
        raise CorpusMaterializerError("source_root produced no approved retrievable pages")

    write_jsonl(output_dir / "corpus.jsonl", corpus_rows)
    for file_name in ("queries.jsonl", "qrels.jsonl", "evidence.jsonl"):
        (output_dir / file_name).write_text("", encoding="utf-8")
    provenance = provenance_record(config, output_dir)
    write_json(output_dir / "provenance.json", provenance)

    validator.validate_bundle(output_dir)
    source_after = compute_tree_digest(source_root)
    if source_after != source_before:
        raise CorpusMaterializerError("source tree mutated during corpus materialization")
    return MaterializeCorpusResult(
        output_dir=output_dir,
        corpus_count=len(corpus_rows),
        source_before_sha256=source_before,
        source_after_sha256=source_after,
    )


def prepare_output_dir(output_dir: Path, *, source_root: Path, repo_root: Path) -> Path:
    resolved_output = output_dir.expanduser().resolve()
    try:
        resolved_output.relative_to(source_root)
    except ValueError:
        pass
    else:
        raise CorpusMaterializerError("output_dir must be outside source_root")

    resolved_repo = repo_root.expanduser().resolve()
    try:
        relative_to_repo = resolved_output.relative_to(resolved_repo)
    except ValueError:
        relative_to_repo = None
    if relative_to_repo is not None and relative_to_repo.parts[:2] != (
        validator.DEFAULT_WORKSPACE_NAME,
        "benchmark-adapters",
    ):
        raise CorpusMaterializerError(
            "output_dir inside the repository must be under "
            f"{validator.DEFAULT_BENCHMARK_WORKSPACE.as_posix()}/"
        )

    if resolved_output.exists() and any(resolved_output.iterdir()):
        raise CorpusMaterializerError("output_dir already exists and is not empty")
    resolved_output.mkdir(parents=True, exist_ok=True)
    return resolved_output


def corpus_row(page: Any, *, adapter: str, implementation: str) -> dict[str, object]:
    source_path = require_portable_path_handle(str(page.path), label=f"{page.id}:path")
    corpus_id = stable_corpus_id(str(page.id), source_path)
    return {
        "corpus_id": corpus_id,
        "metadata": {
            "adapter": adapter,
            "approved_for_serving": True,
            "implementation": implementation,
            "role": str(page.role),
            "source_path": source_path,
        },
        "text": str(page.text),
        "title": str(page.title),
    }


def stable_corpus_id(page_id: str, source_path: str) -> str:
    base = safe_id(page_id) or safe_id(Path(source_path).with_suffix("").as_posix())
    digest = hashlib.sha256(source_path.encode("utf-8")).hexdigest()[:10]
    return f"{base}-{digest}"


def safe_id(value: str) -> str:
    normalized = "-".join(part for part in value.replace("\\", "/").split("/") if part)
    safe = "".join(
        char.lower() if char.isascii() and char.isalnum() else "-" for char in normalized
    )
    return "-".join(part for part in safe.split("-") if part)


def require_portable_path_handle(value: str, *, label: str) -> str:
    if not value or "\\" in value or ":" in value or value.startswith("/"):
        raise CorpusMaterializerError(f"{label} must be a portable relative POSIX path handle")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise CorpusMaterializerError(f"{label} must not contain empty, dot, or dot-dot parts")
    return value


def provenance_record(
    config: MaterializeCorpusConfig,
    output_dir: Path,
) -> dict[str, object]:
    record: dict[str, object] = {
        "adapter": {
            "name": MATERIALIZER_NAME,
            "version": MATERIALIZER_VERSION,
        },
        "bundle_id": config.bundle_id,
        "checksums": {
            file_name: f"sha256:{validator.canonical_text_file_sha256(output_dir / file_name)}"
            for file_name in validator.BUNDLE_JSONL_FILES
        },
        "component_licenses": [
            {
                "attribution": config.attribution,
                "component": config.dataset,
                "license_spdx": config.license_spdx,
                "license_url": config.license_url,
                "license_verified_date": config.license_verified_date,
                "public_report_policy": config.public_report_policy,
                "redistribution_policy": config.redistribution_policy,
            }
        ],
        "dataset": config.dataset,
        "schema_id": validator.SCHEMA_ID,
        "source_revision": config.source_revision,
        "source_url": config.source_url,
    }
    if config.source_release is not None:
        record["source_release"] = config.source_release
    return record


def compute_tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root)
        if any(part in IGNORED_TREE_DIGEST_PARTS for part in relative.parts):
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize a local Markdown/LLMWiki root as a corpus-only bundle."
    )
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--source-release")
    parser.add_argument("--license-spdx", required=True)
    parser.add_argument("--license-url", required=True)
    parser.add_argument("--license-verified-date", required=True)
    parser.add_argument("--attribution", required=True)
    parser.add_argument("--redistribution-policy", required=True)
    parser.add_argument("--public-report-policy", required=True)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> MaterializeCorpusConfig:
    return MaterializeCorpusConfig(
        source_root=args.source_root,
        output_dir=args.output_dir,
        bundle_id=str(args.bundle_id),
        dataset=str(args.dataset),
        source_url=str(args.source_url),
        source_revision=str(args.source_revision),
        source_release=args.source_release,
        license_spdx=str(args.license_spdx),
        license_url=str(args.license_url),
        license_verified_date=str(args.license_verified_date),
        attribution=str(args.attribution),
        redistribution_policy=str(args.redistribution_policy),
        public_report_policy=str(args.public_report_policy),
        repo_root=args.repo_root,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = materialize_corpus_bundle(config_from_args(args))
    except (CorpusMaterializerError, validator.BundleValidationError, ValueError) as error:
        print(f"local corpus materialization failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result.as_json(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
