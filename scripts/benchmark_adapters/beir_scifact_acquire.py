"""Acquire the official BEIR SciFact archive into a safe local dataset root."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import ssl
import stat
import sys
import tempfile
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO, cast
from urllib.request import urlopen

import certifi

from scripts.benchmark_adapters import bundle_validator as validator

ROOT = Path(__file__).resolve().parents[2]
SOURCE_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"
PUBLISHED_MD5 = "5f7d1de60b170fc8027bb7898e2efca1"
ARCHIVE_FILENAME = "scifact.zip"
EXPECTED_LAYOUT = ("corpus.jsonl", "queries.jsonl", "qrels/test.tsv")
DEFAULT_MAX_EXTRACTED_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_FILE_COUNT = 10_000
DEFAULT_MAX_COMPRESSION_RATIO = 100.0
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
RUN_MANIFEST_SCHEMA_ID = "llmwiki-beir-scifact-acquire-run-v1"
UNIX_FILE_TYPE_MASK = 0o170000

SourceOpener = Callable[[str], BinaryIO]


class BeirScifactAcquireError(RuntimeError):
    """Raised when official BEIR SciFact acquisition cannot proceed safely."""


@dataclass(frozen=True)
class AcquireBeirScifactConfig:
    cache_dir: Path
    extract_dir: Path
    run_manifest_path: Path | None = None
    repo_root: Path = ROOT
    max_extracted_bytes: int = DEFAULT_MAX_EXTRACTED_BYTES
    max_file_count: int = DEFAULT_MAX_FILE_COUNT
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO


@dataclass(frozen=True)
class ArchiveDigest:
    md5: str
    sha256: str
    archive_bytes: int


@dataclass(frozen=True)
class ZipExtractionPlan:
    members: tuple[ZipMember, ...]
    file_count: int
    extracted_bytes: int


@dataclass(frozen=True)
class ZipMember:
    info: zipfile.ZipInfo
    relative_path: PurePosixPath
    is_dir: bool


@dataclass(frozen=True)
class AcquiredBeirScifactResult:
    cache_path: Path
    extract_dir: Path
    dataset_root: Path
    source_url: str
    published_md5: str
    observed_md5: str
    sha256: str
    archive_bytes: int
    file_count: int
    extracted_bytes: int
    cache_status: str
    dataset_root_handle: str

    def as_public_json(self) -> dict[str, object]:
        """Return path-free acquisition metadata suitable for public reports."""
        return {
            "archive_bytes": self.archive_bytes,
            "cache_status": self.cache_status,
            "dataset_root": self.dataset_root_handle,
            "extracted_bytes": self.extracted_bytes,
            "file_count": self.file_count,
            "observed_md5": self.observed_md5,
            "published_md5": self.published_md5,
            "sha256": self.sha256,
            "source_url": self.source_url,
        }


def acquire_beir_scifact(
    config: AcquireBeirScifactConfig,
    *,
    source_opener: SourceOpener | None = None,
) -> AcquiredBeirScifactResult:
    """Download/cache and securely extract the official BEIR SciFact archive."""
    opener = source_opener or default_source_opener
    resolved_cache_dir = config.cache_dir.expanduser().resolve()
    resolved_extract_dir = config.extract_dir.expanduser().resolve()
    _validate_config(config, cache_dir=resolved_cache_dir, extract_dir=resolved_extract_dir)
    _require_empty_or_missing_target(resolved_extract_dir)
    _prepare_cache_dir(resolved_cache_dir)

    cache_path = resolved_cache_dir / ARCHIVE_FILENAME
    if cache_path.exists():
        digest = _hash_cached_archive(cache_path)
        _require_expected_md5(digest.md5, label="cached archive")
        cache_status = "reused"
    else:
        digest = _download_official_archive(cache_path, source_opener=opener)
        cache_status = "downloaded"

    extraction = _extract_archive_to_target(
        cache_path,
        resolved_extract_dir,
        max_extracted_bytes=config.max_extracted_bytes,
        max_file_count=config.max_file_count,
        max_compression_ratio=config.max_compression_ratio,
    )
    dataset_root = (
        resolved_extract_dir
        if extraction.dataset_root_handle == "."
        else resolved_extract_dir / extraction.dataset_root_handle
    )
    result = AcquiredBeirScifactResult(
        cache_path=cache_path,
        extract_dir=resolved_extract_dir,
        dataset_root=dataset_root,
        source_url=SOURCE_URL,
        published_md5=PUBLISHED_MD5,
        observed_md5=digest.md5,
        sha256=digest.sha256,
        archive_bytes=digest.archive_bytes,
        file_count=extraction.file_count,
        extracted_bytes=extraction.extracted_bytes,
        cache_status=cache_status,
        dataset_root_handle=extraction.dataset_root_handle,
    )
    if config.run_manifest_path is not None:
        _write_run_manifest(config.run_manifest_path, result, repo_root=config.repo_root)
    return result


@dataclass(frozen=True)
class ExtractionResult:
    file_count: int
    extracted_bytes: int
    dataset_root_handle: str


def default_source_opener(url: str) -> BinaryIO:
    """Open the official source URL with a bounded connection timeout."""
    context = ssl.create_default_context(cafile=certifi.where())
    return cast(BinaryIO, urlopen(url, timeout=60, context=context))


def _validate_config(
    config: AcquireBeirScifactConfig,
    *,
    cache_dir: Path,
    extract_dir: Path,
) -> None:
    if not re.fullmatch(r"[a-f0-9]{32}", PUBLISHED_MD5):
        raise BeirScifactAcquireError(
            "published BEIR MD5 must be a 32-character lowercase hex hash"
        )
    if config.max_extracted_bytes <= 0:
        raise BeirScifactAcquireError("max_extracted_bytes must be positive")
    if config.max_file_count <= 0:
        raise BeirScifactAcquireError("max_file_count must be positive")
    if config.max_compression_ratio <= 0:
        raise BeirScifactAcquireError("max_compression_ratio must be positive")
    if _is_same_or_nested(cache_dir, extract_dir) or _is_same_or_nested(extract_dir, cache_dir):
        raise BeirScifactAcquireError("cache_dir and extract_dir must be separate non-nested paths")
    repo_root = config.repo_root.expanduser().resolve()
    _require_allowed_repo_workspace(cache_dir, repo_root=repo_root, label="cache_dir")
    _require_allowed_repo_workspace(extract_dir, repo_root=repo_root, label="extract_dir")


def _prepare_cache_dir(cache_dir: Path) -> None:
    if cache_dir.exists() and not cache_dir.is_dir():
        raise BeirScifactAcquireError("cache_dir must be a directory")
    cache_dir.mkdir(parents=True, exist_ok=True)


def _require_empty_or_missing_target(target: Path) -> None:
    if target.exists() and not target.is_dir():
        raise BeirScifactAcquireError("extract_dir must be a directory when it already exists")
    if target.exists() and any(target.iterdir()):
        raise BeirScifactAcquireError("extract_dir already exists and is not empty")


def _download_official_archive(cache_path: Path, *, source_opener: SourceOpener) -> ArchiveDigest:
    md5 = hashlib.md5(usedforsecurity=False)
    sha256 = hashlib.sha256()
    archive_bytes = 0
    temp_path: Path | None = None
    try:
        temp_path = _temporary_file_sibling(cache_path)
        with temp_path.open("wb") as target:
            with contextlib.closing(source_opener(SOURCE_URL)) as source:
                while True:
                    chunk = source.read(DOWNLOAD_CHUNK_BYTES)
                    if chunk == b"":
                        break
                    if not isinstance(chunk, bytes):
                        raise BeirScifactAcquireError("source stream must yield bytes")
                    archive_bytes += len(chunk)
                    md5.update(chunk)
                    sha256.update(chunk)
                    target.write(chunk)
            target.flush()
            os.fsync(target.fileno())

        observed_md5 = md5.hexdigest()
        _require_expected_md5(observed_md5, label="downloaded archive")
        os.replace(temp_path, cache_path)
        _fsync_parent_directory(cache_path)
        return ArchiveDigest(
            md5=observed_md5,
            sha256=sha256.hexdigest(),
            archive_bytes=archive_bytes,
        )
    except BaseException as error:
        if temp_path is not None:
            _remove_path(temp_path)
        if not isinstance(error, Exception):
            raise
        if isinstance(error, BeirScifactAcquireError):
            raise
        raise BeirScifactAcquireError(
            "failed to download official BEIR SciFact archive "
            f"from {SOURCE_URL}: {type(error).__name__}: {error}"
        ) from error


def _hash_cached_archive(cache_path: Path) -> ArchiveDigest:
    md5 = hashlib.md5(usedforsecurity=False)
    sha256 = hashlib.sha256()
    archive_bytes = 0
    try:
        if not cache_path.is_file():
            raise BeirScifactAcquireError("cached archive path exists but is not a regular file")
        with cache_path.open("rb") as handle:
            while True:
                chunk = handle.read(DOWNLOAD_CHUNK_BYTES)
                if chunk == b"":
                    break
                archive_bytes += len(chunk)
                md5.update(chunk)
                sha256.update(chunk)
    except BaseException as error:
        if not isinstance(error, Exception):
            raise
        if isinstance(error, BeirScifactAcquireError):
            raise
        raise BeirScifactAcquireError(
            f"failed to read cached BEIR SciFact archive {ARCHIVE_FILENAME}: "
            f"{type(error).__name__}: {error}"
        ) from error
    return ArchiveDigest(
        md5=md5.hexdigest(), sha256=sha256.hexdigest(), archive_bytes=archive_bytes
    )


def _require_expected_md5(observed_md5: str, *, label: str) -> None:
    if observed_md5 != PUBLISHED_MD5:
        raise BeirScifactAcquireError(
            f"{label} MD5 mismatch; expected official BEIR MD5 {PUBLISHED_MD5}, "
            f"observed {observed_md5}"
        )


def _extract_archive_to_target(
    archive_path: Path,
    target: Path,
    *,
    max_extracted_bytes: int,
    max_file_count: int,
    max_compression_ratio: float,
) -> ExtractionResult:
    _require_empty_or_missing_target(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(
        tempfile.mkdtemp(
            dir=target.parent,
            prefix=f".{target.name}.extract-",
        )
    )
    published = False
    try:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                plan = _validate_zip_plan(
                    archive.infolist(),
                    max_extracted_bytes=max_extracted_bytes,
                    max_file_count=max_file_count,
                    max_compression_ratio=max_compression_ratio,
                )
                _extract_members(archive, plan.members, temp_dir)
                dataset_root_handle = _find_dataset_root_handle(temp_dir)
        except BeirScifactAcquireError:
            raise
        except (RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
            raise BeirScifactAcquireError("cached archive is not a readable ZIP") from error

        if target.exists():
            target.rmdir()
        temp_dir.replace(target)
        _fsync_parent_directory(target)
        published = True
        return ExtractionResult(
            file_count=plan.file_count,
            extracted_bytes=plan.extracted_bytes,
            dataset_root_handle=dataset_root_handle,
        )
    except Exception:
        if not published:
            _remove_path(temp_dir)
        raise


def _validate_zip_plan(
    infos: Sequence[zipfile.ZipInfo],
    *,
    max_extracted_bytes: int,
    max_file_count: int,
    max_compression_ratio: float,
) -> ZipExtractionPlan:
    seen_paths: set[str] = set()
    file_paths: set[str] = set()
    members: list[ZipMember] = []
    file_count = 0
    extracted_bytes = 0

    for info in infos:
        relative_path = _validated_zip_relative_path(info)
        key = relative_path.as_posix().casefold()
        if key in seen_paths:
            raise BeirScifactAcquireError(
                f"zip entry {info.filename!r} duplicates or case-collides with another entry"
            )
        seen_paths.add(key)

        _validate_zip_entry_type(info)
        if info.flag_bits & 0x1:
            raise BeirScifactAcquireError(f"zip entry {info.filename!r} is encrypted")

        is_dir = info.is_dir()
        if not is_dir:
            file_count += 1
            extracted_bytes += info.file_size
            if file_count > max_file_count:
                raise BeirScifactAcquireError("zip file count exceeds configured limit")
            if extracted_bytes > max_extracted_bytes:
                raise BeirScifactAcquireError("zip uncompressed size exceeds configured limit")
            _validate_compression_ratio(info, max_compression_ratio=max_compression_ratio)
            file_paths.add(key)
        members.append(ZipMember(info=info, relative_path=relative_path, is_dir=is_dir))

    for key in file_paths:
        parts = key.split("/")
        for index in range(1, len(parts)):
            parent = "/".join(parts[:index])
            if parent in file_paths:
                raise BeirScifactAcquireError(
                    "zip contains a file entry that conflicts with a child path"
                )

    return ZipExtractionPlan(
        members=tuple(members),
        file_count=file_count,
        extracted_bytes=extracted_bytes,
    )


def _validated_zip_relative_path(info: zipfile.ZipInfo) -> PurePosixPath:
    raw_name = info.filename
    if not raw_name or "\x00" in raw_name:
        raise BeirScifactAcquireError("zip entry name must be non-empty text without NUL bytes")
    if "\\" in raw_name:
        raise BeirScifactAcquireError(f"zip entry {raw_name!r} must not contain backslashes")
    windows_path = PureWindowsPath(raw_name)
    if windows_path.drive or windows_path.is_absolute():
        raise BeirScifactAcquireError(
            f"zip entry {raw_name!r} must not use a Windows drive or UNC path"
        )
    if PurePosixPath(raw_name).is_absolute():
        raise BeirScifactAcquireError(f"zip entry {raw_name!r} must be relative")

    stripped = raw_name.rstrip("/")
    parts = stripped.split("/")
    if not stripped or any(part in {"", ".", ".."} for part in parts):
        raise BeirScifactAcquireError(f"zip entry {raw_name!r} contains unsafe path segments")
    return PurePosixPath(*parts)


def _validate_zip_entry_type(info: zipfile.ZipInfo) -> None:
    file_type = (info.external_attr >> 16) & UNIX_FILE_TYPE_MASK
    if file_type == 0:
        return
    if stat.S_ISLNK(file_type):
        raise BeirScifactAcquireError(f"zip entry {info.filename!r} is a symlink")
    if stat.S_ISDIR(file_type):
        if not info.is_dir():
            raise BeirScifactAcquireError(
                f"zip entry {info.filename!r} has inconsistent directory metadata"
            )
        return
    if stat.S_ISREG(file_type):
        if info.is_dir():
            raise BeirScifactAcquireError(
                f"zip entry {info.filename!r} has inconsistent file metadata"
            )
        return
    raise BeirScifactAcquireError(f"zip entry {info.filename!r} is not a regular file or directory")


def _validate_compression_ratio(
    info: zipfile.ZipInfo,
    *,
    max_compression_ratio: float,
) -> None:
    if info.file_size <= 0:
        return
    if info.compress_size <= 0:
        raise BeirScifactAcquireError(
            f"zip entry {info.filename!r} has a suspicious compression ratio"
        )
    ratio = info.file_size / info.compress_size
    if ratio > max_compression_ratio:
        raise BeirScifactAcquireError(
            f"zip entry {info.filename!r} has a suspicious compression ratio"
        )


def _extract_members(
    archive: zipfile.ZipFile,
    members: Sequence[ZipMember],
    target: Path,
) -> None:
    resolved_target = target.resolve()
    for member in members:
        destination = (target / member.relative_path.as_posix()).resolve()
        try:
            destination.relative_to(resolved_target)
        except ValueError as error:
            raise BeirScifactAcquireError(
                f"zip entry {member.info.filename!r} escapes extraction target"
            ) from error

        if member.is_dir:
            destination.mkdir(parents=True, exist_ok=True)
            continue

        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member.info, "r") as source, destination.open("xb") as output:
            shutil.copyfileobj(source, output, length=DOWNLOAD_CHUNK_BYTES)
            output.flush()
            os.fsync(output.fileno())


def _find_dataset_root_handle(extract_dir: Path) -> str:
    if _has_expected_layout(extract_dir):
        return "."
    children = sorted(extract_dir.iterdir(), key=lambda path: path.name.casefold())
    directories = [child for child in children if child.is_dir()]
    files = [child for child in children if child.is_file()]
    candidate_dirs = [directory for directory in directories if _has_expected_layout(directory)]
    if len(candidate_dirs) == 1 and not files and len(directories) == 1:
        return candidate_dirs[0].name
    raise BeirScifactAcquireError(
        "extracted archive must contain corpus.jsonl, queries.jsonl, and qrels/test.tsv "
        "at the root or under one top-level dataset directory"
    )


def _has_expected_layout(root: Path) -> bool:
    return all((root / PurePosixPath(relative)).is_file() for relative in EXPECTED_LAYOUT)


def _write_run_manifest(path: Path, result: AcquiredBeirScifactResult, *, repo_root: Path) -> None:
    manifest_path = path.expanduser().resolve()
    try:
        validator.validate_local_run_manifest(manifest_path, repo_root=repo_root)
    except validator.BundleValidationError as error:
        raise BeirScifactAcquireError(str(error)) from error

    resolved_repo = repo_root.expanduser().resolve()
    try:
        relative_to_repo = manifest_path.relative_to(resolved_repo)
    except ValueError:
        relative_to_repo = None
    if relative_to_repo is not None and relative_to_repo.parts[:2] != (
        validator.DEFAULT_WORKSPACE_NAME,
        "benchmark-adapters",
    ):
        raise BeirScifactAcquireError(
            "run-manifest.json inside the repository must stay under "
            f"{validator.DEFAULT_BENCHMARK_WORKSPACE.as_posix()}/"
        )

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "local_paths": {
            "cache_path": str(result.cache_path),
            "dataset_root": str(result.dataset_root),
            "extract_dir": str(result.extract_dir),
        },
        "public": result.as_public_json(),
        "schema_id": RUN_MANIFEST_SCHEMA_ID,
    }
    _atomic_write_json(manifest_path, payload)


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
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


def _require_allowed_repo_workspace(path: Path, *, repo_root: Path, label: str) -> None:
    try:
        relative_to_repo = path.relative_to(repo_root)
    except ValueError:
        return
    if relative_to_repo.parts[:2] == (validator.DEFAULT_WORKSPACE_NAME, "benchmark-adapters"):
        return
    raise BeirScifactAcquireError(
        f"{label} inside the repository must stay under "
        f"{validator.DEFAULT_BENCHMARK_WORKSPACE.as_posix()}/"
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Acquire and safely extract the official BEIR SciFact archive."
    )
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--extract-dir", type=Path, required=True)
    parser.add_argument("--run-manifest", type=Path)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--max-extracted-bytes", type=int, default=DEFAULT_MAX_EXTRACTED_BYTES)
    parser.add_argument("--max-file-count", type=int, default=DEFAULT_MAX_FILE_COUNT)
    parser.add_argument(
        "--max-compression-ratio", type=float, default=DEFAULT_MAX_COMPRESSION_RATIO
    )
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> AcquireBeirScifactConfig:
    return AcquireBeirScifactConfig(
        cache_dir=args.cache_dir,
        extract_dir=args.extract_dir,
        run_manifest_path=args.run_manifest,
        repo_root=args.repo_root,
        max_extracted_bytes=int(args.max_extracted_bytes),
        max_file_count=int(args.max_file_count),
        max_compression_ratio=float(args.max_compression_ratio),
    )


def run(
    argv: Sequence[str] | None = None,
    *,
    source_opener: SourceOpener | None = None,
) -> int:
    try:
        result = acquire_beir_scifact(
            config_from_args(parse_args(argv)), source_opener=source_opener
        )
    except BeirScifactAcquireError as error:
        print(f"beir scifact acquisition failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result.as_public_json(), indent=2, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ARCHIVE_FILENAME",
    "EXPECTED_LAYOUT",
    "PUBLISHED_MD5",
    "SOURCE_URL",
    "AcquireBeirScifactConfig",
    "AcquiredBeirScifactResult",
    "BeirScifactAcquireError",
    "SourceOpener",
    "acquire_beir_scifact",
    "run",
]
