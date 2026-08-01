from __future__ import annotations

import hashlib
import io
import json
import ssl
import stat
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Literal

import certifi
import pytest

from scripts.benchmark_adapters import beir_scifact_acquire as acquire


@dataclass(frozen=True)
class ZipEntry:
    name: str
    data: bytes = b""
    external_attr: int | None = None
    compression: int = zipfile.ZIP_STORED


def test_official_source_parameters_are_pinned() -> None:
    assert (
        acquire.SOURCE_URL
        == "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"
    )
    assert acquire.PUBLISHED_MD5 == "5f7d1de60b170fc8027bb7898e2efca1"


def test_default_source_opener_uses_certifi_verified_ssl_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certifi_path = str(certifi.where())
    certifi_calls: list[str] = []
    context_calls: list[dict[str, object]] = []
    urlopen_calls: list[dict[str, object]] = []
    original_create_default_context = ssl.create_default_context

    def fake_certifi_where() -> str:
        certifi_calls.append("called")
        return certifi_path

    def fake_create_default_context(
        purpose: ssl.Purpose = ssl.Purpose.SERVER_AUTH,
        *,
        cafile: str | None = None,
        capath: str | None = None,
        cadata: str | bytes | None = None,
    ) -> ssl.SSLContext:
        context_calls.append(
            {
                "cafile": cafile,
                "capath": capath,
                "cadata": cadata,
                "purpose": purpose,
            }
        )
        return original_create_default_context(
            purpose=purpose,
            cafile=cafile,
            capath=capath,
            cadata=cadata,
        )

    def fake_urlopen(
        url: str,
        *,
        timeout: int,
        context: ssl.SSLContext,
    ) -> BinaryIO:
        urlopen_calls.append({"context": context, "timeout": timeout, "url": url})
        return io.BytesIO(b"")

    monkeypatch.setattr(certifi, "where", fake_certifi_where)
    monkeypatch.setattr(ssl, "create_default_context", fake_create_default_context)
    monkeypatch.setattr(acquire, "urlopen", fake_urlopen)

    stream = acquire.default_source_opener(acquire.SOURCE_URL)
    stream.close()

    assert certifi_calls == ["called"]
    assert context_calls == [
        {
            "cafile": certifi_path,
            "capath": None,
            "cadata": None,
            "purpose": ssl.Purpose.SERVER_AUTH,
        }
    ]
    assert len(urlopen_calls) == 1
    assert urlopen_calls[0]["url"] == acquire.SOURCE_URL
    assert urlopen_calls[0]["timeout"] == 60
    context = urlopen_calls[0]["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert context.get_ca_certs()


def test_acquire_downloads_caches_extracts_and_reports_public_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = make_scifact_zip()
    published_md5 = patch_published_md5(monkeypatch, payload)
    before_sha256 = hashlib.sha256(payload).hexdigest()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = acquire.acquire_beir_scifact(
        acquire.AcquireBeirScifactConfig(
            cache_dir=tmp_path / "cache",
            extract_dir=tmp_path / "extract",
            run_manifest_path=repo_root
            / ".llmwiki-work"
            / "benchmark-adapters"
            / "scifact"
            / "run-manifest.json",
            repo_root=repo_root,
        ),
        source_opener=opener_for(payload),
    )

    assert result.cache_path.read_bytes() == payload
    assert result.dataset_root == result.extract_dir / "scifact"
    assert result.dataset_root_handle == "scifact"
    assert (result.dataset_root / "corpus.jsonl").read_text(encoding="utf-8") == CORPUS_JSONL
    assert (result.dataset_root / "queries.jsonl").read_text(encoding="utf-8") == QUERIES_JSONL
    assert (result.dataset_root / "qrels" / "test.tsv").read_text(encoding="utf-8") == QRELS_TSV
    assert hashlib.sha256(payload).hexdigest() == before_sha256

    public = result.as_public_json()
    assert public == {
        "archive_bytes": len(payload),
        "cache_status": "downloaded",
        "dataset_root": "scifact",
        "extracted_bytes": len(CORPUS_JSONL.encode())
        + len(QUERIES_JSONL.encode())
        + len(QRELS_TSV.encode()),
        "file_count": 3,
        "observed_md5": published_md5,
        "published_md5": published_md5,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "source_url": acquire.SOURCE_URL,
    }
    serialized_public = json.dumps(public, sort_keys=True)
    dataset_root = public["dataset_root"]
    assert isinstance(dataset_root, str)
    assert str(tmp_path) not in serialized_public
    assert "\\" not in dataset_root
    assert ":" not in dataset_root

    manifest = repo_root / ".llmwiki-work" / "benchmark-adapters" / "scifact" / "run-manifest.json"
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert manifest_payload["public"] == public
    assert manifest_payload["local_paths"]["cache_path"] == str(result.cache_path)
    assert manifest_payload["schema_id"] == acquire.RUN_MANIFEST_SCHEMA_ID


def test_reuses_valid_cache_without_network_and_metadata_is_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = make_scifact_zip()
    patch_published_md5(monkeypatch, payload)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / acquire.ARCHIVE_FILENAME).write_bytes(payload)

    first = acquire.acquire_beir_scifact(
        acquire.AcquireBeirScifactConfig(cache_dir=cache_dir, extract_dir=tmp_path / "first"),
        source_opener=failing_opener,
    )
    second = acquire.acquire_beir_scifact(
        acquire.AcquireBeirScifactConfig(cache_dir=cache_dir, extract_dir=tmp_path / "second"),
        source_opener=failing_opener,
    )

    assert first.cache_status == "reused"
    assert second.cache_status == "reused"
    assert first.as_public_json() == second.as_public_json()


def test_accepts_expected_layout_at_archive_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = make_scifact_zip(prefix="")
    patch_published_md5(monkeypatch, payload)

    result = acquire.acquire_beir_scifact(
        acquire.AcquireBeirScifactConfig(
            cache_dir=tmp_path / "cache", extract_dir=tmp_path / "out"
        ),
        source_opener=opener_for(payload),
    )

    assert result.dataset_root_handle == "."
    assert result.dataset_root == result.extract_dir


def test_allows_cache_and_extract_under_repo_benchmark_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = make_scifact_zip()
    patch_published_md5(monkeypatch, payload)
    repo_root = tmp_path / "repo"

    result = acquire.acquire_beir_scifact(
        acquire.AcquireBeirScifactConfig(
            cache_dir=repo_root / ".llmwiki-work" / "benchmark-adapters" / "cache",
            extract_dir=repo_root / ".llmwiki-work" / "benchmark-adapters" / "extract",
            repo_root=repo_root,
        ),
        source_opener=opener_for(payload),
    )

    assert result.cache_path.is_file()
    assert result.dataset_root.is_dir()
    assert result.cache_status == "downloaded"


@pytest.mark.parametrize(
    ("cache_relative", "extract_relative", "match"),
    [
        ("cache", "../extract", "cache_dir inside the repository"),
        ("../cache", "extract", "extract_dir inside the repository"),
    ],
)
def test_rejects_cache_or_extract_inside_repo_outside_benchmark_workspace(
    tmp_path: Path,
    cache_relative: str,
    extract_relative: str,
    match: str,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    with pytest.raises(acquire.BeirScifactAcquireError, match=match):
        acquire.acquire_beir_scifact(
            acquire.AcquireBeirScifactConfig(
                cache_dir=repo_root / cache_relative,
                extract_dir=repo_root / extract_relative,
                repo_root=repo_root,
            ),
            source_opener=failing_opener,
        )

    assert not (repo_root / "cache").exists()
    assert not (repo_root / "extract").exists()


def test_download_md5_mismatch_removes_temporary_cache_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = make_scifact_zip()
    monkeypatch.setattr(acquire, "PUBLISHED_MD5", "0" * 32)
    cache_dir = tmp_path / "cache"

    with pytest.raises(acquire.BeirScifactAcquireError, match="MD5 mismatch"):
        acquire.acquire_beir_scifact(
            acquire.AcquireBeirScifactConfig(cache_dir=cache_dir, extract_dir=tmp_path / "out"),
            source_opener=opener_for(payload),
        )

    assert list(cache_dir.iterdir()) == []
    assert not (tmp_path / "out").exists()


def test_corrupted_zip_is_rejected_and_partial_extract_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"this is not a zip archive"
    patch_published_md5(monkeypatch, payload)

    with pytest.raises(acquire.BeirScifactAcquireError, match="readable ZIP"):
        acquire.acquire_beir_scifact(
            acquire.AcquireBeirScifactConfig(
                cache_dir=tmp_path / "cache", extract_dir=tmp_path / "out"
            ),
            source_opener=opener_for(payload),
        )

    assert not (tmp_path / "out").exists()
    assert no_temp_paths(tmp_path)


@pytest.mark.parametrize(
    ("entry_name", "match"),
    [
        ("scifact/../evil.txt", "unsafe path segments"),
        ("/absolute.txt", "relative"),
        ("C:/evil.txt", "Windows drive or UNC"),
        ("//server/share/evil.txt", "Windows drive or UNC"),
    ],
)
def test_rejects_unsafe_zip_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_name: str,
    match: str,
) -> None:
    payload = make_scifact_zip(extra_entries=[ZipEntry(entry_name, b"bad")])
    patch_published_md5(monkeypatch, payload)

    with pytest.raises(acquire.BeirScifactAcquireError, match=match):
        acquire.acquire_beir_scifact(
            acquire.AcquireBeirScifactConfig(
                cache_dir=tmp_path / "cache", extract_dir=tmp_path / "out"
            ),
            source_opener=opener_for(payload),
        )

    assert not (tmp_path / "out").exists()
    assert no_temp_paths(tmp_path)


def test_zip_path_validator_rejects_backslashes() -> None:
    info = zipfile.ZipInfo("placeholder")
    info.filename = "scifact\\evil.txt"

    with pytest.raises(acquire.BeirScifactAcquireError, match="backslashes"):
        acquire._validated_zip_relative_path(info)


def test_rejects_symlink_zip_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    symlink_attr = (stat.S_IFLNK | 0o777) << 16
    payload = make_scifact_zip(extra_entries=[ZipEntry("scifact/link", b"target", symlink_attr)])
    patch_published_md5(monkeypatch, payload)

    with pytest.raises(acquire.BeirScifactAcquireError, match="symlink"):
        acquire.acquire_beir_scifact(
            acquire.AcquireBeirScifactConfig(
                cache_dir=tmp_path / "cache", extract_dir=tmp_path / "out"
            ),
            source_opener=opener_for(payload),
        )


def test_rejects_duplicate_or_case_colliding_zip_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = make_zip(
        [
            *valid_scifact_entries(),
            ZipEntry("scifact/Corpus.jsonl", b"case collision"),
        ]
    )
    patch_published_md5(monkeypatch, payload)

    with pytest.raises(acquire.BeirScifactAcquireError, match="case-collides"):
        acquire.acquire_beir_scifact(
            acquire.AcquireBeirScifactConfig(
                cache_dir=tmp_path / "cache", extract_dir=tmp_path / "out"
            ),
            source_opener=opener_for(payload),
        )


def test_rejects_zip_bomb_uncompressed_size_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = make_scifact_zip()
    patch_published_md5(monkeypatch, payload)

    with pytest.raises(acquire.BeirScifactAcquireError, match="uncompressed size"):
        acquire.acquire_beir_scifact(
            acquire.AcquireBeirScifactConfig(
                cache_dir=tmp_path / "cache",
                extract_dir=tmp_path / "out",
                max_extracted_bytes=10,
            ),
            source_opener=opener_for(payload),
        )


def test_rejects_zip_bomb_file_count_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = make_scifact_zip(extra_entries=[ZipEntry("scifact/extra.txt", b"extra")])
    patch_published_md5(monkeypatch, payload)

    with pytest.raises(acquire.BeirScifactAcquireError, match="file count"):
        acquire.acquire_beir_scifact(
            acquire.AcquireBeirScifactConfig(
                cache_dir=tmp_path / "cache",
                extract_dir=tmp_path / "out",
                max_file_count=3,
            ),
            source_opener=opener_for(payload),
        )


def test_rejects_suspicious_compression_ratio(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = make_scifact_zip(
        corpus_jsonl=json.dumps(
            {"_id": "doc1", "title": "Highly compressible", "text": "A" * 20_000},
            sort_keys=True,
        )
        + "\n",
        compression=zipfile.ZIP_DEFLATED,
    )
    patch_published_md5(monkeypatch, payload)

    with pytest.raises(acquire.BeirScifactAcquireError, match="compression ratio"):
        acquire.acquire_beir_scifact(
            acquire.AcquireBeirScifactConfig(
                cache_dir=tmp_path / "cache",
                extract_dir=tmp_path / "out",
                max_compression_ratio=2.0,
            ),
            source_opener=opener_for(payload),
        )


def test_existing_cache_mismatch_fails_closed_without_downloading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = make_scifact_zip()
    patch_published_md5(monkeypatch, payload)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cached = cache_dir / acquire.ARCHIVE_FILENAME
    cached.write_bytes(b"stale cache")

    with pytest.raises(acquire.BeirScifactAcquireError, match="cached archive MD5 mismatch"):
        acquire.acquire_beir_scifact(
            acquire.AcquireBeirScifactConfig(cache_dir=cache_dir, extract_dir=tmp_path / "out"),
            source_opener=failing_opener,
        )

    assert cached.read_bytes() == b"stale cache"
    assert not (tmp_path / "out").exists()


def test_nonempty_extract_target_fails_before_network_and_preserves_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = make_scifact_zip()
    patch_published_md5(monkeypatch, payload)
    extract_dir = tmp_path / "out"
    extract_dir.mkdir()
    existing = extract_dir / "existing.txt"
    existing.write_text("keep\n", encoding="utf-8")

    with pytest.raises(acquire.BeirScifactAcquireError, match="not empty"):
        acquire.acquire_beir_scifact(
            acquire.AcquireBeirScifactConfig(cache_dir=tmp_path / "cache", extract_dir=extract_dir),
            source_opener=failing_opener,
        )

    assert existing.read_text(encoding="utf-8") == "keep\n"


def test_download_read_failure_is_wrapped_and_cleans_temp_cache(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "cache"

    with pytest.raises(acquire.BeirScifactAcquireError, match="failed to download.*read failed"):
        acquire.acquire_beir_scifact(
            acquire.AcquireBeirScifactConfig(cache_dir=cache_dir, extract_dir=tmp_path / "out"),
            source_opener=read_failing_opener,
        )

    assert cache_dir.is_dir()
    assert list(cache_dir.iterdir()) == []
    assert not (tmp_path / "out").exists()


def test_cli_wraps_source_open_failure_without_public_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = acquire.run(
        [
            "--cache-dir",
            str(tmp_path / "cache"),
            "--extract-dir",
            str(tmp_path / "out"),
        ],
        source_opener=open_failing_opener,
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "beir scifact acquisition failed: failed to download" in captured.err
    assert "open failed" in captured.err
    assert str(tmp_path) not in captured.err
    assert (tmp_path / "cache").is_dir()
    assert list((tmp_path / "cache").iterdir()) == []
    assert not (tmp_path / "out").exists()


def test_cli_wraps_cached_archive_read_failure_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cached = cache_dir / acquire.ARCHIVE_FILENAME
    cached.write_bytes(b"unreadable")
    original_open = Path.open

    def open_with_cached_read_failure(
        self: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> Any:
        if self == cached and mode == "rb":
            return CachedReadFailingStream()
        return original_open(self, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", open_with_cached_read_failure)

    exit_code = acquire.run(
        [
            "--cache-dir",
            str(cache_dir),
            "--extract-dir",
            str(tmp_path / "out"),
        ],
        source_opener=failing_opener,
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "beir scifact acquisition failed: failed to read cached" in captured.err
    assert "cached read failed" in captured.err
    assert "Traceback" not in captured.err
    assert str(tmp_path) not in captured.err
    with original_open(cached, "rb") as handle:
        assert handle.read() == b"unreadable"
    assert not (tmp_path / "out").exists()


def test_cli_prints_only_public_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = make_scifact_zip()
    patch_published_md5(monkeypatch, payload)

    exit_code = acquire.run(
        [
            "--cache-dir",
            str(tmp_path / "cache"),
            "--extract-dir",
            str(tmp_path / "out"),
        ],
        source_opener=opener_for(payload),
    )

    captured = capsys.readouterr()
    public = json.loads(captured.out)
    assert exit_code == 0
    assert captured.err == ""
    assert public["source_url"] == acquire.SOURCE_URL
    assert public["dataset_root"] == "scifact"
    assert str(tmp_path) not in captured.out


CORPUS_JSONL = (
    json.dumps(
        {"_id": "doc1", "text": "A scientific abstract.", "title": "A SciFact Paper"},
        sort_keys=True,
    )
    + "\n"
)
QUERIES_JSONL = json.dumps({"_id": "q1", "text": "A claim?"}, sort_keys=True) + "\n"
QRELS_TSV = "query-id\tcorpus-id\tscore\nq1\tdoc1\t1\n"


def patch_published_md5(monkeypatch: pytest.MonkeyPatch, payload: bytes) -> str:
    digest = hashlib.md5(payload, usedforsecurity=False).hexdigest()
    monkeypatch.setattr(acquire, "PUBLISHED_MD5", digest)
    return digest


def opener_for(payload: bytes) -> acquire.SourceOpener:
    def opener(_url: str) -> BinaryIO:
        return io.BytesIO(payload)

    return opener


def failing_opener(_url: str) -> BinaryIO:
    raise AssertionError("network should not be used")


def open_failing_opener(_url: str) -> BinaryIO:
    raise OSError("open failed")


class ReadFailingStream:
    def read(self, _size: int = -1) -> bytes:
        raise OSError("read failed")

    def close(self) -> None:
        return None


def read_failing_opener(_url: str) -> BinaryIO:
    return ReadFailingStream()  # type: ignore[return-value]


class CachedReadFailingStream:
    def __enter__(self) -> CachedReadFailingStream:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: object,
    ) -> Literal[False]:
        return False

    def read(self, _size: int = -1) -> bytes:
        raise OSError("cached read failed")


def make_scifact_zip(
    *,
    prefix: str = "scifact",
    extra_entries: Sequence[ZipEntry] = (),
    corpus_jsonl: str = CORPUS_JSONL,
    compression: int = zipfile.ZIP_STORED,
) -> bytes:
    return make_zip(
        [
            *valid_scifact_entries(
                prefix=prefix, corpus_jsonl=corpus_jsonl, compression=compression
            ),
            *extra_entries,
        ]
    )


def valid_scifact_entries(
    *,
    prefix: str = "scifact",
    corpus_jsonl: str = CORPUS_JSONL,
    compression: int = zipfile.ZIP_STORED,
) -> list[ZipEntry]:
    return [
        ZipEntry(zip_name(prefix, "corpus.jsonl"), corpus_jsonl.encode(), compression=compression),
        ZipEntry(
            zip_name(prefix, "queries.jsonl"), QUERIES_JSONL.encode(), compression=compression
        ),
        ZipEntry(zip_name(prefix, "qrels/test.tsv"), QRELS_TSV.encode(), compression=compression),
    ]


def zip_name(prefix: str, relative: str) -> str:
    return relative if prefix == "" else f"{prefix}/{relative}"


def make_zip(entries: Sequence[ZipEntry]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for entry in entries:
            info = zipfile.ZipInfo(entry.name)
            if entry.external_attr is not None:
                info.external_attr = entry.external_attr
            archive.writestr(info, entry.data, compress_type=entry.compression)
    return buffer.getvalue()


def no_temp_paths(root: Path) -> bool:
    return not any(path.name.startswith(".out.extract-") for path in root.iterdir())
