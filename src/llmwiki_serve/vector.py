from __future__ import annotations

import hashlib
import hmac
import importlib
import io
import json
import math
import os
import re
import secrets
import sys
import time
import unicodedata
import warnings
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager, redirect_stderr, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, TypeAlias, cast

import psutil

from .errors import LlmWikiUserError
from .models import SearchResult, WikiPage
from .search import (
    SearchCorpus,
    exact_required_page_ids_for_query,
    overview_results,
    page_exclusion_set,
    page_is_excluded,
    role_rank,
)

VectorVisibilityScope: TypeAlias = Literal["approved", "all"]
VectorModelDownloadPolicy: TypeAlias = Literal["never", "allow"]
VectorProviderName: TypeAlias = Literal["fastembed"]

VECTOR_TEXT_SCHEMA_ID = "llmwiki-vector-text-v1"
VECTOR_INDEX_SCHEMA_ID = "llmwiki-vector-index-v2"
VECTOR_CACHE_SCHEMA_VERSION = "llmwiki-vector-cache-v2"
VECTOR_METADATA_SCHEMA_ID = "llmwiki-vector-chunks-v1"
VECTOR_DISTANCE_METRIC: Literal["cosine"] = "cosine"
FASTEMBED_PROVIDER_ID = "fastembed"
DEFAULT_FASTEMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_FASTEMBED_MODEL_REVISION = "hf:faf4aa4225822f3bc6376869cb1164e8e3feedd0"
DEFAULT_FASTEMBED_MODEL_SOURCE = "qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
DEFAULT_FASTEMBED_MODEL_DIMENSION = 384
HYBRID_RRF_K = 60
HYBRID_LEXICAL_WEIGHT = 1.0
HYBRID_RELATED_VECTOR_WEIGHT = 1.0
HYBRID_GLOBAL_VECTOR_WEIGHT = 0.75
HYBRID_ORIENTATION_DOC_WEIGHT = 0.35
HYBRID_GRAPH_PRIOR_WEIGHT = 0.25
HYBRID_ORIENTATION_SEED_LIMIT = 3
HYBRID_ORIENTATION_CANDIDATE_LIMIT = 24
HYBRID_RELATED_PAGE_LIMIT = 64
HYBRID_RELATED_PER_SEED_LIMIT = 16
HYBRID_ORIENTATION_ROLES = frozenset({"hot", "index", "overview"})
HYBRID_CANDIDATE_DEPTH_MIN = 256
HYBRID_CANDIDATE_DEPTH_MULTIPLIER = 4
HYBRID_CANDIDATE_DEPTH_CAP = 1024
VECTOR_FILTERED_COPY_MAX_ROWS = 128
VECTOR_SCORE_BLOCK_ROWS = 2048
MAX_CHUNK_CHARS = 1_200
TARGET_CHUNK_TERMS = 180
VECTOR_LOCK_TIMEOUT_SECONDS = 30.0
VECTOR_LOCK_RETRY_SECONDS = 0.01
VECTOR_LOCK_STALE_SECONDS = 60.0
VECTOR_SALT_FILE = "salt.json"
VECTOR_VECTOR_FILE_PREFIX = "vectors"
VECTOR_VECTOR_FILE_SUFFIX = ".npy"
VECTOR_METADATA_FILE_PREFIX = "chunks"
VECTOR_METADATA_FILE_SUFFIX = ".json"
VECTOR_MANIFEST_FILE = "index.json"

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_WORD_RE = re.compile(r"\S+")
_RELATION_TOKEN_RE = re.compile(r"[0-9A-Za-z]+|[가-힣]+")
_RELATION_BOUNDARY_CHARS = r"0-9A-Za-z가-힣"
_WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]*)?\]\]")
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)(?:\s+[\"'][^)]*[\"'])?\)")


class EmbeddingProvider(Protocol):
    provider_id: str
    model_id: str
    model_revision: str
    dimension: int
    distance_metric: Literal["cosine"]

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...

    def embed_query(self, text: str) -> Sequence[float]: ...

    def safe_metadata(self) -> dict[str, str | int]: ...


class VectorSearchError(LlmWikiUserError):
    pass


@dataclass(frozen=True)
class VectorConfig:
    enabled: bool = False
    provider: VectorProviderName = "fastembed"
    model_name: str = DEFAULT_FASTEMBED_MODEL
    cache_dir: Path | None = None
    model_cache_dir: Path | None = None
    model_download: VectorModelDownloadPolicy = "never"


@dataclass(frozen=True)
class VectorChunk:
    page: WikiPage
    chunk_id: str
    ordinal: int
    start: int
    end: int
    heading_hash: str
    page_content_hash: str
    text: str


@dataclass(frozen=True)
class VectorChunkRecord:
    page_id: str
    chunk_id: str
    ordinal: int
    start: int
    end: int
    heading_hash: str
    page_content_hash: str
    vector: tuple[float, ...]
    norm: float


@dataclass(frozen=True)
class VectorIndex:
    identity: dict[str, str | int]
    records: tuple[VectorChunkRecord, ...]
    dimension: int
    cache_hit: bool
    vector_matrix: Any | None = None
    vector_norms: Any | None = None


@dataclass(frozen=True)
class _RelatedVectorCandidate:
    page_id: str
    score: float
    record: VectorChunkRecord


@dataclass(frozen=True)
class HybridDiagnostics:
    mode: Literal["plain-rrf", "orientation-seeded"]
    orientation_seed_count: int = 0
    related_page_count: int = 0
    fallback_reason: str = ""


@dataclass(frozen=True)
class _OrientationSeed:
    page: WikiPage
    rank: int
    lexical_rank: int | None
    vector_rank: int | None
    evidence_text: str


@dataclass(frozen=True)
class _Paragraph:
    text: str
    start: int
    end: int
    breadcrumb: tuple[str, ...]


@dataclass(frozen=True)
class _Segment:
    text: str
    start: int
    end: int


class _VectorScoreUnavailable(Exception):
    pass


class FastEmbedProvider:
    provider_id = FASTEMBED_PROVIDER_ID
    distance_metric: Literal["cosine"] = VECTOR_DISTANCE_METRIC

    def __init__(
        self,
        *,
        model_name: str,
        model_cache_dir: Path | None = None,
        model_download: VectorModelDownloadPolicy = "never",
    ) -> None:
        self.model_id = model_name.strip()
        if not self.model_id:
            raise VectorSearchError("Vector model name must be non-empty.")
        self.model_revision = DEFAULT_FASTEMBED_MODEL_REVISION
        self.dimension = DEFAULT_FASTEMBED_MODEL_DIMENSION
        self.fastembed_version = ""
        self.numpy_version = ""
        self.onnxruntime_version = ""
        try:
            from importlib.metadata import version

            fastembed_module = importlib.import_module("fastembed")
            TextEmbedding = fastembed_module.TextEmbedding
        except ImportError as exc:
            raise VectorSearchError(
                "Vector search requires the optional FastEmbed provider. "
                'Install it with `pip install "llmwiki-serve[vector]"` and restart.'
            ) from exc

        self.fastembed_version = version("fastembed")
        try:
            self.numpy_version = version("numpy")
        except Exception:
            self.numpy_version = ""
        try:
            self.onnxruntime_version = version("onnxruntime")
        except Exception:
            self.onnxruntime_version = ""
        model_metadata = fastembed_model_metadata(TextEmbedding, self.model_id)
        if model_metadata is None:
            raise VectorSearchError(
                f"FastEmbed model {safe_model_label(self.model_id)!r} is not supported "
                "by TextEmbedding. "
                "Choose a supported FastEmbed TextEmbedding model."
            )
        self.dimension = int(model_metadata.get("dim") or self.dimension)
        source = model_metadata.get("sources")
        if isinstance(source, dict) and source.get("hf") == DEFAULT_FASTEMBED_MODEL_SOURCE:
            self.model_revision = DEFAULT_FASTEMBED_MODEL_REVISION
        cache_dir_value = str(model_cache_dir) if model_cache_dir is not None else None
        local_files_only = model_download != "allow"
        try:
            with suppress_fastembed_diagnostics():
                self._embedding = TextEmbedding(
                    model_name=self.model_id,
                    cache_dir=cache_dir_value,
                    local_files_only=local_files_only,
                )
        except Exception as exc:
            raise VectorSearchError(
                "Vector provider model is not available locally. Pre-cache the configured "
                "FastEmbed model or restart with `--vector-model-download allow` "
                "or `LLMWIKI_VECTOR_MODEL_DOWNLOAD=allow` for an operator-approved download."
            ) from exc
        revision = revision_from_fastembed_instance(self._embedding)
        if revision:
            self.model_revision = revision

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        try:
            with suppress_fastembed_diagnostics():
                return [float_vector(vector) for vector in self._embedding.embed(list(texts))]
        except Exception as exc:
            raise VectorSearchError(
                "Vector provider failed while embedding document chunks."
            ) from exc

    def embed_query(self, text: str) -> Sequence[float]:
        try:
            with suppress_fastembed_diagnostics():
                return float_vector(next(iter(self._embedding.query_embed(text))))
        except Exception as exc:
            raise VectorSearchError("Vector provider failed while embedding the query.") from exc

    def safe_metadata(self) -> dict[str, str | int]:
        metadata: dict[str, str | int] = {
            "provider_id": self.provider_id,
            "model_id": safe_model_label(self.model_id),
            "model_revision": safe_model_label(self.model_revision),
            "dimension": self.dimension,
            "distance_metric": self.distance_metric,
            "fastembed_version": self.fastembed_version,
        }
        if self.numpy_version:
            metadata["numpy_version"] = self.numpy_version
        if self.onnxruntime_version:
            metadata["onnxruntime_version"] = self.onnxruntime_version
        return metadata


class VectorIndexCache:
    def __init__(self, *, root: Path, cache_dir: Path | None = None) -> None:
        self.root = root.expanduser().resolve(strict=False)
        self.cache_dir = resolve_vector_cache_dir(self.root, cache_dir)

    def load_or_build(
        self,
        *,
        provider: EmbeddingProvider,
        pages: Sequence[WikiPage],
        source_id: str,
        projection_signature: str,
        visibility_scope: VectorVisibilityScope,
        package_version: str,
    ) -> VectorIndex:
        identity = self.identity(
            provider=provider,
            pages=pages,
            source_id=source_id,
            projection_signature=projection_signature,
            visibility_scope=visibility_scope,
        )
        identity_digest = stable_json_digest(identity)
        record_dir = self.cache_dir / identity_digest[:2] / identity_digest
        cached = self._read_index(record_dir, identity, provider.dimension)
        if cached is not None:
            return cached
        with vector_cache_lock(record_dir / "build.lock"):
            cached = self._read_index(record_dir, identity, provider.dimension)
            if cached is not None:
                return cached
            chunks = build_vector_chunks(pages)
            return self._build_index(
                record_dir=record_dir,
                identity=identity,
                chunks=chunks,
                provider=provider,
                package_version=package_version,
            )

    def identity(
        self,
        *,
        provider: EmbeddingProvider,
        pages: Sequence[WikiPage],
        source_id: str,
        projection_signature: str,
        visibility_scope: VectorVisibilityScope,
    ) -> dict[str, str | int]:
        return {
            "cache_schema": VECTOR_CACHE_SCHEMA_VERSION,
            "source_scope": source_id,
            "root_fingerprint": self.root_fingerprint(),
            "projection_signature": projection_signature,
            "content_hash": pages_content_hash(pages),
            "provider_id": provider.provider_id,
            "provider_artifact_fingerprint": provider_artifact_fingerprint(provider),
            "model_id": safe_model_label(provider.model_id),
            "model_revision": safe_model_label(provider.model_revision),
            "dimension": provider.dimension,
            "distance_metric": provider.distance_metric,
            "text_schema": VECTOR_TEXT_SCHEMA_ID,
            "index_schema": VECTOR_INDEX_SCHEMA_ID,
            "visibility_scope": visibility_scope,
        }

    def root_fingerprint(self) -> str:
        salt = self._sidecar_salt()
        digest = hmac.new(
            bytes.fromhex(salt),
            os.path.normcase(str(self.root)).encode("utf-8", errors="surrogatepass"),
            hashlib.sha256,
        ).hexdigest()
        return f"hmac-sha256:{digest}"

    def _sidecar_salt(self) -> str:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        salt_path = self.cache_dir / VECTOR_SALT_FILE
        salt = read_sidecar_salt(salt_path)
        if salt:
            return salt
        with vector_cache_lock(self.cache_dir / "salt.lock"):
            salt = read_sidecar_salt(salt_path)
            if salt:
                return salt
            salt = secrets.token_hex(32)
            atomic_json_write(
                salt_path,
                {"schema": "llmwiki-vector-cache-salt-v1", "salt": salt},
            )
            return salt

    def _read_index(
        self,
        record_dir: Path,
        identity: dict[str, str | int],
        dimension: int,
    ) -> VectorIndex | None:
        manifest_path = record_dir / VECTOR_MANIFEST_FILE
        if not manifest_path.is_file():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                return None
            if manifest.get("identity") != identity:
                return None
            if manifest.get("cache_schema") != VECTOR_CACHE_SCHEMA_VERSION:
                return None
            if manifest.get("identity_digest") != stable_json_digest(identity):
                return None
            if manifest.get("dimension") != dimension:
                return None
            vector_ref = sidecar_manifest_ref(manifest, "vectors")
            metadata_ref = sidecar_manifest_ref(manifest, "metadata")
            if vector_ref is None or metadata_ref is None:
                return None
            if not sidecar_ref_matches_identity(vector_ref, identity):
                return None
            if not sidecar_ref_matches_identity(metadata_ref, identity):
                return None
            if vector_ref.get("format") != "npy" or metadata_ref.get("format") != "json":
                return None
            if metadata_ref.get("schema") != VECTOR_METADATA_SCHEMA_ID:
                return None
            vector_path = sidecar_file_path(record_dir, vector_ref["file"])
            metadata_path = sidecar_file_path(record_dir, metadata_ref["file"])
            if vector_path is None or metadata_path is None:
                return None
            if sha256_file(vector_path) != vector_ref["sha256"]:
                return None
            metadata_bytes = metadata_path.read_bytes()
            if hashlib.sha256(metadata_bytes).hexdigest() != metadata_ref["sha256"]:
                return None
            metadata = json.loads(metadata_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return vector_index_from_sidecars(
            metadata=metadata,
            vector_path=vector_path,
            vector_ref=vector_ref,
            identity=identity,
            dimension=dimension,
            cache_hit=True,
        )

    def _build_index(
        self,
        *,
        record_dir: Path,
        identity: dict[str, str | int],
        chunks: Sequence[VectorChunk],
        provider: EmbeddingProvider,
        package_version: str,
    ) -> VectorIndex:
        records: list[VectorChunkRecord] = []
        if chunks:
            vectors = provider.embed_documents([chunk.text for chunk in chunks])
            if len(vectors) != len(chunks):
                raise VectorSearchError(
                    "Vector provider returned a document embedding count that does not "
                    "match the chunk count."
                )
            for chunk, vector in zip(chunks, vectors, strict=True):
                normalized = vector_tuple(vector, expected_dimension=provider.dimension)
                records.append(
                    VectorChunkRecord(
                        page_id=chunk.page.id,
                        chunk_id=chunk.chunk_id,
                        ordinal=chunk.ordinal,
                        start=chunk.start,
                        end=chunk.end,
                        heading_hash=chunk.heading_hash,
                        page_content_hash=chunk.page_content_hash,
                        vector=normalized,
                        norm=vector_norm(normalized),
                    )
                )
        index = VectorIndex(
            identity=identity,
            records=tuple(records),
            dimension=provider.dimension,
            cache_hit=False,
            vector_matrix=vector_matrix_for_records(records),
            vector_norms=vector_norms_for_records(records),
        )
        self._publish(record_dir, index, package_version)
        return index

    def _publish(self, record_dir: Path, index: VectorIndex, package_version: str) -> None:
        record_dir.mkdir(parents=True, exist_ok=True)
        identity_digest = stable_json_digest(index.identity)
        vector_shape = [len(index.records), index.dimension]
        vector_path, vector_checksum = atomic_vector_matrix_write(record_dir, index)
        metadata = vector_index_metadata_payload(index)
        metadata_bytes = stable_json_bytes(metadata)
        metadata_checksum = hashlib.sha256(metadata_bytes).hexdigest()
        metadata_path = sidecar_content_path(
            record_dir,
            prefix=VECTOR_METADATA_FILE_PREFIX,
            checksum=metadata_checksum,
            suffix=VECTOR_METADATA_FILE_SUFFIX,
        )
        atomic_bytes_write(metadata_path, metadata_bytes)
        sidecar_identity = sidecar_identity_fields(index.identity)
        manifest = {
            "cache_schema": VECTOR_CACHE_SCHEMA_VERSION,
            "identity": index.identity,
            "identity_digest": identity_digest,
            "sidecars": {
                "vectors": {
                    **sidecar_identity,
                    "file": vector_path.name,
                    "format": "npy",
                    "sha256": vector_checksum,
                    "shape": vector_shape,
                    "dtype": "float32",
                },
                "metadata": {
                    **sidecar_identity,
                    "file": metadata_path.name,
                    "format": "json",
                    "schema": VECTOR_METADATA_SCHEMA_ID,
                    "sha256": metadata_checksum,
                    "chunk_count": len(index.records),
                },
            },
            "chunk_count": len(index.records),
            "dimension": index.dimension,
            "vector_dtype": "float32",
            "vector_shape": vector_shape,
            "created_at": int(time.time()),
            "package_version": package_version,
        }
        atomic_json_write(record_dir / VECTOR_MANIFEST_FILE, manifest)


def read_sidecar_salt(salt_path: Path) -> str:
    if not salt_path.is_file():
        return ""
    try:
        payload = json.loads(salt_path.read_text(encoding="utf-8"))
        salt = str(payload.get("salt") or "")
    except (OSError, json.JSONDecodeError):
        return ""
    return salt if len(salt) >= 32 else ""


def normalize_vector_config(
    value: bool | VectorConfig | None,
    *,
    enabled_if_provider: bool = False,
) -> VectorConfig:
    if value is None:
        return VectorConfig(enabled=enabled_if_provider)
    if isinstance(value, bool):
        return VectorConfig(enabled=value)
    return validate_vector_config(value)


def validate_vector_config(config: VectorConfig) -> VectorConfig:
    if config.provider != "fastembed":
        raise VectorSearchError("vector provider must be 'fastembed'")
    if config.model_download not in {"never", "allow"}:
        raise VectorSearchError("vector model download policy must be 'never' or 'allow'")
    if not config.model_name.strip():
        raise VectorSearchError("vector model name must be non-empty")
    return config


def vector_config_from_env() -> VectorConfig:
    enabled = env_bool(os.getenv("LLMWIKI_VECTOR_ENABLED"))
    provider_value = os.getenv("LLMWIKI_VECTOR_PROVIDER") or "fastembed"
    if provider_value != "fastembed":
        raise VectorSearchError("LLMWIKI_VECTOR_PROVIDER must be 'fastembed'")
    model_name = os.getenv("LLMWIKI_VECTOR_MODEL") or DEFAULT_FASTEMBED_MODEL
    model_download = os.getenv("LLMWIKI_VECTOR_MODEL_DOWNLOAD") or "never"
    if model_download not in {"never", "allow"}:
        raise VectorSearchError("LLMWIKI_VECTOR_MODEL_DOWNLOAD must be 'never' or 'allow'")
    cache_dir_env = os.getenv("LLMWIKI_VECTOR_CACHE_DIR")
    model_cache_dir_env = os.getenv("LLMWIKI_VECTOR_MODEL_CACHE_DIR")
    return validate_vector_config(
        VectorConfig(
            enabled=enabled,
            provider=cast(VectorProviderName, provider_value),
            model_name=model_name,
            cache_dir=Path(cache_dir_env).expanduser() if cache_dir_env else None,
            model_cache_dir=(
                Path(model_cache_dir_env).expanduser() if model_cache_dir_env else None
            ),
            model_download=cast(VectorModelDownloadPolicy, model_download),
        )
    )


def env_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def create_embedding_provider(config: VectorConfig) -> EmbeddingProvider:
    if not config.enabled:
        raise disabled_vector_error()
    return FastEmbedProvider(
        model_name=config.model_name,
        model_cache_dir=config.model_cache_dir,
        model_download=config.model_download,
    )


def provider_artifact_fingerprint(provider: EmbeddingProvider) -> str:
    explicit = getattr(provider, "artifact_fingerprint", "")
    if isinstance(explicit, str) and explicit.strip():
        return f"explicit-sha256:{stable_text_digest(explicit.strip())}"
    if provider.provider_id != FASTEMBED_PROVIDER_ID:
        return "custom-provider-unfingerprinted-v1"
    try:
        metadata = provider.safe_metadata()
    except Exception:
        metadata = {}
    payload = {
        "provider_id": FASTEMBED_PROVIDER_ID,
        "fastembed_version": str(metadata.get("fastembed_version") or "unknown"),
        "numpy_version": str(metadata.get("numpy_version") or "unknown"),
        "onnxruntime_version": str(metadata.get("onnxruntime_version") or "unknown"),
    }
    return f"sha256:{stable_json_digest(payload)}"


@contextmanager
def suppress_fastembed_diagnostics() -> Iterator[None]:
    loguru_logger = None
    try:
        loguru_module = importlib.import_module("loguru")
        loguru_logger = getattr(loguru_module, "logger", None)
        if loguru_logger is not None:
            loguru_logger.disable("fastembed")
    except Exception:
        loguru_logger = None
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"The model .* now uses mean pooling.*",
            category=UserWarning,
        )
        try:
            with redirect_stderr(io.StringIO()):
                yield
        finally:
            if loguru_logger is not None:
                loguru_logger.enable("fastembed")


def disabled_vector_error() -> VectorSearchError:
    return VectorSearchError(
        "Vector search is not enabled for this server. Configure an operator-owned "
        "local provider, for example `llmwiki-serve serve --vector-provider fastembed` "
        'after installing `pip install "llmwiki-serve[vector]"`.'
    )


def build_vector_chunks(pages: Sequence[WikiPage]) -> list[VectorChunk]:
    chunks: list[VectorChunk] = []
    for page in pages:
        page_hash = page_content_hash(page)
        ordinal = 0
        for paragraph in page_paragraphs(page):
            prefix = chunk_prefix(page, paragraph.breadcrumb)
            heading_hash = stable_text_digest(" > ".join(paragraph.breadcrumb))
            segment_limit = max(200, MAX_CHUNK_CHARS - len(prefix) - 1)
            for segment in split_paragraph(paragraph, max_chars=segment_limit):
                text = bounded_chunk_text(prefix, segment.text)
                if not text:
                    continue
                chunk_id = stable_text_digest(
                    json.dumps(
                        [
                            VECTOR_TEXT_SCHEMA_ID,
                            page.id,
                            page_hash,
                            heading_hash,
                            ordinal,
                            segment.start,
                            segment.end,
                        ],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
                chunks.append(
                    VectorChunk(
                        page=page,
                        chunk_id=chunk_id,
                        ordinal=ordinal,
                        start=segment.start,
                        end=segment.end,
                        heading_hash=heading_hash,
                        page_content_hash=page_hash,
                        text=text,
                    )
                )
                ordinal += 1
    return chunks


def page_paragraphs(page: WikiPage) -> list[_Paragraph]:
    text = normalized_page_text(page.text)
    paragraphs: list[_Paragraph] = []
    breadcrumb: list[str] = []
    paragraph_start: int | None = None
    paragraph_end = 0
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        line_start = offset
        offset += len(raw_line)
        line = raw_line[:-1] if raw_line.endswith("\n") else raw_line
        line_end = line_start + len(line)
        heading = _HEADING_RE.match(line.strip())
        if heading:
            flush_paragraph(text, paragraphs, paragraph_start, paragraph_end, tuple(breadcrumb))
            paragraph_start = None
            level = len(heading.group(1))
            label = " ".join(heading.group(2).split())
            breadcrumb = breadcrumb[: level - 1]
            breadcrumb.append(label)
            continue
        if not line.strip():
            flush_paragraph(text, paragraphs, paragraph_start, paragraph_end, tuple(breadcrumb))
            paragraph_start = None
            continue
        if paragraph_start is None:
            paragraph_start = line_start
        paragraph_end = line_end
    flush_paragraph(text, paragraphs, paragraph_start, paragraph_end, tuple(breadcrumb))
    return paragraphs


def flush_paragraph(
    text: str,
    paragraphs: list[_Paragraph],
    start: int | None,
    end: int,
    breadcrumb: tuple[str, ...],
) -> None:
    if start is None or end <= start:
        return
    body = text[start:end]
    if not body.strip():
        return
    paragraphs.append(_Paragraph(text=body, start=start, end=end, breadcrumb=breadcrumb))


def split_paragraph(paragraph: _Paragraph, *, max_chars: int) -> list[_Segment]:
    matches = list(_WORD_RE.finditer(paragraph.text))
    if not matches:
        return []
    segments: list[_Segment] = []
    segment_start_match = matches[0]
    segment_end_match = matches[0]
    term_count = 0
    for match in matches:
        candidate_start = segment_start_match.start()
        candidate_end = match.end()
        would_exceed_chars = candidate_end - candidate_start > max_chars
        would_exceed_terms = term_count >= TARGET_CHUNK_TERMS
        if term_count and (would_exceed_chars or would_exceed_terms):
            segments.extend(paragraph_segment(paragraph, segment_start_match, segment_end_match))
            segment_start_match = match
            term_count = 0
        segment_end_match = match
        term_count += 1
    segments.extend(paragraph_segment(paragraph, segment_start_match, segment_end_match))
    return segments


def paragraph_segment(
    paragraph: _Paragraph,
    start_match: re.Match[str],
    end_match: re.Match[str],
) -> list[_Segment]:
    start = start_match.start()
    end = end_match.end()
    absolute_start = paragraph.start + start
    absolute_end = paragraph.start + end
    text = paragraph.text[start:end]
    if len(text) <= MAX_CHUNK_CHARS:
        return [_Segment(text=text, start=absolute_start, end=absolute_end)]
    segments: list[_Segment] = []
    cursor = 0
    while cursor < len(text):
        next_cursor = min(len(text), cursor + MAX_CHUNK_CHARS)
        segments.append(
            _Segment(
                text=text[cursor:next_cursor],
                start=absolute_start + cursor,
                end=absolute_start + next_cursor,
            )
        )
        cursor = next_cursor
    return segments


def chunk_prefix(page: WikiPage, breadcrumb: Sequence[str]) -> str:
    parts = [page.title.strip()]
    heading = " > ".join(item for item in breadcrumb if item.strip())
    if heading and heading.casefold() != page.title.strip().casefold():
        parts.append(heading)
    return " ".join(" ".join(part.split()) for part in parts if part.strip())


def bounded_chunk_text(prefix: str, body: str) -> str:
    combined = " ".join(part for part in (prefix, body) if part.strip())
    clean = " ".join(combined.split())
    if len(clean) <= MAX_CHUNK_CHARS:
        return clean
    trimmed = clean[:MAX_CHUNK_CHARS]
    boundary = trimmed.rfind(" ")
    if boundary >= MAX_CHUNK_CHARS // 2:
        trimmed = trimmed[:boundary]
    return trimmed.rstrip()


def search_vector_index(
    vector_index: VectorIndex,
    *,
    provider: EmbeddingProvider,
    pages: Sequence[WikiPage],
    query: str,
    limit: int,
    snippet_chars: int | None = None,
    exclude_page_ids: Sequence[str] | None = None,
) -> list[SearchResult]:
    visible_pages = list(pages)
    query_state = embed_query_vector(provider, query)
    if query_state is None:
        return vector_query_fallback_results(
            visible_pages,
            query=query,
            limit=limit,
            snippet_chars=snippet_chars,
            exclude_page_ids=exclude_page_ids,
        )
    query_vector, query_norm = query_state
    return search_vector_index_with_query_vector(
        vector_index,
        pages=visible_pages,
        query=query,
        query_vector=query_vector,
        query_norm=query_norm,
        limit=limit,
        snippet_chars=snippet_chars,
        exclude_page_ids=exclude_page_ids,
    )


def embed_query_vector(
    provider: EmbeddingProvider,
    query: str,
) -> tuple[tuple[float, ...], float] | None:
    if not query.strip():
        return None
    query_vector = vector_tuple(provider.embed_query(query), expected_dimension=provider.dimension)
    query_norm = vector_norm(query_vector)
    if query_norm <= 0:
        return None
    return query_vector, query_norm


def vector_query_fallback_results(
    pages: Sequence[WikiPage],
    *,
    query: str,
    limit: int,
    snippet_chars: int | None,
    exclude_page_ids: Sequence[str] | None = None,
) -> list[SearchResult]:
    if query.strip():
        return []
    return overview_results(
        list(pages),
        limit,
        snippet_chars=snippet_chars,
        exclude_page_ids=exclude_page_ids,
    )


def search_vector_index_with_query_vector(
    vector_index: VectorIndex,
    *,
    pages: Sequence[WikiPage],
    query: str,
    query_vector: tuple[float, ...] | None,
    query_norm: float,
    limit: int,
    snippet_chars: int | None = None,
    exclude_page_ids: Sequence[str] | None = None,
) -> list[SearchResult]:
    visible_pages = list(pages)
    if query_vector is None or query_norm <= 0:
        return vector_query_fallback_results(
            visible_pages,
            query=query,
            limit=limit,
            snippet_chars=snippet_chars,
            exclude_page_ids=exclude_page_ids,
        )
    pages_by_id = {page.id: page for page in visible_pages}
    excluded = page_exclusion_set(exclude_page_ids)
    record_positions = filtered_record_positions_for_small_page_subset(
        vector_index,
        pages_by_id,
        excluded,
    )
    if record_positions == ():
        return []
    best_by_page = best_vector_records_by_page(
        vector_index,
        pages_by_id=pages_by_id,
        excluded=excluded,
        query_vector=query_vector,
        query_norm=query_norm,
        record_positions=record_positions,
    )
    if not best_by_page:
        return []
    ranked = sorted(
        best_by_page.items(),
        key=lambda item: vector_sort_key(item[1][0], pages_by_id[item[0]], item[1][1]),
    )
    return [
        vector_result(
            pages_by_id[page_id],
            score=score,
            record=record,
            snippet_chars=snippet_chars,
        )
        for page_id, (score, record) in ranked[:limit]
    ]


def orientation_exclude_page_ids(
    pages: Sequence[WikiPage],
    exclude_page_ids: Sequence[str] | None = None,
) -> list[str]:
    excluded = list(exclude_page_ids or ())
    excluded.extend(page.id for page in pages if not hybrid_orientation_page(page))
    return excluded


def hybrid_search_results_for_vector_index(
    *,
    lexical_results: Sequence[SearchResult],
    orientation_lexical_results: Sequence[SearchResult],
    vector_index: VectorIndex,
    provider: EmbeddingProvider,
    pages: Sequence[WikiPage],
    corpus: SearchCorpus,
    query: str,
    limit: int,
    candidate_limit: int,
    snippet_chars: int | None,
    exclude_page_ids: Sequence[str] | None = None,
    diagnostics_sink: Callable[[HybridDiagnostics], None] | None = None,
) -> list[SearchResult]:
    visible_pages = list(pages)
    query_state = embed_query_vector(provider, query)
    if query_state is None:
        query_vector: tuple[float, ...] | None = None
        query_norm = 0.0
    else:
        query_vector, query_norm = query_state
    orientation_pages = [page for page in visible_pages if hybrid_orientation_page(page)]
    orientation_vector_results = search_vector_index_with_query_vector(
        vector_index,
        pages=orientation_pages,
        query=query,
        query_vector=query_vector,
        query_norm=query_norm,
        limit=HYBRID_ORIENTATION_CANDIDATE_LIMIT,
        snippet_chars=snippet_chars,
        exclude_page_ids=exclude_page_ids,
    )
    seeds = orientation_seeds(
        lexical_results=orientation_lexical_results,
        vector_results=orientation_vector_results,
        corpus=corpus,
    )
    (
        related_ranked_ids,
        related_vector_results,
        related_vector_scores_by_page,
    ) = related_vector_results_from_orientation(
        corpus=corpus,
        seeds=seeds,
        vector_index=vector_index,
        query=query,
        query_vector=query_vector,
        query_norm=query_norm,
        snippet_chars=snippet_chars,
        exclude_page_ids=exclude_page_ids,
    )
    if not seeds or not related_ranked_ids:
        global_vector_results = search_vector_index_with_query_vector(
            vector_index,
            pages=visible_pages,
            query=query,
            query_vector=query_vector,
            query_norm=query_norm,
            limit=candidate_limit,
            snippet_chars=snippet_chars,
            exclude_page_ids=exclude_page_ids,
        )
        if diagnostics_sink is not None:
            diagnostics_sink(
                HybridDiagnostics(
                    mode="plain-rrf",
                    orientation_seed_count=len(seeds),
                    related_page_count=len(related_ranked_ids),
                    fallback_reason="no_safe_related_set",
                )
            )
        return plain_hybrid_search_results(
            lexical_results=lexical_results,
            vector_results=global_vector_results,
            corpus=corpus,
            query=query,
            limit=limit,
            suppress_orientation_answers=True,
        )
    global_vector_results = search_vector_index_with_query_vector(
        vector_index,
        pages=visible_pages,
        query=query,
        query_vector=query_vector,
        query_norm=query_norm,
        limit=candidate_limit,
        snippet_chars=snippet_chars,
        exclude_page_ids=exclude_page_ids,
    )
    if diagnostics_sink is not None:
        diagnostics_sink(
            HybridDiagnostics(
                mode="orientation-seeded",
                orientation_seed_count=len(seeds),
                related_page_count=len(related_ranked_ids),
            )
        )
    return orientation_seeded_hybrid_search_results(
        lexical_results=lexical_results,
        vector_results=global_vector_results,
        related_vector_results=related_vector_results,
        related_vector_scores_by_page=related_vector_scores_by_page,
        orientation_seeds=seeds,
        related_ranked_ids=related_ranked_ids,
        corpus=corpus,
        query=query,
        limit=limit,
    )


def hybrid_search_results(
    *,
    lexical_results: Sequence[SearchResult],
    vector_results: Sequence[SearchResult],
    corpus: SearchCorpus,
    query: str,
    limit: int,
    candidate_limit: int | None = None,
    diagnostics_sink: Callable[[HybridDiagnostics], None] | None = None,
) -> list[SearchResult]:
    _ = candidate_limit
    seeds = orientation_seeds(
        lexical_results=lexical_results,
        vector_results=vector_results,
        corpus=corpus,
    )
    if diagnostics_sink is not None:
        diagnostics_sink(
            HybridDiagnostics(
                mode="plain-rrf",
                orientation_seed_count=len(seeds),
                related_page_count=0,
                fallback_reason="no_exact_related_vector_scores",
            )
        )
    return plain_hybrid_search_results(
        lexical_results=lexical_results,
        vector_results=vector_results,
        corpus=corpus,
        query=query,
        limit=limit,
    )


def plain_hybrid_search_results(
    *,
    lexical_results: Sequence[SearchResult],
    vector_results: Sequence[SearchResult],
    corpus: SearchCorpus,
    query: str,
    limit: int,
    suppress_orientation_answers: bool = False,
) -> list[SearchResult]:
    required_page_ids = exact_required_page_ids_for_query(corpus, query)
    if required_page_ids == set():
        return []
    allowed = required_page_ids
    suppressed_page_ids = (
        suppressed_orientation_answer_page_ids(corpus, query)
        if suppress_orientation_answers
        else set()
    )
    lexical_ranked = [
        result
        for result in lexical_results
        if (allowed is None or result.page_id in allowed)
        and result.page_id not in suppressed_page_ids
    ]
    vector_ranked = [
        result
        for result in vector_results
        if (allowed is None or result.page_id in allowed)
        and result.page_id not in suppressed_page_ids
    ]
    page_results: dict[str, SearchResult] = {}
    lexical_rank_by_page: dict[str, int] = {}
    vector_rank_by_page: dict[str, int] = {}
    scores: dict[str, float] = {}
    for rank, result in enumerate(lexical_ranked, start=1):
        page_results.setdefault(result.page_id, result)
        lexical_rank_by_page[result.page_id] = rank
        scores[result.page_id] = scores.get(result.page_id, 0.0) + 1.0 / (HYBRID_RRF_K + rank)
    for rank, result in enumerate(vector_ranked, start=1):
        if result.page_id not in page_results:
            page_results[result.page_id] = result
        vector_rank_by_page[result.page_id] = rank
        scores[result.page_id] = scores.get(result.page_id, 0.0) + 1.0 / (HYBRID_RRF_K + rank)

    def hybrid_key(page_id: str) -> tuple[float, int, int, str, str]:
        result = page_results[page_id]
        exact_lexical = (
            1 if required_page_ids is not None and page_id in lexical_rank_by_page else 0
        )
        return (-scores[page_id], -exact_lexical, role_rank(result.role), result.path, page_id)

    ranked_ids = sorted(scores, key=hybrid_key)
    results: list[SearchResult] = []
    for page_id in ranked_ids[:limit]:
        base = page_results[page_id]
        if required_page_ids is not None and page_id in lexical_rank_by_page:
            lexical_match = next(item for item in lexical_ranked if item.page_id == page_id)
            base = lexical_match
        results.append(
            base.model_copy(update={"score": round(scores[page_id], 4), "route": "hybrid"})
        )
    return results


def orientation_seeded_hybrid_search_results(
    *,
    lexical_results: Sequence[SearchResult],
    vector_results: Sequence[SearchResult],
    related_vector_results: Sequence[SearchResult],
    orientation_seeds: Sequence[_OrientationSeed],
    related_ranked_ids: Sequence[str],
    corpus: SearchCorpus,
    query: str,
    limit: int,
    related_vector_scores_by_page: dict[str, float] | None = None,
) -> list[SearchResult]:
    required_page_ids = exact_required_page_ids_for_query(corpus, query)
    if required_page_ids == set():
        return []
    allowed = required_page_ids
    suppressed_page_ids = suppressed_orientation_answer_page_ids(corpus, query)
    related_ids = set(related_ranked_ids)
    lexical_ranked = [
        result
        for result in lexical_results
        if (allowed is None or result.page_id in allowed)
        and result.page_id not in suppressed_page_ids
    ]
    global_vector_ranked = [
        result
        for result in vector_results
        if (allowed is None or result.page_id in allowed)
        and result.page_id not in suppressed_page_ids
    ]
    related_vector_ranked = [
        result
        for result in related_vector_results
        if (allowed is None or result.page_id in allowed)
        and result.page_id in related_ids
        and result.page_id not in suppressed_page_ids
    ]
    lexical_rank_by_page = {
        result.page_id: rank for rank, result in enumerate(lexical_ranked, start=1)
    }
    related_vector_ranked = rerank_related_vector_results(
        related_vector_ranked,
        lexical_rank_by_page=lexical_rank_by_page,
        exact_scores_by_page=related_vector_scores_by_page,
    )
    orientation_ranked = [
        seed_result(seed)
        for seed in orientation_seeds
        if (allowed is None or seed.page.id in allowed) and seed.page.id not in suppressed_page_ids
    ]
    graph_prior_ranked = [
        result
        for result in graph_prior_results_by_vector_relevance(
            corpus,
            related_ranked_ids,
            related_vector_ranked,
            exact_scores_by_page=related_vector_scores_by_page,
        )
        if (allowed is None or result.page_id in allowed)
        and result.page_id not in suppressed_page_ids
    ]

    page_results: dict[str, SearchResult] = {}
    scores: dict[str, float] = {}
    add_hybrid_channel(
        scores,
        page_results,
        lexical_ranked,
        weight=HYBRID_LEXICAL_WEIGHT,
        rank_record=lexical_rank_by_page,
    )
    add_hybrid_channel(
        scores,
        page_results,
        related_vector_ranked,
        weight=HYBRID_RELATED_VECTOR_WEIGHT,
    )
    add_hybrid_channel(
        scores,
        page_results,
        global_vector_ranked,
        weight=HYBRID_GLOBAL_VECTOR_WEIGHT,
    )
    add_hybrid_channel(
        scores,
        page_results,
        orientation_ranked,
        weight=HYBRID_ORIENTATION_DOC_WEIGHT,
    )
    add_hybrid_channel(
        scores,
        page_results,
        graph_prior_ranked,
        weight=HYBRID_GRAPH_PRIOR_WEIGHT,
    )

    def hybrid_key(page_id: str) -> tuple[float, int, int, str, str]:
        result = page_results[page_id]
        exact_lexical = (
            1 if required_page_ids is not None and page_id in lexical_rank_by_page else 0
        )
        return (-scores[page_id], -exact_lexical, role_rank(result.role), result.path, page_id)

    ranked_ids = sorted(scores, key=hybrid_key)
    results: list[SearchResult] = []
    for page_id in ranked_ids[:limit]:
        base = page_results[page_id]
        if required_page_ids is not None and page_id in lexical_rank_by_page:
            base = next(item for item in lexical_ranked if item.page_id == page_id)
        results.append(
            base.model_copy(update={"score": round(scores[page_id], 4), "route": "hybrid"})
        )
    return results


def hybrid_candidate_depth(limit: int, *, total_docs: int) -> int:
    if total_docs <= 0:
        return 0
    requested = max(1, int(limit))
    scaled = max(
        HYBRID_CANDIDATE_DEPTH_MIN,
        HYBRID_CANDIDATE_DEPTH_MULTIPLIER * requested,
    )
    bounded_overfetch = min(HYBRID_CANDIDATE_DEPTH_CAP, scaled)
    return min(total_docs, max(requested, bounded_overfetch))


def add_hybrid_channel(
    scores: dict[str, float],
    page_results: dict[str, SearchResult],
    ranked_results: Sequence[SearchResult],
    *,
    weight: float,
    rank_record: dict[str, int] | None = None,
) -> None:
    for rank, result in enumerate(ranked_results, start=1):
        page_results.setdefault(result.page_id, result)
        if rank_record is not None:
            rank_record[result.page_id] = rank
        scores[result.page_id] = scores.get(result.page_id, 0.0) + weight / (HYBRID_RRF_K + rank)


def rerank_related_vector_results(
    results: Sequence[SearchResult],
    *,
    lexical_rank_by_page: dict[str, int],
    exact_scores_by_page: dict[str, float] | None = None,
) -> list[SearchResult]:
    if not exact_scores_by_page:
        return list(results)
    lexical_floor = len(lexical_rank_by_page) + HYBRID_RRF_K + 1

    def related_vector_key(
        item: tuple[int, SearchResult],
    ) -> tuple[int, float, int, int, str, str]:
        original_rank, result = item
        exact_score = exact_scores_by_page.get(result.page_id)
        if exact_score is None or not math.isfinite(exact_score):
            return (1, 0.0, original_rank, role_rank(result.role), result.path, result.page_id)
        return (
            0,
            -exact_score,
            lexical_rank_by_page.get(result.page_id, lexical_floor),
            role_rank(result.role),
            result.path,
            result.page_id,
        )

    return [result for _rank, result in sorted(enumerate(results), key=related_vector_key)]


def suppressed_orientation_answer_page_ids(corpus: SearchCorpus, query: str) -> set[str]:
    return {
        page.id
        for page in corpus.pages
        if hybrid_orientation_page(page) and not page_explicitly_matches_query(page, query)
    }


def page_explicitly_matches_query(page: WikiPage, query: str) -> bool:
    query_label = normalized_relation_label(query)
    if not query_label:
        return False
    return query_label in {
        normalized
        for value in (
            page.id,
            page.title,
            page.path,
            page.path.rsplit("/", maxsplit=1)[-1],
            Path(page.path).stem,
        )
        if (normalized := normalized_relation_label(value))
    }


def orientation_seeds(
    *,
    lexical_results: Sequence[SearchResult],
    vector_results: Sequence[SearchResult],
    corpus: SearchCorpus,
) -> list[_OrientationSeed]:
    pages_by_id = {page.id: page for page in corpus.pages}
    lexical_by_page = {result.page_id: result for result in lexical_results}
    vector_by_page = {result.page_id: result for result in vector_results}
    lexical_rank = {result.page_id: rank for rank, result in enumerate(lexical_results, start=1)}
    vector_rank = {result.page_id: rank for rank, result in enumerate(vector_results, start=1)}
    seeds: list[_OrientationSeed] = []
    for page in corpus.pages:
        if not hybrid_orientation_page(page):
            continue
        lex_rank = lexical_rank.get(page.id)
        vec_rank = vector_rank.get(page.id)
        bounded_lex = lex_rank is not None and lex_rank <= HYBRID_ORIENTATION_CANDIDATE_LIMIT
        bounded_vec = vec_rank is not None and vec_rank <= HYBRID_ORIENTATION_CANDIDATE_LIMIT
        if not bounded_lex and not bounded_vec:
            continue
        evidence = orientation_evidence_text(
            lexical_by_page.get(page.id),
            vector_by_page.get(page.id),
        )
        if not evidence:
            continue
        rank = min(
            lex_rank or HYBRID_ORIENTATION_CANDIDATE_LIMIT + 1,
            vec_rank or HYBRID_ORIENTATION_CANDIDATE_LIMIT + 1,
        )
        seeds.append(
            _OrientationSeed(
                page=pages_by_id[page.id],
                rank=rank,
                lexical_rank=lex_rank,
                vector_rank=vec_rank,
                evidence_text=evidence,
            )
        )
    seeds.sort(
        key=lambda seed: (
            seed.rank,
            seed.lexical_rank or HYBRID_ORIENTATION_CANDIDATE_LIMIT + 1,
            seed.vector_rank or HYBRID_ORIENTATION_CANDIDATE_LIMIT + 1,
            role_rank(seed.page.role),
            seed.page.path,
            seed.page.id,
        )
    )
    return seeds[:HYBRID_ORIENTATION_SEED_LIMIT]


def hybrid_orientation_page(page: WikiPage) -> bool:
    return page.role in HYBRID_ORIENTATION_ROLES


def orientation_evidence_text(
    lexical_result: SearchResult | None,
    vector_result: SearchResult | None,
) -> str:
    parts = [
        result.snippet.strip()
        for result in (lexical_result, vector_result)
        if result is not None and result.snippet.strip()
    ]
    return " ".join(parts)


def related_page_ids_from_orientation(
    corpus: SearchCorpus,
    seeds: Sequence[_OrientationSeed],
    *,
    query: str = "",
    exclude_page_ids: Sequence[str] | None = None,
) -> list[str]:
    excluded = page_exclusion_set(exclude_page_ids)
    per_seed_pools = related_page_id_pools_from_orientation(
        corpus,
        seeds,
        query=query,
        excluded=excluded,
    )
    pages_by_id = {page.id: page for page in corpus.pages}
    candidate_ids = set().union(*per_seed_pools) if per_seed_pools else set()

    def plain_related_key(page_id: str) -> tuple[int, str, str]:
        page = pages_by_id[page_id]
        return (role_rank(page.role), page.path, page.id)

    return sorted(candidate_ids, key=plain_related_key)[:HYBRID_RELATED_PAGE_LIMIT]


def related_vector_results_from_orientation(
    *,
    corpus: SearchCorpus,
    seeds: Sequence[_OrientationSeed],
    vector_index: VectorIndex,
    query: str,
    query_vector: tuple[float, ...] | None,
    query_norm: float,
    snippet_chars: int | None,
    exclude_page_ids: Sequence[str] | None = None,
) -> tuple[list[str], list[SearchResult], dict[str, float]]:
    if not seeds:
        return [], [], {}
    if query_vector is None or query_norm <= 0:
        return [], [], {}
    excluded = page_exclusion_set(exclude_page_ids)
    per_seed_pools = related_page_id_pools_from_orientation(
        corpus,
        seeds,
        query=query,
        excluded=excluded,
    )
    candidate_ids = set().union(*per_seed_pools) if per_seed_pools else set()
    if not candidate_ids:
        return [], [], {}
    pages_by_id = {page.id: page for page in corpus.pages}
    candidate_pages_by_id = {
        page_id: pages_by_id[page_id] for page_id in candidate_ids if page_id in pages_by_id
    }
    record_positions = filtered_record_positions_for_small_page_subset(
        vector_index,
        candidate_pages_by_id,
        excluded,
    )
    if record_positions == ():
        return [], [], {}
    best_by_page = best_vector_records_by_page(
        vector_index,
        pages_by_id=candidate_pages_by_id,
        excluded=excluded,
        query_vector=query_vector,
        query_norm=query_norm,
        record_positions=record_positions,
    )
    if not best_by_page:
        return [], [], {}
    candidates_by_page = {
        page_id: _RelatedVectorCandidate(page_id=page_id, score=score, record=record)
        for page_id, (score, record) in best_by_page.items()
    }

    def exact_related_key(candidate: _RelatedVectorCandidate) -> tuple[float, int, str, str, int]:
        return vector_sort_key(candidate.score, pages_by_id[candidate.page_id], candidate.record)

    per_seed_capped_ids: set[str] = set()
    for pool in per_seed_pools:
        ranked_for_seed = sorted(
            (candidates_by_page[page_id] for page_id in pool if page_id in candidates_by_page),
            key=exact_related_key,
        )
        per_seed_capped_ids.update(
            candidate.page_id for candidate in ranked_for_seed[:HYBRID_RELATED_PER_SEED_LIMIT]
        )
    selected = sorted(
        (candidates_by_page[page_id] for page_id in per_seed_capped_ids),
        key=exact_related_key,
    )[:HYBRID_RELATED_PAGE_LIMIT]
    if not selected:
        return [], [], {}
    related_ids = [candidate.page_id for candidate in selected]
    related_results = [
        vector_result(
            pages_by_id[candidate.page_id],
            score=candidate.score,
            record=candidate.record,
            snippet_chars=snippet_chars,
        )
        for candidate in selected
    ]
    exact_scores = {candidate.page_id: candidate.score for candidate in selected}
    return related_ids, related_results, exact_scores


def related_page_id_pools_from_orientation(
    corpus: SearchCorpus,
    seeds: Sequence[_OrientationSeed],
    *,
    query: str,
    excluded: set[str],
) -> list[set[str]]:
    if not seeds:
        return []
    pages_by_id = {page.id: page for page in corpus.pages}
    page_by_key = page_lookup(corpus.pages)
    source_ref_pages: dict[str, list[WikiPage]] = {}
    tag_pages: dict[str, list[WikiPage]] = {}
    for page in corpus.pages:
        for source_ref in page.source_refs:
            source_ref_pages.setdefault(normalized_relation_label(source_ref), []).append(page)
        for tag in page.tags:
            tag_pages.setdefault(normalized_relation_label(tag), []).append(page)

    per_seed_pools: list[set[str]] = []
    for seed in seeds:
        pool: set[str] = set()
        for link in visible_seed_links(seed, query=query):
            target = page_by_key.get(normalized_relation_label(link))
            if target is not None:
                safe_id = safe_related_page_id(
                    target.id,
                    seed_page_id=seed.page.id,
                    pages_by_id=pages_by_id,
                    excluded=excluded,
                )
                if safe_id is not None:
                    pool.add(safe_id)
        for source_ref in visible_seed_source_refs(seed, query=query):
            for page in source_ref_pages.get(normalized_relation_label(source_ref), ()):
                safe_id = safe_related_page_id(
                    page.id,
                    seed_page_id=seed.page.id,
                    pages_by_id=pages_by_id,
                    excluded=excluded,
                )
                if safe_id is not None:
                    pool.add(safe_id)
        for tag in visible_seed_tags(seed, query=query):
            for page in tag_pages.get(normalized_relation_label(tag), ()):
                safe_id = safe_related_page_id(
                    page.id,
                    seed_page_id=seed.page.id,
                    pages_by_id=pages_by_id,
                    excluded=excluded,
                )
                if safe_id is not None:
                    pool.add(safe_id)
        per_seed_pools.append(pool)
    return per_seed_pools


def safe_related_page_id(
    page_id: str,
    *,
    seed_page_id: str,
    pages_by_id: dict[str, WikiPage],
    excluded: set[str],
) -> str | None:
    page = pages_by_id.get(page_id)
    if page is None or page.id == seed_page_id or page_is_excluded(page, excluded):
        return None
    return page.id


def visible_seed_links(seed: _OrientationSeed, *, query: str = "") -> list[str]:
    return [
        link
        for link in seed.page.links
        if relation_label_visible(link, seed.evidence_text) or relation_label_visible(link, query)
    ]


def visible_seed_source_refs(seed: _OrientationSeed, *, query: str = "") -> list[str]:
    return [
        source_ref
        for source_ref in seed.page.source_refs
        if relation_label_visible(source_ref, seed.evidence_text)
        or relation_label_visible(source_ref, query)
    ]


def visible_seed_tags(seed: _OrientationSeed, *, query: str = "") -> list[str]:
    return [
        tag
        for tag in seed.page.tags
        if relation_label_visible(tag, seed.evidence_text) or relation_label_visible(tag, query)
    ]


def relation_label_visible(label: str, evidence_text: str) -> bool:
    label_values = relation_label_candidates(label)
    if not label_values or not evidence_text.strip():
        return False
    normalized_values = {
        normalized for value in label_values if (normalized := normalized_relation_label(value))
    }
    if normalized_values & explicit_relation_labels(evidence_text):
        return True
    return any(relation_phrase_visible(value, evidence_text) for value in label_values)


def relation_label_candidates(label: str) -> set[str]:
    normalized = unicodedata.normalize("NFC", label.strip())
    stripped = normalized[:-3] if normalized.endswith(".md") else normalized
    return {
        stripped,
        Path(stripped).stem,
        stripped.rsplit("/", maxsplit=1)[-1],
    }


def explicit_relation_labels(text: str) -> set[str]:
    values: set[str] = set()
    for match in _WIKI_LINK_RE.finditer(text):
        values.update(relation_label_candidates(match.group(1).strip()))
    for match in _MARKDOWN_LINK_RE.finditer(text):
        target = match.group(1).strip("<>")
        target = target.split("#", maxsplit=1)[0].split("?", maxsplit=1)[0]
        values.update(relation_label_candidates(target))
    return {normalized for value in values if (normalized := normalized_relation_label(value))}


def relation_phrase_visible(label: str, evidence_text: str) -> bool:
    tokens = relation_tokens(label)
    if not tokens:
        return False
    separator = rf"[^{_RELATION_BOUNDARY_CHARS}]*"
    phrase = separator.join(re.escape(token) for token in tokens)
    pattern = rf"(?<![{_RELATION_BOUNDARY_CHARS}]){phrase}(?![{_RELATION_BOUNDARY_CHARS}])"
    evidence = unicodedata.normalize("NFC", evidence_text).casefold()
    return re.search(pattern, evidence) is not None


def relation_tokens(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFC", value)
    return tuple(match.group(0).casefold() for match in _RELATION_TOKEN_RE.finditer(normalized))


def normalized_relation_label(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    text = normalized[:-3] if normalized.endswith(".md") else normalized
    return re.sub(r"[^a-z0-9가-힣]+", "", text.casefold())


def page_lookup(pages: Sequence[WikiPage]) -> dict[str, WikiPage]:
    lookup: dict[str, WikiPage] = {}
    for page in pages:
        keys = {
            page.id,
            page.title,
            page.path,
            page.path.rsplit("/", maxsplit=1)[-1],
            Path(page.path).stem,
        }
        for key in keys:
            normalized = normalized_relation_label(key)
            if normalized and normalized not in lookup:
                lookup[normalized] = page
    return lookup


def seed_result(seed: _OrientationSeed) -> SearchResult:
    page = seed.page
    snippet = seed.evidence_text
    if len(snippet) > 280:
        snippet = snippet[:279].rstrip() + "..."
    return SearchResult(
        page_id=page.id,
        title=page.title,
        path=page.path,
        score=1.0 / (HYBRID_RRF_K + seed.rank),
        snippet=snippet,
        role=page.role,
        source_refs=page.source_refs,
        route="hybrid",
    )


def graph_prior_result(corpus: SearchCorpus, page_id: str) -> SearchResult:
    page = next(page for page in corpus.pages if page.id == page_id)
    snippet = " ".join((page.summary or page.text).split())
    if len(snippet) > 280:
        snippet = snippet[:279].rstrip() + "..."
    return SearchResult(
        page_id=page.id,
        title=page.title,
        path=page.path,
        score=0.0,
        snippet=snippet,
        role=page.role,
        source_refs=page.source_refs,
        route="hybrid",
    )


def graph_prior_results_by_vector_relevance(
    corpus: SearchCorpus,
    related_page_ids: Sequence[str],
    related_vector_results: Sequence[SearchResult],
    *,
    exact_scores_by_page: dict[str, float] | None = None,
) -> list[SearchResult]:
    pages_by_id = {page.id: page for page in corpus.pages}
    vector_rank_by_page = {
        result.page_id: rank for rank, result in enumerate(related_vector_results, start=1)
    }
    vector_score_by_page = {result.page_id: result.score for result in related_vector_results}
    candidate_ids = {page_id for page_id in related_page_ids if page_id in pages_by_id}

    def graph_prior_key(page_id: str) -> tuple[int, float, int, int, str, str]:
        page = pages_by_id[page_id]
        vector_rank = vector_rank_by_page.get(page_id)
        if vector_rank is None:
            return (1, 0.0, 0, role_rank(page.role), page.path, page.id)
        score = (
            exact_scores_by_page[page_id]
            if exact_scores_by_page is not None and page_id in exact_scores_by_page
            else float(vector_score_by_page[page_id])
        )
        return (
            0,
            -float(score),
            vector_rank,
            role_rank(page.role),
            page.path,
            page.id,
        )

    return [
        graph_prior_result(corpus, page_id)
        for page_id in sorted(candidate_ids, key=graph_prior_key)
    ]


def vector_result(
    page: WikiPage,
    *,
    score: float,
    record: VectorChunkRecord,
    snippet_chars: int | None,
) -> SearchResult:
    return SearchResult(
        page_id=page.id,
        title=page.title,
        path=page.path,
        score=round(score, 4),
        snippet=chunk_snippet(page, record, snippet_chars),
        role=page.role,
        source_refs=page.source_refs,
        route="vector",
    )


def chunk_snippet(page: WikiPage, record: VectorChunkRecord, snippet_chars: int | None) -> str:
    limit = snippet_limit(snippet_chars)
    if limit <= 0:
        return ""
    text = normalized_page_text(page.text)
    if 0 <= record.start < record.end <= len(text):
        start = max(0, record.start - 80)
        clean = " ".join(text[start : max(record.end, start + limit)].split())
    else:
        clean = " ".join((page.text or page.summary).split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."


def snippet_limit(value: int | None) -> int:
    if value is None:
        return 280
    return max(0, min(int(value), 2_000))


def filtered_record_positions_for_small_page_subset(
    vector_index: VectorIndex,
    pages_by_id: dict[str, WikiPage],
    excluded: set[str],
) -> tuple[int, ...] | None:
    effective_page_ids = {
        page.id for page in pages_by_id.values() if not page_is_excluded(page, excluded)
    }
    if not effective_page_ids:
        return ()
    index_page_ids = {record.page_id for record in vector_index.records}
    if index_page_ids <= effective_page_ids:
        return None
    positions: list[int] = []
    for position, record in enumerate(vector_index.records):
        if record.page_id not in effective_page_ids or record.norm <= 0:
            continue
        if len(positions) >= VECTOR_FILTERED_COPY_MAX_ROWS:
            return None
        positions.append(position)
    return tuple(positions)


def best_vector_records_by_page(
    vector_index: VectorIndex,
    *,
    pages_by_id: dict[str, WikiPage],
    excluded: set[str],
    query_vector: tuple[float, ...],
    query_norm: float,
    record_positions: Sequence[int] | None,
) -> dict[str, tuple[float, VectorChunkRecord]]:
    try:
        return best_vector_records_by_page_from_scores(
            vector_index,
            pages_by_id=pages_by_id,
            excluded=excluded,
            query_vector=query_vector,
            query_norm=query_norm,
            record_positions=record_positions,
            use_numpy=True,
        )
    except _VectorScoreUnavailable:
        return best_vector_records_by_page_from_scores(
            vector_index,
            pages_by_id=pages_by_id,
            excluded=excluded,
            query_vector=query_vector,
            query_norm=query_norm,
            record_positions=record_positions,
            use_numpy=False,
        )


def best_vector_records_by_page_from_scores(
    vector_index: VectorIndex,
    *,
    pages_by_id: dict[str, WikiPage],
    excluded: set[str],
    query_vector: tuple[float, ...],
    query_norm: float,
    record_positions: Sequence[int] | None,
    use_numpy: bool,
) -> dict[str, tuple[float, VectorChunkRecord]]:
    best_by_page: dict[str, tuple[float, VectorChunkRecord]] = {}
    for position, score in iter_vector_record_score_items(
        vector_index,
        query_vector,
        query_norm,
        record_positions=record_positions,
        use_numpy=use_numpy,
    ):
        record = vector_index.records[position]
        page = pages_by_id.get(record.page_id)
        if page is None or page_is_excluded(page, excluded) or record.norm <= 0:
            continue
        if not math.isfinite(score):
            continue
        previous = best_by_page.get(record.page_id)
        if previous is None or (score, -record.ordinal) > (previous[0], -previous[1].ordinal):
            best_by_page[record.page_id] = (score, record)
    return best_by_page


def vector_sort_key(
    score: float,
    page: WikiPage,
    record: VectorChunkRecord,
) -> tuple[float, int, str, str, int]:
    return (-score, role_rank(page.role), page.path, page.id, record.ordinal)


def vector_record_scores(
    vector_index: VectorIndex,
    query_vector: tuple[float, ...],
    query_norm: float,
    *,
    record_positions: Sequence[int] | None = None,
) -> tuple[float, ...]:
    positions = (
        tuple(range(len(vector_index.records)))
        if record_positions is None
        else tuple(record_positions)
    )
    if not positions:
        return ()
    try:
        return tuple(
            score
            for _position, score in iter_vector_record_score_items(
                vector_index,
                query_vector,
                query_norm,
                record_positions=positions,
                use_numpy=True,
            )
        )
    except _VectorScoreUnavailable:
        return tuple(
            score
            for _position, score in iter_vector_record_score_items(
                vector_index,
                query_vector,
                query_norm,
                record_positions=positions,
                use_numpy=False,
            )
        )


def iter_vector_record_score_items(
    vector_index: VectorIndex,
    query_vector: tuple[float, ...],
    query_norm: float,
    *,
    record_positions: Sequence[int] | None = None,
    use_numpy: bool,
) -> Iterator[tuple[int, float]]:
    matrix = vector_index.vector_matrix
    norms = vector_index.vector_norms
    if record_positions is None:
        if not vector_index.records:
            return
        if use_numpy and matrix is not None and norms is not None:
            yield from iter_numpy_cosine_score_range(
                matrix,
                norms,
                start=0,
                end=len(vector_index.records),
                query_vector=query_vector,
                query_norm=query_norm,
            )
            return
        for position in range(len(vector_index.records)):
            yield (
                position,
                python_vector_record_score(
                    vector_index,
                    position,
                    query_vector,
                    query_norm,
                ),
            )
        return
    positions = tuple(record_positions)
    if not positions:
        return
    if use_numpy and matrix is not None and norms is not None:
        yield from iter_numpy_vector_record_score_items(
            matrix,
            norms,
            positions,
            query_vector,
            query_norm,
            record_positions_were_filtered=record_positions is not None,
        )
        return
    for position in positions:
        yield position, python_vector_record_score(vector_index, position, query_vector, query_norm)


def iter_numpy_vector_record_score_items(
    matrix: Any,
    norms: Any,
    positions: Sequence[int],
    query_vector: tuple[float, ...],
    query_norm: float,
    *,
    record_positions_were_filtered: bool,
) -> Iterator[tuple[int, float]]:
    if not record_positions_were_filtered:
        yield from iter_numpy_cosine_score_range(
            matrix,
            norms,
            start=0,
            end=len(positions),
            query_vector=query_vector,
            query_norm=query_norm,
        )
        return
    if len(positions) <= VECTOR_FILTERED_COPY_MAX_ROWS:
        try:
            scores = numpy_cosine_scores(
                matrix[list(positions)],
                norms[list(positions)],
                query_vector,
                query_norm,
            )
        except Exception as exc:
            raise _VectorScoreUnavailable from exc
        if scores is None or len(scores) != len(positions):
            raise _VectorScoreUnavailable
        yield from zip(positions, scores, strict=True)
        return
    if not strictly_increasing(positions):
        raise _VectorScoreUnavailable
    for start, end in contiguous_position_ranges(positions):
        yield from iter_numpy_cosine_score_range(
            matrix,
            norms,
            start=start,
            end=end,
            query_vector=query_vector,
            query_norm=query_norm,
        )


def iter_numpy_cosine_score_range(
    matrix: Any,
    norms: Any,
    *,
    start: int,
    end: int,
    query_vector: tuple[float, ...],
    query_norm: float,
) -> Iterator[tuple[int, float]]:
    cursor = start
    while cursor < end:
        next_cursor = min(end, cursor + VECTOR_SCORE_BLOCK_ROWS)
        try:
            block_scores = numpy_cosine_scores(
                matrix[cursor:next_cursor],
                norms[cursor:next_cursor],
                query_vector,
                query_norm,
            )
        except Exception as exc:
            raise _VectorScoreUnavailable from exc
        if block_scores is None or len(block_scores) != next_cursor - cursor:
            raise _VectorScoreUnavailable
        for offset, score in enumerate(block_scores):
            yield cursor + offset, score
        cursor = next_cursor


def numpy_cosine_scores_for_positions(
    matrix: Any,
    norms: Any,
    positions: Sequence[int],
    query_vector: tuple[float, ...],
    query_norm: float,
) -> tuple[float, ...] | None:
    try:
        return tuple(
            score
            for _position, score in iter_numpy_vector_record_score_items(
                matrix,
                norms,
                positions,
                query_vector,
                query_norm,
                record_positions_were_filtered=True,
            )
        )
    except _VectorScoreUnavailable:
        return None


def strictly_increasing(positions: Sequence[int]) -> bool:
    return all(left < right for left, right in zip(positions, positions[1:], strict=False))


def contiguous_position_ranges(positions: Sequence[int]) -> Iterator[tuple[int, int]]:
    if not positions:
        return
    start = positions[0]
    previous = start
    for position in positions[1:]:
        if position == previous + 1:
            previous = position
            continue
        yield start, previous + 1
        start = position
        previous = position
    yield start, previous + 1


def python_vector_record_score(
    vector_index: VectorIndex,
    position: int,
    query_vector: tuple[float, ...],
    query_norm: float,
) -> float:
    try:
        record = vector_index.records[position]
    except IndexError as exc:
        raise VectorSearchError("Vector index record positions are inconsistent.") from exc
    if record.norm <= 0 or not math.isfinite(record.norm):
        return float("-inf")
    document_vector = vector_for_record_score(vector_index, position, record)
    return cosine(query_vector, query_norm, document_vector, record.norm)


def vector_for_record_score(
    vector_index: VectorIndex,
    position: int,
    record: VectorChunkRecord,
) -> tuple[float, ...]:
    if record.vector:
        if len(record.vector) != vector_index.dimension:
            raise VectorSearchError("Vector cache record has an unexpected dimension.")
        return record.vector
    matrix = vector_index.vector_matrix
    if matrix is None:
        raise VectorSearchError(
            "Vector cache record is missing in-memory vectors. Rebuild the vector cache."
        )
    try:
        row = matrix[position]
        raw = row.tolist() if hasattr(row, "tolist") else tuple(row)
    except Exception as exc:
        raise VectorSearchError(
            "Vector cache matrix row could not be read. Rebuild the vector cache."
        ) from exc
    try:
        return vector_tuple(raw, expected_dimension=vector_index.dimension)
    except VectorSearchError as exc:
        raise VectorSearchError(
            "Vector cache matrix row has invalid shape or values. Rebuild the vector cache."
        ) from exc


def numpy_cosine_scores(
    matrix: Any,
    norms: Any,
    query_vector: tuple[float, ...],
    query_norm: float,
) -> tuple[float, ...] | None:
    try:
        import numpy as np
    except ImportError:
        return None
    try:
        query = np.asarray(query_vector, dtype=np.float32)
        denominator = norms * np.float32(query_norm)
        with np.errstate(divide="ignore", invalid="ignore"):
            raw_scores = (matrix @ query) / denominator
        raw_scores = np.where(np.isfinite(raw_scores), raw_scores, -np.inf)
        return tuple(float(value) for value in raw_scores.tolist())
    except Exception:
        return None


def cosine(
    query_vector: tuple[float, ...],
    query_norm: float,
    document_vector: tuple[float, ...],
    document_norm: float,
) -> float:
    return sum(left * right for left, right in zip(query_vector, document_vector, strict=True)) / (
        query_norm * document_norm
    )


def vector_tuple(vector: Sequence[float], *, expected_dimension: int) -> tuple[float, ...]:
    result = tuple(float(value) for value in vector)
    if len(result) != expected_dimension:
        raise VectorSearchError(
            "Vector provider produced an embedding with an unexpected dimension."
        )
    if not all(math.isfinite(value) for value in result):
        raise VectorSearchError("Vector provider produced a non-finite embedding value.")
    return result


def float_vector(vector: Iterable[Any]) -> list[float]:
    return [float(value) for value in vector]


def vector_norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


def vector_matrix_for_records(records: Sequence[VectorChunkRecord]) -> Any | None:
    if not records:
        return None
    try:
        import numpy as np
    except ImportError:
        return None
    try:
        return np.asarray([record.vector for record in records], dtype=np.float32)
    except Exception:
        return None


def vector_norms_for_records(records: Sequence[VectorChunkRecord]) -> Any | None:
    if not records:
        return None
    try:
        import numpy as np
    except ImportError:
        return None
    try:
        return np.asarray([record.norm for record in records], dtype=np.float32)
    except Exception:
        return None


def vector_index_metadata_payload(index: VectorIndex) -> dict[str, Any]:
    return {
        "cache_schema": VECTOR_CACHE_SCHEMA_VERSION,
        "index_schema": VECTOR_INDEX_SCHEMA_ID,
        "metadata_schema": VECTOR_METADATA_SCHEMA_ID,
        "identity_digest": stable_json_digest(index.identity),
        "dimension": index.dimension,
        "chunk_count": len(index.records),
        "chunks": [
            {
                "page_id": record.page_id,
                "chunk_id": record.chunk_id,
                "ordinal": record.ordinal,
                "start": record.start,
                "end": record.end,
                "heading_hash": record.heading_hash,
                "page_content_hash": record.page_content_hash,
            }
            for record in index.records
        ],
    }


def vector_index_from_sidecars(
    *,
    metadata: Any,
    vector_path: Path,
    vector_ref: dict[str, Any],
    identity: dict[str, str | int],
    dimension: int,
    cache_hit: bool,
) -> VectorIndex | None:
    if not isinstance(metadata, dict):
        return None
    if metadata.get("cache_schema") != VECTOR_CACHE_SCHEMA_VERSION:
        return None
    if metadata.get("index_schema") != VECTOR_INDEX_SCHEMA_ID:
        return None
    if metadata.get("metadata_schema") != VECTOR_METADATA_SCHEMA_ID:
        return None
    if metadata.get("identity_digest") != stable_json_digest(identity):
        return None
    if metadata.get("dimension") != dimension:
        return None
    raw_chunks = metadata.get("chunks")
    if not isinstance(raw_chunks, list):
        return None
    if metadata.get("chunk_count") != len(raw_chunks):
        return None
    matrix = load_vector_matrix(
        vector_path,
        vector_ref,
        expected_shape=(len(raw_chunks), dimension),
    )
    if matrix is None:
        return None
    norms = vector_norms_for_matrix(matrix)
    if norms is None or len(norms) != len(raw_chunks):
        return None
    records: list[VectorChunkRecord] = []
    for index, raw in enumerate(raw_chunks):
        if not isinstance(raw, dict):
            return None
        if vector_metadata_contains_private_fields(raw):
            return None
        try:
            record = VectorChunkRecord(
                page_id=str(raw["page_id"]),
                chunk_id=str(raw["chunk_id"]),
                ordinal=int(raw["ordinal"]),
                start=int(raw["start"]),
                end=int(raw["end"]),
                heading_hash=str(raw["heading_hash"]),
                page_content_hash=str(raw["page_content_hash"]),
                vector=(),
                norm=float(norms[index]),
            )
        except (KeyError, TypeError, ValueError):
            return None
        if record.ordinal < 0 or record.start < 0 or record.end < record.start:
            return None
        if not record.page_id or not record.chunk_id or not math.isfinite(record.norm):
            return None
        records.append(record)
    return VectorIndex(
        identity=identity,
        records=tuple(records),
        dimension=dimension,
        cache_hit=cache_hit,
        vector_matrix=matrix,
        vector_norms=norms,
    )


def vector_metadata_contains_private_fields(raw: dict[Any, Any]) -> bool:
    return bool({"text", "snippet", "query", "vector", "path", "source_refs"} & set(raw))


def atomic_vector_matrix_write(record_dir: Path, index: VectorIndex) -> tuple[Path, str]:
    matrix = vector_matrix_array_for_records(index.records, dimension=index.dimension)
    tmp_path = record_dir / (f".{VECTOR_VECTOR_FILE_PREFIX}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with tmp_path.open("wb") as handle:
            numpy_save(handle, matrix)
            handle.flush()
            os.fsync(handle.fileno())
        checksum = sha256_file(tmp_path)
        final_path = sidecar_content_path(
            record_dir,
            prefix=VECTOR_VECTOR_FILE_PREFIX,
            checksum=checksum,
            suffix=VECTOR_VECTOR_FILE_SUFFIX,
        )
        os.replace(tmp_path, final_path)
        return final_path, checksum
    finally:
        with suppress(FileNotFoundError):
            tmp_path.unlink()


def vector_matrix_array_for_records(records: Sequence[VectorChunkRecord], *, dimension: int) -> Any:
    np = numpy_or_vector_error()
    if records:
        matrix = np.asarray([record.vector for record in records], dtype=np.float32)
    else:
        matrix = np.empty((0, dimension), dtype=np.float32)
    if tuple(matrix.shape) != (len(records), dimension):
        raise VectorSearchError("Vector provider produced an invalid vector matrix shape.")
    return matrix


def numpy_save(handle: Any, matrix: Any) -> None:
    np = numpy_or_vector_error()
    np.save(handle, matrix, allow_pickle=False)


def numpy_or_vector_error() -> Any:
    try:
        import numpy as np
    except ImportError as exc:
        raise VectorSearchError(
            'Vector cache requires NumPy. Install it with `pip install "llmwiki-serve[vector]"`.'
        ) from exc
    return np


def load_vector_matrix(
    vector_path: Path,
    vector_ref: dict[str, Any],
    *,
    expected_shape: tuple[int, int],
) -> Any | None:
    if vector_ref.get("shape") != list(expected_shape):
        return None
    if vector_ref.get("dtype") != "float32":
        return None
    try:
        import numpy as np
    except ImportError:
        return None
    try:
        matrix = np.load(vector_path, allow_pickle=False, mmap_mode="r")
    except Exception:
        return None
    if tuple(getattr(matrix, "shape", ())) != expected_shape:
        return None
    if getattr(matrix, "dtype", None) != np.dtype(np.float32):
        return None
    return matrix


def vector_norms_for_matrix(matrix: Any) -> Any | None:
    try:
        import numpy as np
    except ImportError:
        return None
    try:
        norms = np.linalg.norm(matrix, axis=1).astype(np.float32)
        if not np.all(np.isfinite(norms)):
            return None
        return norms
    except Exception:
        return None


def sidecar_content_path(record_dir: Path, *, prefix: str, checksum: str, suffix: str) -> Path:
    return record_dir / f"{prefix}.{checksum[:16]}{suffix}"


def sidecar_manifest_ref(manifest: dict[str, Any], name: str) -> dict[str, Any] | None:
    sidecars = manifest.get("sidecars")
    if not isinstance(sidecars, dict):
        return None
    ref = sidecars.get(name)
    if not isinstance(ref, dict):
        return None
    file_name = ref.get("file")
    checksum = ref.get("sha256")
    if not isinstance(file_name, str) or not isinstance(checksum, str):
        return None
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        return None
    return ref


def sidecar_file_path(record_dir: Path, file_name: str) -> Path | None:
    if not file_name or "/" in file_name or "\\" in file_name or ":" in file_name:
        return None
    candidate = Path(file_name)
    if candidate.name != file_name or candidate.is_absolute():
        return None
    return record_dir / file_name


def sidecar_identity_fields(identity: dict[str, str | int]) -> dict[str, str | int]:
    return {
        "cache_schema": VECTOR_CACHE_SCHEMA_VERSION,
        "identity_digest": stable_json_digest(identity),
        "provider_id": identity["provider_id"],
        "provider_artifact_fingerprint": identity["provider_artifact_fingerprint"],
        "model_id": identity["model_id"],
        "model_revision": identity["model_revision"],
        "content_hash": identity["content_hash"],
        "visibility_scope": identity["visibility_scope"],
        "text_schema": VECTOR_TEXT_SCHEMA_ID,
        "index_schema": VECTOR_INDEX_SCHEMA_ID,
    }


def sidecar_ref_matches_identity(ref: dict[str, Any], identity: dict[str, str | int]) -> bool:
    expected = sidecar_identity_fields(identity)
    return all(ref.get(key) == value for key, value in expected.items())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_vector_cache_dir(root: Path, cache_dir: Path | None) -> Path:
    candidate = (cache_dir or default_vector_cache_dir()).expanduser()
    return resolve_external_cache_dir(
        root=root,
        candidate=candidate,
        label="vector cache directory",
    )


def resolve_vector_model_cache_dir(root: Path, model_cache_dir: Path | None) -> Path | None:
    if model_cache_dir is None:
        return None
    return resolve_external_cache_dir(
        root=root,
        candidate=model_cache_dir.expanduser(),
        label="vector model cache directory",
    )


def resolve_external_cache_dir(*, root: Path, candidate: Path, label: str) -> Path:
    lexical_candidate = lexical_absolute_path(candidate)
    lexical_root = lexical_absolute_path(root.expanduser())
    resolved_candidate = candidate.resolve(strict=False)
    resolved_root = root.expanduser().resolve(strict=False)
    if same_or_relative_to(lexical_candidate, lexical_root) or same_or_relative_to(
        resolved_candidate, resolved_root
    ):
        raise VectorSearchError(f"{label} must be outside the served source root")
    return resolved_candidate


def default_vector_cache_dir() -> Path:
    if os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        if local_app_data:
            return Path(local_app_data) / "llmwiki-serve" / "vector-cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "llmwiki-serve" / "vector-cache"
    xdg_cache_home = os.getenv("XDG_CACHE_HOME")
    if xdg_cache_home:
        return Path(xdg_cache_home).expanduser() / "llmwiki-serve" / "vector-cache"
    return Path.home() / ".cache" / "llmwiki-serve" / "vector-cache"


def lexical_absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(path))


def same_or_relative_to(child: Path, parent: Path) -> bool:
    child_text = containment_path_text(child)
    parent_text = containment_path_text(parent)
    try:
        return os.path.commonpath([child_text, parent_text]) == parent_text
    except ValueError:
        return False


def containment_path_text(path: Path) -> str:
    text = os.path.normcase(os.path.abspath(str(path)))
    if os.name == "nt" or sys.platform == "darwin":
        return text.casefold()
    return text


@contextmanager
def vector_cache_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + VECTOR_LOCK_TIMEOUT_SECONDS
    acquired = False
    while not acquired:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"pid": os.getpid(), "created_at": time.time()}))
            acquired = True
        except FileExistsError:
            remove_stale_lock(lock_path)
            if time.monotonic() >= deadline:
                raise VectorSearchError("vector cache is busy; retry the request") from None
            time.sleep(VECTOR_LOCK_RETRY_SECONDS)
    try:
        yield
    finally:
        with suppress(FileNotFoundError):
            lock_path.unlink()


def remove_stale_lock(lock_path: Path) -> None:
    try:
        age = time.time() - lock_path.stat().st_mtime
    except OSError:
        return
    if age <= VECTOR_LOCK_STALE_SECONDS:
        return
    if lock_owner_is_running(lock_path):
        return
    with suppress(OSError):
        lock_path.unlink()


def lock_owner_is_running(lock_path: Path) -> bool:
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        pid = int(payload.get("pid") or 0)
        created_at = float(payload.get("created_at") or 0.0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        process = psutil.Process(pid)
        if created_at > 0 and process.create_time() > created_at + 1.0:
            return False
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def atomic_json_write(path: Path, payload: Any) -> None:
    atomic_bytes_write(path, stable_json_bytes(payload))


def atomic_bytes_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with tmp_path.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def stable_json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def stable_json_digest(payload: Any) -> str:
    return hashlib.sha256(stable_json_bytes(payload)).hexdigest()


def stable_text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pages_content_hash(pages: Sequence[WikiPage]) -> str:
    payload = [
        {
            "id": page.id,
            "title": page.title,
            "role": page.role,
            "text": normalized_page_text(page.text),
            "summary": page.summary,
            "review_state": page.review_state,
            "status": page.status,
        }
        for page in pages
    ]
    return f"sha256:{stable_json_digest(payload)}"


def page_content_hash(page: WikiPage) -> str:
    payload = {
        "id": page.id,
        "title": page.title,
        "role": page.role,
        "text": normalized_page_text(page.text),
        "summary": page.summary,
        "review_state": page.review_state,
        "status": page.status,
    }
    return f"sha256:{stable_json_digest(payload)}"


def normalized_page_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def safe_model_label(value: str) -> str:
    text = " ".join(value.strip().split())
    if not text:
        return "unknown"
    if is_local_model_path_label(text):
        return f"digest:{stable_text_digest(text)[:16]}"
    return text


def is_local_model_path_label(value: str) -> bool:
    normalized = value.strip()
    if not normalized:
        return False
    if normalized.lower().startswith("file:"):
        return True
    if normalized.startswith(("/", "~", "./", "../", ".\\", "..\\")):
        return True
    if "\\" in normalized:
        return True
    return re.match(r"^[A-Za-z]:/", normalized) is not None


def fastembed_model_metadata(text_embedding_type: Any, model_name: str) -> dict[str, Any] | None:
    for metadata in text_embedding_type.list_supported_models():
        if (
            isinstance(metadata, dict)
            and str(metadata.get("model") or "").lower() == model_name.lower()
        ):
            return metadata
    return None


def revision_from_fastembed_instance(instance: Any) -> str:
    model = getattr(instance, "model", None)
    model_dir = str(getattr(model, "_model_dir", "") or "")
    if not model_dir:
        return ""
    parts = Path(model_dir).parts
    if "snapshots" not in parts:
        return ""
    index = parts.index("snapshots")
    if index + 1 >= len(parts):
        return ""
    revision = parts[index + 1]
    if re.fullmatch(r"[0-9a-f]{40}", revision):
        return f"hf:{revision}"
    return ""
