"""Acquire and materialize the official NoMIRACL Korean judged pool."""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, BinaryIO, cast
from urllib.parse import quote
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark_adapters import bundle_validator as validator  # noqa: E402

ADAPTER_NAME = "nomiracl-ko"
ADAPTER_VERSION = "0.1.0"
DATASET_NAME = "nomiracl-ko"
DATASET_DISPLAY_NAME = "NoMIRACL Korean dev judged pool"
HF_DATASET_REPO = "miracl/nomiracl"
HF_REVISION = "ecd08778d0426a5ca28ac99763b0c9ddc2c78e68"
HF_DATASET_URL = f"https://huggingface.co/datasets/{HF_DATASET_REPO}"
HF_RESOLVE_BASE_URL = f"{HF_DATASET_URL}/resolve/{HF_REVISION}"
HF_REVISION_API_URL = (
    f"https://huggingface.co/api/datasets/{HF_DATASET_REPO}/revision/{HF_REVISION}"
)
HF_TREE_API_URL = (
    f"https://huggingface.co/api/datasets/{HF_DATASET_REPO}/tree/"
    f"{HF_REVISION}/data/korean?recursive=true"
)
GITHUB_REPO_URL = "https://github.com/project-miracl/nomiracl"
LICENSE_VERIFIED_DATE = "2026-08-02"
LANGUAGE = "korean"
LANGUAGE_CODE = "ko"
NON_RELEVANT_SAMPLE_SEED = "llmwiki-serve-nomiracl-ko-dev-non-relevant-sample-v1"
NON_RELEVANT_SAMPLE_SIZE = 213
OFFICIAL_CORPUS_ROW_COUNT = 53_048
OFFICIAL_UNIQUE_DOCUMENT_COUNT = 37_658
OFFICIAL_DUPLICATE_CORPUS_ROW_COUNT = 15_390
OFFICIAL_DEV_RELEVANT_QUERY_COUNT = 213
OFFICIAL_DEV_RELEVANT_QREL_COUNT = 3_057
OFFICIAL_DEV_RELEVANT_POSITIVE_QREL_COUNT = 547
OFFICIAL_DEV_NON_RELEVANT_QUERY_COUNT = 1_577
OFFICIAL_DEV_NON_RELEVANT_QREL_COUNT = 15_770
OFFICIAL_DEV_NON_RELEVANT_SAMPLE_QUERY_COUNT = NON_RELEVANT_SAMPLE_SIZE
OFFICIAL_DEV_NON_RELEVANT_SAMPLE_QREL_COUNT = NON_RELEVANT_SAMPLE_SIZE * 10
EVALUATION_PROTOCOL = "judged_pool"
FULL_CORPUS_EVALUATED = False
ORIENTATION_FILE_NAMES = frozenset({"hot.md", "index.md", "overview.md", "quickstart.md"})
RUN_MANIFEST_SCHEMA_ID = "llmwiki-nomiracl-ko-acquire-run-v1"
DOWNLOAD_CHUNK_BYTES = 1024 * 1024

SourceOpener = Callable[[str], BinaryIO]


class NomiraclKoError(RuntimeError):
    """Raised when NoMIRACL Korean acquisition/materialization cannot proceed."""


@dataclass(frozen=True)
class OfficialSourceFile:
    relative_path: str
    sha256: str
    size_bytes: int


OFFICIAL_SOURCE_FILES: tuple[OfficialSourceFile, ...] = (
    OfficialSourceFile(
        "data/korean/corpus.jsonl.gz",
        "31baca4fa48f2e64ca44be9b04c0c151e828b6c3577eabbfab5052a68c9f41cf",
        14_261_412,
    ),
    OfficialSourceFile(
        "data/korean/topics/dev.relevant.tsv",
        "a365552cfc997a8915e948a3b5994883f641e7c3f12b6af87bbfaf89729e32a8",
        12_597,
    ),
    OfficialSourceFile(
        "data/korean/qrels/dev.relevant.tsv",
        "88827ccc5b64e531b25f70eef30202d33daa9bfa3ac8cceadf5cdbd5ca5034df",
        55_675,
    ),
    OfficialSourceFile(
        "data/korean/topics/dev.non_relevant.tsv",
        "41e4b012659c6a9ae9322cea4bd351e3ea574671776bf3145450e999bd2001aa",
        94_692,
    ),
    OfficialSourceFile(
        "data/korean/qrels/dev.non_relevant.tsv",
        "a1cf8f43cd6a379a1a584c5d28eb13376aff0cd52f44129223b198bc06eff86f",
        369_011,
    ),
)
OFFICIAL_SOURCE_FILES_BY_PATH = {item.relative_path: item for item in OFFICIAL_SOURCE_FILES}
SOURCE_FILE_PATHS = frozenset(OFFICIAL_SOURCE_FILES_BY_PATH)


@dataclass(frozen=True)
class NomiraclKoSourceCounts:
    corpus_row_count: int
    unique_document_count: int
    duplicate_corpus_row_count: int
    dev_relevant_query_count: int
    dev_relevant_qrel_count: int
    dev_relevant_positive_qrel_count: int
    dev_non_relevant_query_count: int
    dev_non_relevant_qrel_count: int
    dev_non_relevant_positive_qrel_count: int

    def as_public_json(self) -> dict[str, int]:
        return {
            "corpus_row_count": self.corpus_row_count,
            "dev_non_relevant_positive_qrel_count": self.dev_non_relevant_positive_qrel_count,
            "dev_non_relevant_qrel_count": self.dev_non_relevant_qrel_count,
            "dev_non_relevant_query_count": self.dev_non_relevant_query_count,
            "dev_relevant_positive_qrel_count": self.dev_relevant_positive_qrel_count,
            "dev_relevant_qrel_count": self.dev_relevant_qrel_count,
            "dev_relevant_query_count": self.dev_relevant_query_count,
            "duplicate_corpus_row_count": self.duplicate_corpus_row_count,
            "unique_document_count": self.unique_document_count,
        }


OFFICIAL_SOURCE_COUNTS = NomiraclKoSourceCounts(
    corpus_row_count=OFFICIAL_CORPUS_ROW_COUNT,
    unique_document_count=OFFICIAL_UNIQUE_DOCUMENT_COUNT,
    duplicate_corpus_row_count=OFFICIAL_DUPLICATE_CORPUS_ROW_COUNT,
    dev_relevant_query_count=OFFICIAL_DEV_RELEVANT_QUERY_COUNT,
    dev_relevant_qrel_count=OFFICIAL_DEV_RELEVANT_QREL_COUNT,
    dev_relevant_positive_qrel_count=OFFICIAL_DEV_RELEVANT_POSITIVE_QREL_COUNT,
    dev_non_relevant_query_count=OFFICIAL_DEV_NON_RELEVANT_QUERY_COUNT,
    dev_non_relevant_qrel_count=OFFICIAL_DEV_NON_RELEVANT_QREL_COUNT,
    dev_non_relevant_positive_qrel_count=0,
)


@dataclass(frozen=True)
class SourceCorpusRow:
    corpus_id: str
    title: str
    text: str


@dataclass(frozen=True)
class SourceQueryRow:
    query_id: str
    text: str


@dataclass(frozen=True)
class SourceQrelRow:
    query_id: str
    corpus_id: str
    relevance: int | float


@dataclass(frozen=True)
class NomiraclKoEvaluationPool:
    protocol: str
    full_corpus: bool
    document_count: int
    document_ids_sha256: str
    pool_sha256: str
    query_count: int
    qrel_count: int
    relevant_query_count: int
    relevant_qrel_count: int
    positive_qrel_count: int
    non_relevant_sample_query_count: int
    non_relevant_sample_qrel_count: int
    non_relevant_sample_query_ids_sha256: str
    qrel_rows_sha256: str

    def as_public_json(self) -> dict[str, object]:
        return {
            "document_count": self.document_count,
            "document_ids_sha256": f"sha256:{self.document_ids_sha256}",
            "full_corpus": self.full_corpus,
            "non_relevant_sample_qrel_count": self.non_relevant_sample_qrel_count,
            "non_relevant_sample_query_count": self.non_relevant_sample_query_count,
            "non_relevant_sample_query_ids_sha256": (
                f"sha256:{self.non_relevant_sample_query_ids_sha256}"
            ),
            "pool_sha256": f"sha256:{self.pool_sha256}",
            "positive_qrel_count": self.positive_qrel_count,
            "protocol": self.protocol,
            "qrel_count": self.qrel_count,
            "qrel_rows_sha256": f"sha256:{self.qrel_rows_sha256}",
            "query_count": self.query_count,
            "relevant_qrel_count": self.relevant_qrel_count,
            "relevant_query_count": self.relevant_query_count,
            "source_splits": ["dev.relevant", "dev.non_relevant"],
        }


@dataclass(frozen=True)
class NomiraclKoSourceData:
    documents: tuple[SourceCorpusRow, ...]
    relevant_queries: tuple[SourceQueryRow, ...]
    relevant_qrels: tuple[SourceQrelRow, ...]
    non_relevant_queries: tuple[SourceQueryRow, ...]
    non_relevant_qrels: tuple[SourceQrelRow, ...]
    counts: NomiraclKoSourceCounts


@dataclass(frozen=True)
class AcquireNomiraclKoConfig:
    source_dir: Path
    run_manifest_path: Path | None = None
    repo_root: Path = ROOT
    verify_hf_metadata: bool = True


@dataclass(frozen=True)
class AcquiredNomiraclKoResult:
    source_dir: Path
    source_revision: str
    source_url: str
    license_spdx: str
    files: tuple[dict[str, object], ...]
    counts: NomiraclKoSourceCounts
    cache_status: str

    def as_public_json(self) -> dict[str, object]:
        return {
            "adapter": {"name": ADAPTER_NAME, "version": ADAPTER_VERSION},
            "cache_status": self.cache_status,
            "counts": self.counts.as_public_json(),
            "dataset": DATASET_NAME,
            "files": list(self.files),
            "license_spdx": self.license_spdx,
            "local_outputs": {"source_dir": "local-only"},
            "source_revision": self.source_revision,
            "source_url": self.source_url,
        }


@dataclass(frozen=True)
class MaterializeNomiraclKoResult:
    output_dir: Path
    wiki_dir: Path
    bundle_dir: Path
    source_before_sha256: str
    source_after_sha256: str
    source_counts: NomiraclKoSourceCounts
    evaluation_pool: NomiraclKoEvaluationPool

    @property
    def corpus_count(self) -> int:
        return self.evaluation_pool.document_count

    @property
    def query_count(self) -> int:
        return self.evaluation_pool.query_count

    @property
    def qrel_count(self) -> int:
        return self.evaluation_pool.qrel_count

    @property
    def relevant_query_count(self) -> int:
        return self.evaluation_pool.relevant_query_count

    @property
    def relevant_qrel_count(self) -> int:
        return self.evaluation_pool.relevant_qrel_count

    @property
    def positive_qrel_count(self) -> int:
        return self.evaluation_pool.positive_qrel_count

    @property
    def non_relevant_sample_query_count(self) -> int:
        return self.evaluation_pool.non_relevant_sample_query_count

    @property
    def non_relevant_sample_qrel_count(self) -> int:
        return self.evaluation_pool.non_relevant_sample_qrel_count

    @property
    def non_relevant_sample_query_ids_sha256(self) -> str:
        return self.evaluation_pool.non_relevant_sample_query_ids_sha256

    @property
    def source_mutated(self) -> bool:
        return self.source_before_sha256 != self.source_after_sha256

    def as_json(self) -> dict[str, object]:
        return {
            "bundle_dir": str(self.bundle_dir),
            "corpus_count": self.corpus_count,
            "evaluation_pool": self.evaluation_pool.as_public_json(),
            "full_corpus": self.evaluation_pool.full_corpus,
            "non_relevant_sample_qrel_count": self.non_relevant_sample_qrel_count,
            "non_relevant_sample_query_count": self.non_relevant_sample_query_count,
            "non_relevant_sample_query_ids_sha256": self.non_relevant_sample_query_ids_sha256,
            "output_dir": str(self.output_dir),
            "positive_qrel_count": self.positive_qrel_count,
            "protocol": self.evaluation_pool.protocol,
            "qrel_count": self.qrel_count,
            "query_count": self.query_count,
            "relevant_qrel_count": self.relevant_qrel_count,
            "relevant_query_count": self.relevant_query_count,
            "source_after_sha256": self.source_after_sha256,
            "source_before_sha256": self.source_before_sha256,
            "source_counts": self.source_counts.as_public_json(),
            "source_mutated": self.source_mutated,
            "wiki_dir": str(self.wiki_dir),
        }


def acquire_nomiracl_ko(
    config: AcquireNomiraclKoConfig,
    *,
    source_opener: SourceOpener | None = None,
    expected_files: Sequence[OfficialSourceFile] = OFFICIAL_SOURCE_FILES,
    expected_counts: NomiraclKoSourceCounts = OFFICIAL_SOURCE_COUNTS,
) -> AcquiredNomiraclKoResult:
    """Download/cache official NoMIRACL-ko files and verify pinned metadata."""
    opener = source_opener or default_source_opener
    resolved_source_dir = config.source_dir.expanduser().resolve(strict=False)
    _validate_local_workspace_path(
        resolved_source_dir, repo_root=config.repo_root, label="source_dir"
    )
    _prepare_directory(resolved_source_dir)
    if config.verify_hf_metadata:
        verify_hf_revision_and_tree(opener, expected_files=expected_files)

    file_metadata: list[dict[str, object]] = []
    statuses: list[str] = []
    for expected in expected_files:
        destination = resolved_source_dir / PurePosixPath(expected.relative_path)
        status = _download_or_verify_file(destination, expected, source_opener=opener)
        statuses.append(status)
        file_metadata.append(
            {
                "path": expected.relative_path,
                "sha256": f"sha256:{expected.sha256}",
                "size_bytes": expected.size_bytes,
            }
        )

    counts = validate_nomiracl_ko_source(
        resolved_source_dir,
        expected_files=expected_files,
        expected_counts=expected_counts,
        enforce_official_canonical_invariants=True,
    ).counts
    result = AcquiredNomiraclKoResult(
        source_dir=resolved_source_dir,
        source_revision=f"git:{HF_REVISION}",
        source_url=HF_DATASET_URL,
        license_spdx="Apache-2.0",
        files=tuple(file_metadata),
        counts=counts,
        cache_status=_combined_cache_status(statuses),
    )
    if config.run_manifest_path is not None:
        _write_acquire_run_manifest(config.run_manifest_path, result, repo_root=config.repo_root)
    return result


def default_source_opener(url: str) -> BinaryIO:
    """Open a public NoMIRACL source URL with a bounded timeout."""
    return cast(BinaryIO, urlopen(url, timeout=120))


def verify_hf_revision_and_tree(
    source_opener: SourceOpener,
    *,
    expected_files: Sequence[OfficialSourceFile] = OFFICIAL_SOURCE_FILES,
) -> None:
    metadata = _read_json_url(HF_REVISION_API_URL, source_opener)
    sha = metadata.get("sha")
    if sha != HF_REVISION:
        raise NomiraclKoError("Hugging Face metadata did not resolve to the pinned revision")
    if metadata.get("id") != HF_DATASET_REPO:
        raise NomiraclKoError("Hugging Face metadata did not identify miracl/nomiracl")
    if not _metadata_has_apache_license(metadata):
        raise NomiraclKoError("Hugging Face metadata does not advertise Apache-2.0")

    tree_payload = _read_json_any_url(HF_TREE_API_URL, source_opener)
    raw_entries = (
        tree_payload.get("value")
        if isinstance(tree_payload, dict) and "value" in tree_payload
        else tree_payload
    )
    if not isinstance(raw_entries, list):
        raise NomiraclKoError("Hugging Face tree metadata is not a list")
    by_path: dict[str, Mapping[str, Any]] = {}
    for item in raw_entries:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            by_path[str(item["path"])] = item
    for expected in expected_files:
        entry = by_path.get(expected.relative_path)
        if entry is None:
            raise NomiraclKoError(f"official NoMIRACL-ko file is missing: {expected.relative_path}")
        if entry.get("type") != "file":
            raise NomiraclKoError(
                f"official NoMIRACL-ko path is not a file: {expected.relative_path}"
            )
        if int(entry.get("size", -1)) != expected.size_bytes:
            raise NomiraclKoError(
                f"official NoMIRACL-ko file size mismatch: {expected.relative_path}"
            )
        lfs = entry.get("lfs")
        if (
            isinstance(lfs, dict)
            and expected.relative_path.endswith(".gz")
            and lfs.get("oid") != expected.sha256
        ):
            raise NomiraclKoError(
                f"official NoMIRACL-ko LFS digest mismatch: {expected.relative_path}"
            )


def _metadata_has_apache_license(metadata: Mapping[str, Any]) -> bool:
    tags = metadata.get("tags")
    if isinstance(tags, list) and "license:apache-2.0" in tags:
        return True
    card_data = metadata.get("cardData")
    if isinstance(card_data, dict):
        license_value = card_data.get("license")
        if license_value == "apache-2.0":
            return True
        if isinstance(license_value, list) and "apache-2.0" in license_value:
            return True
    return False


def _read_json_url(url: str, source_opener: SourceOpener) -> dict[str, Any]:
    payload = _read_json_any_url(url, source_opener)
    if not isinstance(payload, dict):
        raise NomiraclKoError(f"official NoMIRACL metadata from {url} must be a JSON object")
    return cast(dict[str, Any], payload)


def _read_json_any_url(url: str, source_opener: SourceOpener) -> Any:
    try:
        with contextlib.closing(source_opener(url)) as source:
            payload = json.loads(source.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NomiraclKoError(f"failed to read official NoMIRACL metadata from {url}") from error
    return payload


def _download_or_verify_file(
    destination: Path,
    expected: OfficialSourceFile,
    *,
    source_opener: SourceOpener,
) -> str:
    _validate_relative_source_path(expected.relative_path)
    if destination.exists():
        if not destination.is_file():
            raise NomiraclKoError(f"cached source path is not a file: {expected.relative_path}")
        _verify_file_digest_and_size(destination, expected)
        return "reused"

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _temporary_file_sibling(destination)
    try:
        url = f"{HF_RESOLVE_BASE_URL}/{quote(expected.relative_path, safe='/')}"
        with temp_path.open("wb") as target:
            with contextlib.closing(source_opener(url)) as source:
                while True:
                    chunk = source.read(DOWNLOAD_CHUNK_BYTES)
                    if chunk == b"":
                        break
                    if not isinstance(chunk, bytes):
                        raise NomiraclKoError("source stream must yield bytes")
                    target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        _verify_file_digest_and_size(temp_path, expected)
        os.replace(temp_path, destination)
        _fsync_parent_directory(destination)
        return "downloaded"
    except Exception:
        _remove_path(temp_path)
        raise


def _verify_file_digest_and_size(path: Path, expected: OfficialSourceFile) -> None:
    if path.stat().st_size != expected.size_bytes:
        raise NomiraclKoError(f"official NoMIRACL-ko file size mismatch: {expected.relative_path}")
    observed = hashlib.sha256(path.read_bytes()).hexdigest()
    if observed != expected.sha256:
        raise NomiraclKoError(
            f"official NoMIRACL-ko file SHA-256 mismatch: {expected.relative_path}"
        )


def validate_nomiracl_ko_source(
    source_dir: Path,
    *,
    expected_files: Sequence[OfficialSourceFile] = OFFICIAL_SOURCE_FILES,
    expected_counts: NomiraclKoSourceCounts = OFFICIAL_SOURCE_COUNTS,
    enforce_official_canonical_invariants: bool = True,
) -> NomiraclKoSourceData:
    """Validate local official-shape NoMIRACL-ko source files."""
    resolved = source_dir.expanduser().resolve(strict=False)
    for expected in expected_files:
        path = resolved / PurePosixPath(expected.relative_path)
        if not path.is_file():
            raise NomiraclKoError(f"missing NoMIRACL-ko source file: {expected.relative_path}")
        if enforce_official_canonical_invariants:
            _verify_file_digest_and_size(path, expected)

    documents, corpus_row_count = _read_corpus_gzip(
        resolved / "data" / "korean" / "corpus.jsonl.gz"
    )
    corpus_ids = frozenset(row.corpus_id for row in documents)
    relevant_queries = _read_topics_tsv(
        resolved / "data" / "korean" / "topics" / "dev.relevant.tsv"
    )
    non_relevant_queries = _read_topics_tsv(
        resolved / "data" / "korean" / "topics" / "dev.non_relevant.tsv"
    )
    relevant_qrels = _read_qrels_trec(
        resolved / "data" / "korean" / "qrels" / "dev.relevant.tsv",
        query_ids=frozenset(row.query_id for row in relevant_queries),
        corpus_ids=corpus_ids,
    )
    non_relevant_qrels = _read_qrels_trec(
        resolved / "data" / "korean" / "qrels" / "dev.non_relevant.tsv",
        query_ids=frozenset(row.query_id for row in non_relevant_queries),
        corpus_ids=corpus_ids,
    )
    counts = NomiraclKoSourceCounts(
        corpus_row_count=corpus_row_count,
        unique_document_count=len(documents),
        duplicate_corpus_row_count=corpus_row_count - len(documents),
        dev_relevant_query_count=len(relevant_queries),
        dev_relevant_qrel_count=len(relevant_qrels),
        dev_relevant_positive_qrel_count=sum(1 for row in relevant_qrels if row.relevance > 0),
        dev_non_relevant_query_count=len(non_relevant_queries),
        dev_non_relevant_qrel_count=len(non_relevant_qrels),
        dev_non_relevant_positive_qrel_count=sum(
            1 for row in non_relevant_qrels if row.relevance > 0
        ),
    )
    _require_positive_qrel_per_query(relevant_qrels)
    if any(row.relevance != 0 for row in non_relevant_qrels):
        raise NomiraclKoError("official dev.non_relevant qrels must all have relevance 0")
    _require_qrel_per_non_relevant_query(non_relevant_qrels, non_relevant_queries)
    if enforce_official_canonical_invariants:
        _require_official_counts(counts, expected_counts)
    return NomiraclKoSourceData(
        documents=tuple(documents),
        relevant_queries=tuple(relevant_queries),
        relevant_qrels=tuple(relevant_qrels),
        non_relevant_queries=tuple(non_relevant_queries),
        non_relevant_qrels=tuple(non_relevant_qrels),
        counts=counts,
    )


def materialize_nomiracl_ko(
    source_dir: Path,
    output_dir: Path,
    *,
    repo_root: Path = ROOT,
    non_relevant_sample_size: int = NON_RELEVANT_SAMPLE_SIZE,
    enforce_official_canonical_invariants: bool = True,
) -> MaterializeNomiraclKoResult:
    """Materialize NoMIRACL-ko judged-pool docs as Markdown and a local bundle."""
    if non_relevant_sample_size <= 0:
        raise NomiraclKoError("non_relevant_sample_size must be positive")
    resolved_source = source_dir.expanduser().resolve(strict=False)
    if not resolved_source.is_dir():
        raise NomiraclKoError("source_dir must exist and be a directory")
    resolved_output = _resolve_output_dir(
        output_dir, source_dir=resolved_source, repo_root=repo_root
    )
    source_before = _compute_tree_digest(resolved_source)
    source_data = validate_nomiracl_ko_source(
        resolved_source,
        enforce_official_canonical_invariants=enforce_official_canonical_invariants,
    )

    sample_queries = deterministic_non_relevant_sample(
        source_data.non_relevant_queries,
        sample_size=non_relevant_sample_size,
    )
    sample_query_ids = frozenset(row.query_id for row in sample_queries)
    sample_qrels = tuple(
        row for row in source_data.non_relevant_qrels if row.query_id in sample_query_ids
    )
    if len(sample_qrels) == 0:
        raise NomiraclKoError("non-relevant diagnostic sample has no qrels")
    selected_qrels = (*source_data.relevant_qrels, *sample_qrels)
    pool_documents = documents_for_evaluation_pool(source_data.documents, selected_qrels)
    evaluation_pool = build_evaluation_pool_summary(
        documents=pool_documents,
        relevant_queries=source_data.relevant_queries,
        relevant_qrels=source_data.relevant_qrels,
        non_relevant_sample_queries=sample_queries,
        non_relevant_sample_qrels=sample_qrels,
        selected_qrels=selected_qrels,
    )

    wiki_dir = resolved_output / "wiki"
    bundle_dir = resolved_output / "bundle"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    _write_wiki_documents(wiki_dir, pool_documents)

    corpus_rows = [
        {
            "corpus_id": row.corpus_id,
            "metadata": {
                "dataset": DATASET_NAME,
                "language": LANGUAGE_CODE,
                "nomiracl_docid": row.corpus_id,
            },
            "text": row.text,
            "title": row.title,
        }
        for row in pool_documents
    ]
    query_rows = [
        _bundle_query_row(
            row,
            answerability="answerable",
            source_split="dev.relevant",
            evaluation_split="holdout",
            label_source="nomiracl-ko-dev-relevant-qrels",
            tags=("retrieval", "korean", "judged-pool"),
        )
        for row in source_data.relevant_queries
    ]
    query_rows.extend(
        _bundle_query_row(
            row,
            answerability="unanswerable",
            source_split="dev.non_relevant",
            evaluation_split="smoke",
            label_source="nomiracl-ko-dev-non-relevant-zero-qrels-sample",
            tags=("diagnostic", "korean", "non-relevant", "judged-pool"),
        )
        for row in sample_queries
    )
    qrel_rows = [_bundle_qrel_row(row) for row in selected_qrels]

    _write_jsonl(bundle_dir / "corpus.jsonl", corpus_rows)
    _write_jsonl(bundle_dir / "queries.jsonl", query_rows)
    _write_jsonl(bundle_dir / "qrels.jsonl", qrel_rows)
    (bundle_dir / "evidence.jsonl").write_text("", encoding="utf-8", newline="\n")
    _write_json(
        bundle_dir / "provenance.json",
        _provenance_record(
            bundle_dir,
            evaluation_pool=evaluation_pool,
        ),
    )
    validator.validate_bundle(bundle_dir)

    source_after = _compute_tree_digest(resolved_source)
    if source_after != source_before:
        raise NomiraclKoError("source tree mutated during NoMIRACL-ko materialization")

    return MaterializeNomiraclKoResult(
        output_dir=resolved_output,
        wiki_dir=wiki_dir,
        bundle_dir=bundle_dir,
        source_before_sha256=source_before,
        source_after_sha256=source_after,
        source_counts=source_data.counts,
        evaluation_pool=evaluation_pool,
    )


def documents_for_evaluation_pool(
    documents: Sequence[SourceCorpusRow],
    selected_qrels: Sequence[SourceQrelRow],
) -> tuple[SourceCorpusRow, ...]:
    """Return only corpus documents referenced by the selected judged qrels."""
    pool_doc_ids = frozenset(row.corpus_id for row in selected_qrels)
    if not pool_doc_ids:
        raise NomiraclKoError("NoMIRACL-ko evaluation pool must contain qrel documents")
    documents_by_id = {row.corpus_id: row for row in documents}
    missing = sorted(pool_doc_ids - frozenset(documents_by_id))
    if missing:
        raise NomiraclKoError("selected NoMIRACL-ko qrels reference missing corpus documents")
    return tuple(documents_by_id[corpus_id] for corpus_id in sorted(pool_doc_ids))


def build_evaluation_pool_summary(
    *,
    documents: Sequence[SourceCorpusRow],
    relevant_queries: Sequence[SourceQueryRow],
    relevant_qrels: Sequence[SourceQrelRow],
    non_relevant_sample_queries: Sequence[SourceQueryRow],
    non_relevant_sample_qrels: Sequence[SourceQrelRow],
    selected_qrels: Sequence[SourceQrelRow],
) -> NomiraclKoEvaluationPool:
    document_ids = tuple(row.corpus_id for row in documents)
    query_ids = tuple(row.query_id for row in (*relevant_queries, *non_relevant_sample_queries))
    return NomiraclKoEvaluationPool(
        protocol=EVALUATION_PROTOCOL,
        full_corpus=FULL_CORPUS_EVALUATED,
        document_count=len(document_ids),
        document_ids_sha256=document_ids_sha256(document_ids),
        pool_sha256=evaluation_pool_sha256(
            document_ids=document_ids,
            query_ids=query_ids,
            qrels=selected_qrels,
        ),
        query_count=len(query_ids),
        qrel_count=len(selected_qrels),
        relevant_query_count=len(relevant_queries),
        relevant_qrel_count=len(relevant_qrels),
        positive_qrel_count=sum(1 for row in relevant_qrels if row.relevance > 0),
        non_relevant_sample_query_count=len(non_relevant_sample_queries),
        non_relevant_sample_qrel_count=len(non_relevant_sample_qrels),
        non_relevant_sample_query_ids_sha256=sample_query_ids_sha256(non_relevant_sample_queries),
        qrel_rows_sha256=qrel_rows_sha256(selected_qrels),
    )


def document_ids_sha256(document_ids: Sequence[str]) -> str:
    payload = "\n".join(sorted(document_ids)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def qrel_rows_sha256(qrels: Sequence[SourceQrelRow]) -> str:
    payload = "".join(
        json.dumps(_bundle_qrel_row(row), ensure_ascii=False, sort_keys=True) + "\n"
        for row in qrels
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def evaluation_pool_sha256(
    *,
    document_ids: Sequence[str],
    query_ids: Sequence[str],
    qrels: Sequence[SourceQrelRow],
) -> str:
    payload = {
        "document_ids": sorted(document_ids),
        "full_corpus": FULL_CORPUS_EVALUATED,
        "protocol": EVALUATION_PROTOCOL,
        "qrels": [_bundle_qrel_row(row) for row in qrels],
        "query_ids": list(query_ids),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def deterministic_non_relevant_sample(
    queries: Sequence[SourceQueryRow],
    *,
    sample_size: int = NON_RELEVANT_SAMPLE_SIZE,
) -> tuple[SourceQueryRow, ...]:
    if sample_size <= 0:
        raise NomiraclKoError("sample_size must be positive")
    if len(queries) < sample_size:
        raise NomiraclKoError("not enough non-relevant queries for deterministic sample")
    return tuple(
        sorted(
            queries,
            key=lambda row: (
                hashlib.sha256(f"{NON_RELEVANT_SAMPLE_SEED}\0{row.query_id}".encode()).hexdigest(),
                row.query_id,
            ),
        )[:sample_size]
    )


def sample_query_ids_sha256(queries: Sequence[SourceQueryRow]) -> str:
    payload = "\n".join(row.query_id for row in queries).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _read_corpus_gzip(path: Path) -> tuple[list[SourceCorpusRow], int]:
    if not path.is_file():
        raise NomiraclKoError("data/korean/corpus.jsonl.gz must exist")
    rows_by_id: dict[str, SourceCorpusRow] = {}
    row_count = 0
    try:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                row_count += 1
                label = f"corpus.jsonl.gz:{line_number}"
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError as error:
                    raise NomiraclKoError(f"{label}: invalid JSONL") from error
                if not isinstance(parsed, dict):
                    raise NomiraclKoError(f"{label}: JSONL row must be an object")
                _require_exact_fields(parsed, {"docid", "title", "text"}, label)
                corpus_id = _require_string(parsed, "docid", label)
                title = _require_string(parsed, "title", label)
                text = _require_string(parsed, "text", label)
                current = SourceCorpusRow(corpus_id=corpus_id, title=title, text=text)
                existing = rows_by_id.get(corpus_id)
                if existing is not None and existing != current:
                    raise NomiraclKoError(f"{label}: duplicate docid has conflicting text")
                rows_by_id.setdefault(corpus_id, current)
    except OSError as error:
        raise NomiraclKoError("failed to read NoMIRACL-ko corpus gzip") from error
    if not rows_by_id:
        raise NomiraclKoError("NoMIRACL-ko corpus must contain at least one document")
    return sorted(rows_by_id.values(), key=lambda row: row.corpus_id), row_count


def _read_topics_tsv(path: Path) -> list[SourceQueryRow]:
    rows: list[SourceQueryRow] = []
    seen: set[str] = set()
    for line_number, line in _read_text_lines(path):
        parts = line.split("\t", 1)
        label = f"{path.name}:{line_number}"
        if len(parts) != 2:
            raise NomiraclKoError(f"{label}: topic row must have qid and query")
        query_id, text = parts
        if not query_id or not text:
            raise NomiraclKoError(f"{label}: qid and query must be non-empty")
        if query_id in seen:
            raise NomiraclKoError(f"{label}: duplicate query id {query_id!r}")
        seen.add(query_id)
        rows.append(SourceQueryRow(query_id=query_id, text=text))
    if not rows:
        raise NomiraclKoError(f"{path.name} must contain at least one topic")
    return rows


def _read_qrels_trec(
    path: Path,
    *,
    query_ids: frozenset[str],
    corpus_ids: frozenset[str],
) -> list[SourceQrelRow]:
    rows: list[SourceQrelRow] = []
    seen_pairs: set[tuple[str, str]] = set()
    for line_number, line in _read_text_lines(path):
        label = f"{path.name}:{line_number}"
        parts = line.split()
        if len(parts) != 4:
            raise NomiraclKoError(f"{label}: qrel row must have 4 TREC columns")
        query_id, _unused, corpus_id, relevance_text = parts
        if query_id not in query_ids:
            raise NomiraclKoError(f"{label}: qrel references unknown query_id {query_id!r}")
        if corpus_id not in corpus_ids:
            raise NomiraclKoError(f"{label}: qrel references unknown corpus_id {corpus_id!r}")
        pair = (query_id, corpus_id)
        if pair in seen_pairs:
            raise NomiraclKoError(f"{label}: duplicate qrel pair")
        seen_pairs.add(pair)
        rows.append(
            SourceQrelRow(
                query_id=query_id,
                corpus_id=corpus_id,
                relevance=_parse_relevance(relevance_text, label),
            )
        )
    if not rows:
        raise NomiraclKoError(f"{path.name} must contain at least one qrel")
    return rows


def _read_text_lines(path: Path) -> list[tuple[int, str]]:
    if not path.is_file():
        raise NomiraclKoError(f"{path.name} must exist")
    try:
        return [
            (line_number, line)
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if line.strip()
        ]
    except UnicodeDecodeError as error:
        raise NomiraclKoError(f"{path.name} must be UTF-8 text") from error


def _parse_relevance(value: str, label: str) -> int | float:
    try:
        parsed: int | float = int(value) if re.fullmatch(r"[+-]?\d+", value) else float(value)
    except ValueError as error:
        raise NomiraclKoError(f"{label}: relevance must be numeric") from error
    if isinstance(parsed, float) and not math.isfinite(parsed):
        raise NomiraclKoError(f"{label}: relevance must be finite")
    if parsed < 0:
        raise NomiraclKoError(f"{label}: relevance must be non-negative")
    return parsed


def _require_exact_fields(record: Mapping[str, Any], fields: set[str], label: str) -> None:
    missing = sorted(fields - set(record))
    unknown = sorted(set(record) - fields)
    if missing:
        raise NomiraclKoError(f"{label}: missing required fields: {missing}")
    if unknown:
        raise NomiraclKoError(f"{label}: unknown fields: {unknown}")


def _require_string(record: Mapping[str, Any], field: str, label: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise NomiraclKoError(f"{label}.{field} must be a non-empty string")
    return value


def _require_positive_qrel_per_query(qrels: Sequence[SourceQrelRow]) -> None:
    by_query: dict[str, bool] = {}
    for qrel in qrels:
        by_query.setdefault(qrel.query_id, False)
        if qrel.relevance > 0:
            by_query[qrel.query_id] = True
    missing = sorted(query_id for query_id, has_positive in by_query.items() if not has_positive)
    if missing:
        raise NomiraclKoError("every dev.relevant query must have a positive qrel")


def _require_qrel_per_non_relevant_query(
    qrels: Sequence[SourceQrelRow],
    queries: Sequence[SourceQueryRow],
) -> None:
    counts = Counter(row.query_id for row in qrels)
    missing = sorted(row.query_id for row in queries if counts[row.query_id] == 0)
    if missing:
        raise NomiraclKoError("every dev.non_relevant query must have at least one qrel")


def _require_official_counts(
    observed: NomiraclKoSourceCounts,
    expected: NomiraclKoSourceCounts,
) -> None:
    failures = [
        f"{field}={getattr(observed, field)} expected {getattr(expected, field)}"
        for field in (item.name for item in fields(expected))
        if getattr(observed, field) != getattr(expected, field)
    ]
    if failures:
        raise NomiraclKoError("official NoMIRACL-ko invariants failed: " + "; ".join(failures))


def _write_wiki_documents(wiki_dir: Path, documents: Sequence[SourceCorpusRow]) -> None:
    used_names: set[str] = set()
    for document in documents:
        file_name = _wiki_file_name(document.corpus_id)
        if file_name.casefold() in used_names:
            raise NomiraclKoError("stable NoMIRACL-ko filename collision")
        used_names.add(file_name.casefold())
        if file_name in ORIENTATION_FILE_NAMES:
            raise NomiraclKoError("NoMIRACL-ko materializer must not create orientation pages")
        (wiki_dir / file_name).write_text(_wiki_markdown(document), encoding="utf-8", newline="\n")


def _wiki_file_name(corpus_id: str) -> str:
    digest = hashlib.sha256(corpus_id.encode("utf-8")).hexdigest()[:20]
    return f"doc-{digest}.md"


def _wiki_markdown(document: SourceCorpusRow) -> str:
    return (
        "---\n"
        f"original_id: {json.dumps(document.corpus_id, ensure_ascii=False)}\n"
        f"title: {json.dumps(document.title, ensure_ascii=False)}\n"
        f"dataset: {json.dumps(DATASET_NAME)}\n"
        f"language: {json.dumps(LANGUAGE_CODE)}\n"
        "review_state: approved\n"
        "---\n\n"
        f"# {_markdown_heading(document.title)}\n\n"
        f"{document.text.rstrip()}\n"
    )


def _markdown_heading(title: str) -> str:
    return " ".join(title.split()) or "Untitled"


def _bundle_query_row(
    row: SourceQueryRow,
    *,
    answerability: str,
    source_split: str,
    evaluation_split: str,
    label_source: str,
    tags: Sequence[str],
) -> dict[str, object]:
    return {
        "answerability": answerability,
        "answers": [],
        "evaluation_split": evaluation_split,
        "label_source": label_source,
        "query": row.text,
        "query_id": row.query_id,
        "source_split": source_split,
        "tags": list(tags),
    }


def _bundle_qrel_row(row: SourceQrelRow) -> dict[str, object]:
    return {
        "corpus_id": row.corpus_id,
        "query_id": row.query_id,
        "relevance": row.relevance,
    }


def _provenance_record(
    bundle_dir: Path,
    *,
    evaluation_pool: NomiraclKoEvaluationPool,
) -> dict[str, object]:
    return {
        "adapter": {"name": ADAPTER_NAME, "version": ADAPTER_VERSION},
        "benchmark_protocol": EVALUATION_PROTOCOL,
        "bundle_id": DATASET_NAME,
        "checksums": {
            file_name: f"sha256:{validator.canonical_text_file_sha256(bundle_dir / file_name)}"
            for file_name in validator.BUNDLE_JSONL_FILES
        },
        "component_licenses": [
            {
                "attribution": "NoMIRACL, Thakur et al. 2024; MIRACL project",
                "component": "NoMIRACL Korean judged-pool topics, qrels, and passages",
                "license_spdx": "Apache-2.0",
                "license_url": HF_DATASET_URL,
                "license_verified_date": LICENSE_VERIFIED_DATE,
                "public_report_policy": "allowed-with-attribution",
                "redistribution_policy": "derived-metrics-only",
            }
        ],
        "dataset": DATASET_NAME,
        "evaluation_pool": evaluation_pool.as_public_json(),
        "full_corpus": FULL_CORPUS_EVALUATED,
        "official_full_corpus": OFFICIAL_SOURCE_COUNTS.as_public_json(),
        "schema_id": validator.SCHEMA_ID,
        "source_release": (
            "Hugging Face miracl/nomiracl Korean dev judged pool; "
            f"official GitHub project {GITHUB_REPO_URL}"
        ),
        "source_revision": f"git:{HF_REVISION}",
        "source_url": HF_DATASET_URL,
    }


def _public_cli_success_metadata(result: MaterializeNomiraclKoResult) -> dict[str, object]:
    return {
        "adapter": {"name": ADAPTER_NAME, "version": ADAPTER_VERSION},
        "bundle_created": True,
        "corpus_count": result.corpus_count,
        "dataset": DATASET_NAME,
        "evaluation_pool": result.evaluation_pool.as_public_json(),
        "full_corpus": result.evaluation_pool.full_corpus,
        "local_outputs": {
            "bundle_dir": "local-only",
            "output_dir": "local-only",
            "wiki_dir": "local-only",
        },
        "non_relevant_sample": {
            "qrel_count": result.non_relevant_sample_qrel_count,
            "query_count": result.non_relevant_sample_query_count,
            "query_ids_sha256": f"sha256:{result.non_relevant_sample_query_ids_sha256}",
            "rule": non_relevant_sample_rule(),
        },
        "positive_qrel_count": result.positive_qrel_count,
        "protocol": result.evaluation_pool.protocol,
        "qrel_count": result.qrel_count,
        "query_count": result.query_count,
        "relevant_qrel_count": result.relevant_qrel_count,
        "relevant_query_count": result.relevant_query_count,
        "source_counts": result.source_counts.as_public_json(),
        "source_mutated": result.source_mutated,
    }


def non_relevant_sample_rule() -> str:
    return (
        "sort dev.non_relevant query ids by sha256("
        f"{NON_RELEVANT_SAMPLE_SEED!r} + NUL + query_id), then query_id; "
        f"take first {NON_RELEVANT_SAMPLE_SIZE}"
    )


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
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


def _resolve_output_dir(output_dir: Path, *, source_dir: Path, repo_root: Path) -> Path:
    resolved_output = output_dir.expanduser().resolve(strict=False)
    if _is_same_or_nested(resolved_output, source_dir):
        raise NomiraclKoError("output_dir must be outside source_dir")
    if _is_same_or_nested(source_dir, resolved_output):
        raise NomiraclKoError("source_dir must be outside output_dir")
    _validate_local_workspace_path(resolved_output, repo_root=repo_root, label="output_dir")
    if resolved_output.exists() and any(resolved_output.iterdir()):
        raise NomiraclKoError("output_dir already exists and is not empty")
    return resolved_output


def _validate_local_workspace_path(path: Path, *, repo_root: Path, label: str) -> None:
    resolved_repo = repo_root.expanduser().resolve(strict=False)
    try:
        relative = path.relative_to(resolved_repo)
    except ValueError:
        return
    if relative.parts[:2] == (validator.DEFAULT_WORKSPACE_NAME, "benchmark-adapters"):
        return
    raise NomiraclKoError(
        f"{label} inside the repository must stay under "
        f"{validator.DEFAULT_BENCHMARK_WORKSPACE.as_posix()}/"
    )


def _validate_relative_source_path(value: str) -> None:
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not value
        or posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or "\\" in value
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise NomiraclKoError(f"unsafe official source path: {value!r}")


def _prepare_directory(path: Path) -> None:
    if path.exists() and not path.is_dir():
        raise NomiraclKoError("source_dir must be a directory")
    path.mkdir(parents=True, exist_ok=True)


def _combined_cache_status(statuses: Sequence[str]) -> str:
    unique = set(statuses)
    if unique == {"reused"}:
        return "reused"
    if unique == {"downloaded"}:
        return "downloaded"
    return "mixed"


def _write_acquire_run_manifest(
    path: Path,
    result: AcquiredNomiraclKoResult,
    *,
    repo_root: Path,
) -> None:
    resolved = path.expanduser().resolve(strict=False)
    try:
        validator.validate_local_run_manifest(resolved, repo_root=repo_root)
    except validator.BundleValidationError as error:
        raise NomiraclKoError(str(error)) from error
    payload = {
        "local_paths": {"source_dir": str(result.source_dir)},
        "public": result.as_public_json(),
        "schema_id": RUN_MANIFEST_SCHEMA_ID,
    }
    _atomic_json_write(resolved, payload)


def _atomic_json_write(path: Path, payload: Mapping[str, object]) -> None:
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
        _remove_path(temp_path)
        raise


def _temporary_file_sibling(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    with contextlib.suppress(OSError):
        path.unlink()


def _is_same_or_nested(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Acquire or materialize official NoMIRACL Korean judged-pool data."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    acquire_parser = subparsers.add_parser("acquire", help="download and validate source files")
    acquire_parser.add_argument("--source-dir", type=Path, required=True)
    acquire_parser.add_argument("--run-manifest", type=Path)
    acquire_parser.add_argument("--repo-root", type=Path, default=ROOT)
    acquire_parser.add_argument(
        "--skip-hf-metadata",
        action="store_true",
        help="Skip HF API metadata verification; file digests/counts are still enforced.",
    )

    materialize_parser = subparsers.add_parser(
        "materialize",
        help="materialize validated source files as Markdown and a benchmark bundle",
    )
    materialize_parser.add_argument("--source-dir", type=Path, required=True)
    materialize_parser.add_argument("--output-dir", type=Path, required=True)
    materialize_parser.add_argument("--repo-root", type=Path, default=ROOT)
    return parser.parse_args(argv)


def run(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "acquire":
            acquire_result = acquire_nomiracl_ko(
                AcquireNomiraclKoConfig(
                    source_dir=cast(Path, args.source_dir),
                    run_manifest_path=cast(Path | None, args.run_manifest),
                    repo_root=cast(Path, args.repo_root),
                    verify_hf_metadata=not bool(args.skip_hf_metadata),
                )
            )
            print(json.dumps(acquire_result.as_public_json(), indent=2, sort_keys=True))
            return 0
        if args.command == "materialize":
            materialize_result = materialize_nomiracl_ko(
                cast(Path, args.source_dir),
                cast(Path, args.output_dir),
                repo_root=cast(Path, args.repo_root),
            )
            print(
                json.dumps(
                    _public_cli_success_metadata(materialize_result),
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
    except (
        NomiraclKoError,
        OSError,
        ValueError,
        validator.BundleValidationError,
    ) as error:
        print(f"nomiracl-ko adapter failed: {error}", file=sys.stderr)
        return 2
    raise AssertionError(f"unhandled command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ADAPTER_NAME",
    "ADAPTER_VERSION",
    "DATASET_NAME",
    "EVALUATION_PROTOCOL",
    "HF_DATASET_REPO",
    "HF_REVISION",
    "HF_DATASET_URL",
    "FULL_CORPUS_EVALUATED",
    "LANGUAGE_CODE",
    "NON_RELEVANT_SAMPLE_SEED",
    "NON_RELEVANT_SAMPLE_SIZE",
    "OFFICIAL_SOURCE_COUNTS",
    "OFFICIAL_SOURCE_FILES",
    "AcquireNomiraclKoConfig",
    "AcquiredNomiraclKoResult",
    "MaterializeNomiraclKoResult",
    "NomiraclKoError",
    "NomiraclKoEvaluationPool",
    "NomiraclKoSourceCounts",
    "OfficialSourceFile",
    "SourceCorpusRow",
    "SourceQrelRow",
    "SourceQueryRow",
    "acquire_nomiracl_ko",
    "deterministic_non_relevant_sample",
    "document_ids_sha256",
    "documents_for_evaluation_pool",
    "evaluation_pool_sha256",
    "main",
    "materialize_nomiracl_ko",
    "non_relevant_sample_rule",
    "qrel_rows_sha256",
    "run",
    "sample_query_ids_sha256",
    "validate_nomiracl_ko_source",
    "verify_hf_revision_and_tree",
]
