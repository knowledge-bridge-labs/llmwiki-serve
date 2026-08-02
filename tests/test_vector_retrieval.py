from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import sys
import threading
import time
import types
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import llmwiki_serve.vector as vector_module
from llmwiki_serve.api import MCP_STREAM_PATH, create_app, create_mcp_stream_server
from llmwiki_serve.cli import app as cli_app
from llmwiki_serve.errors import LlmWikiUserError
from llmwiki_serve.managed_context import ManagedContextConfig
from llmwiki_serve.models import SearchResult, WikiPage
from llmwiki_serve.search import search_corpus
from llmwiki_serve.service import LlmWikiService, managed_context_hit_page_ids
from llmwiki_serve.vector import (
    DEFAULT_FASTEMBED_MODEL,
    HYBRID_CANDIDATE_DEPTH_CAP,
    HYBRID_CANDIDATE_DEPTH_MIN,
    HYBRID_CANDIDATE_DEPTH_MULTIPLIER,
    HYBRID_ORIENTATION_CANDIDATE_LIMIT,
    HYBRID_RELATED_PER_SEED_LIMIT,
    HYBRID_RRF_K,
    VECTOR_CACHE_SCHEMA_VERSION,
    VECTOR_INDEX_SCHEMA_ID,
    VECTOR_METADATA_SCHEMA_ID,
    VECTOR_TEXT_SCHEMA_ID,
    FastEmbedProvider,
    VectorConfig,
    VectorIndexCache,
    build_vector_chunks,
    hybrid_candidate_depth,
    plain_hybrid_search_results,
    relation_label_visible,
    resolve_vector_cache_dir,
    resolve_vector_model_cache_dir,
    safe_model_label,
    vector_cache_lock,
)


class FakeEmbeddingProvider:
    provider_id = "fake"
    model_id = "fake-multilingual-model"
    model_revision = "fake-revision-1"
    dimension = 3
    distance_metric: Literal["cosine"] = "cosine"

    def __init__(self) -> None:
        self.document_calls = 0
        self.query_calls = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls += 1
        return [fake_vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return fake_vector(text)

    def safe_metadata(self) -> dict[str, str | int]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "dimension": self.dimension,
            "distance_metric": self.distance_metric,
        }


class BadDimensionProvider(FakeEmbeddingProvider):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls += 1
        return [[1.0, 0.0] for _text in texts]


class KeywordEmbeddingProvider(FakeEmbeddingProvider):
    def __init__(self, vectors: list[tuple[str, list[float]]]) -> None:
        super().__init__()
        self.vectors = [(keyword.casefold(), vector) for keyword, vector in vectors]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls += 1
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        lowered = text.casefold()
        for keyword, vector in self.vectors:
            if keyword in lowered:
                return vector
        return [0.1, 0.1, 0.1]


def test_vector_search_uses_fake_provider_cache_and_redacted_capabilities(tmp_path: Path) -> None:
    root = sample_vector_wiki(tmp_path / "wiki")
    cache_dir = tmp_path / "vector-cache"
    provider = FakeEmbeddingProvider()
    service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=cache_dir),
        vector_provider=provider,
    )

    results = service.search("invoice refund", mode="vector", limit=1, fields=["page_id", "route"])

    assert results == [{"page_id": "billing", "route": "vector"}]
    assert provider.document_calls == 1
    assert provider.query_calls == 1
    capabilities = set(service.manifest().capabilities)
    assert {
        "llmwiki_retrieval_v1",
        "llmwiki_search_mode_lexical",
        "llmwiki_search_mode_literal",
        "llmwiki_search_mode_vector",
        "llmwiki_search_mode_hybrid",
    } <= capabilities

    encoded_cache = "\n".join(
        path.read_text(encoding="utf-8") for path in cache_dir.rglob("*.json")
    )
    assert VECTOR_CACHE_SCHEMA_VERSION in encoded_cache
    assert VECTOR_INDEX_SCHEMA_ID in encoded_cache
    assert "semantic refund policy" not in encoded_cache
    assert "invoice refund" not in encoded_cache
    assert str(root) not in encoded_cache
    assert not any(path.name.startswith("vectors") for path in root.rglob("*"))
    assert list(cache_dir.rglob("vectors.*.npy"))
    assert list(cache_dir.rglob("chunks.*.json"))

    second_provider = FakeEmbeddingProvider()
    second_service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=cache_dir),
        vector_provider=second_provider,
    )
    assert (
        second_service.search("invoice refund", mode="vector", limit=1)[0]["page_id"] == "billing"
    )
    assert second_provider.document_calls == 0
    assert second_provider.query_calls == 1


def test_vector_cache_binary_sidecars_round_trip_without_raw_text(tmp_path: Path) -> None:
    root = sample_vector_wiki(tmp_path / "wiki")
    cache_dir = tmp_path / "cache"
    provider = FakeEmbeddingProvider()

    service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=cache_dir),
        vector_provider=provider,
    )
    service.search("invoice refund", mode="vector")

    manifest_path = single_path(cache_dir.rglob("index.json"))
    manifest = read_json(manifest_path)
    sidecars = manifest["sidecars"]
    assert manifest["cache_schema"] == VECTOR_CACHE_SCHEMA_VERSION
    assert manifest["identity"]["cache_schema"] == VECTOR_CACHE_SCHEMA_VERSION
    assert manifest["identity"]["index_schema"] == VECTOR_INDEX_SCHEMA_ID
    assert sidecars["vectors"]["format"] == "npy"
    assert sidecars["vectors"]["dtype"] == "float32"
    assert sidecars["vectors"]["shape"] == [2, provider.dimension]
    assert sidecars["metadata"]["schema"] == VECTOR_METADATA_SCHEMA_ID
    assert sidecars["metadata"]["chunk_count"] == 2
    assert sidecars["vectors"]["identity_digest"] == manifest["identity_digest"]
    assert sidecars["metadata"]["identity_digest"] == manifest["identity_digest"]

    vector_path = manifest_path.parent / sidecars["vectors"]["file"]
    metadata_path = manifest_path.parent / sidecars["metadata"]["file"]
    assert vector_path.suffix == ".npy"
    assert vector_path.is_file()
    assert metadata_path.is_file()
    metadata = read_json(metadata_path)
    assert metadata["metadata_schema"] == VECTOR_METADATA_SCHEMA_ID
    assert all(
        not ({"text", "snippet", "query", "vector", "norm", "path", "source_refs"} & set(chunk))
        for chunk in metadata["chunks"]
    )
    cache_bytes = b"".join(path.read_bytes() for path in cache_dir.rglob("*") if path.is_file())
    assert b"semantic refund policy" not in cache_bytes
    assert bytes(str(root), "utf-8") not in cache_bytes

    cached_provider = FakeEmbeddingProvider()
    cached_service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=cache_dir),
        vector_provider=cached_provider,
    )
    assert cached_service.search("invoice refund", mode="vector")[0]["page_id"] == "billing"
    assert cached_provider.document_calls == 0
    assert cached_provider.query_calls == 1


def test_vector_cache_hit_python_fallback_scores_from_loaded_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = sample_vector_wiki(tmp_path / "wiki")
    cache_dir = tmp_path / "cache"
    builder = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=cache_dir),
        vector_provider=FakeEmbeddingProvider(),
    )
    assert builder.search("invoice refund", mode="vector")[0]["page_id"] == "billing"

    cached_provider = FakeEmbeddingProvider()
    cached_service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=cache_dir),
        vector_provider=cached_provider,
    )
    monkeypatch.setattr(vector_module, "numpy_cosine_scores", lambda *_args, **_kwargs: None)

    result = cached_service.search(
        "invoice refund",
        mode="vector",
        fields=["page_id", "route"],
    )

    assert result[0] == {"page_id": "billing", "route": "vector"}
    assert cached_provider.document_calls == 0
    assert cached_provider.query_calls == 1


@pytest.mark.parametrize(
    "mutation",
    ["old-schema", "hash", "shape", "dtype", "partial-vector", "raw-text-metadata"],
)
def test_vector_cache_rebuilds_malformed_binary_sidecars(
    tmp_path: Path,
    mutation: str,
) -> None:
    root = sample_vector_wiki(tmp_path / f"wiki-{mutation}")
    cache_dir = tmp_path / f"cache-{mutation}"
    service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=cache_dir),
        vector_provider=FakeEmbeddingProvider(),
    )
    service.search("invoice refund", mode="vector")

    manifest_path = single_path(cache_dir.rglob("index.json"))
    manifest = read_json(manifest_path)
    sidecars = manifest["sidecars"]
    if mutation == "old-schema":
        manifest["cache_schema"] = "llmwiki-vector-cache-v1"
    elif mutation == "hash":
        sidecars["vectors"]["sha256"] = "0" * 64
    elif mutation == "shape":
        sidecars["vectors"]["shape"] = [999, 999]
    elif mutation == "dtype":
        sidecars["vectors"]["dtype"] = "float64"
    elif mutation == "partial-vector":
        vector_path = manifest_path.parent / sidecars["vectors"]["file"]
        vector_path.write_bytes(b"partial-npy")
        sidecars["vectors"]["sha256"] = hashlib.sha256(b"partial-npy").hexdigest()
    elif mutation == "raw-text-metadata":
        metadata_path = manifest_path.parent / sidecars["metadata"]["file"]
        metadata = read_json(metadata_path)
        metadata["chunks"][0]["text"] = "semantic refund policy"
        metadata_bytes = json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        metadata_path.write_bytes(metadata_bytes)
        sidecars["metadata"]["sha256"] = hashlib.sha256(metadata_bytes).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    rebuild_provider = FakeEmbeddingProvider()
    rebuild_service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=cache_dir),
        vector_provider=rebuild_provider,
    )
    assert rebuild_service.search("invoice refund", mode="vector")[0]["page_id"] == "billing"
    assert rebuild_provider.document_calls == 1


def test_vector_cache_readers_ignore_unpublished_draft_sidecars(tmp_path: Path) -> None:
    root = sample_vector_wiki(tmp_path / "wiki")
    cache_dir = tmp_path / "cache"
    service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=cache_dir),
        vector_provider=FakeEmbeddingProvider(),
    )
    service.search("invoice refund", mode="vector")
    manifest_path = single_path(cache_dir.rglob("index.json"))
    record_dir = manifest_path.parent
    (record_dir / ".vectors.concurrent-writer.tmp").write_bytes(b"partial")
    (record_dir / "vectors.unpublished.npy").write_bytes(b"not referenced")
    (record_dir / "chunks.unpublished.json").write_text("{corrupt", encoding="utf-8")

    reader_provider = FakeEmbeddingProvider()
    reader_service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=cache_dir),
        vector_provider=reader_provider,
    )
    assert reader_service.search("invoice refund", mode="vector")[0]["page_id"] == "billing"
    assert reader_provider.document_calls == 0


def test_vector_chunking_is_deterministic_heading_aware_and_private_term_free() -> None:
    lf_page = vector_page(
        "alpha",
        "notes/alpha.md",
        "# Alpha\n\nIntro paragraph with local evidence.\n\n## Detail\n\n" + "word " * 260,
        source_refs=["SECRET-SRC"],
    )
    crlf_page = lf_page.model_copy(update={"text": lf_page.text.replace("\n", "\r\n")})

    lf_chunks = build_vector_chunks([lf_page])
    crlf_chunks = build_vector_chunks([crlf_page])

    assert [chunk.chunk_id for chunk in lf_chunks] == [chunk.chunk_id for chunk in crlf_chunks]
    assert len(lf_chunks) >= 2
    assert all(len(chunk.text) <= 1_200 for chunk in lf_chunks)
    assert any("Alpha > Detail" in chunk.text for chunk in lf_chunks)
    assert all("notes/alpha.md" not in chunk.text for chunk in lf_chunks)
    assert all("SECRET-SRC" not in chunk.text for chunk in lf_chunks)
    assert {chunk.page_content_hash for chunk in lf_chunks} == {
        chunk.page_content_hash for chunk in crlf_chunks
    }
    assert build_vector_chunks([vector_page("empty", "empty.md", "# Empty\n")]) == []


def test_disabled_unknown_and_min_score_errors_do_not_fallback(tmp_path: Path) -> None:
    root = sample_vector_wiki(tmp_path / "wiki")
    default_service = LlmWikiService(root)

    with pytest.raises(LlmWikiUserError, match="not enabled"):
        default_service.search("invoice refund", mode="vector")
    with pytest.raises(LlmWikiUserError, match="min_score is not supported"):
        default_service.search("invoice refund", mode="hybrid", min_score=0.1)
    with pytest.raises(LlmWikiUserError, match="unknown search mode"):
        default_service.search("invoice refund", mode="semantic")  # type: ignore[arg-type]

    client = TestClient(create_app(root))
    disabled = client.post("/search", json={"query": "invoice refund", "mode": "vector"})
    assert disabled.status_code == 400
    assert "not enabled" in disabled.json()["detail"]
    invalid = client.post("/search", json={"query": "invoice refund", "mode": "semantic"})
    assert invalid.status_code == 422

    mcp_disabled = client.post(
        "/mcp",
        json=mcp_call_payload("llmwiki_search", {"query": "invoice refund", "mode": "vector"}),
    ).json()
    assert mcp_disabled["error"]["code"] == -32602
    assert "not enabled" in mcp_disabled["error"]["message"]
    mcp_unknown = client.post(
        "/mcp",
        json=mcp_call_payload("llmwiki_search", {"query": "invoice refund", "mode": "semantic"}),
    ).json()
    assert mcp_unknown["error"] == {
        "code": -32602,
        "message": "unknown search mode: expected lexical, literal, vector, or hybrid",
    }
    mcp_invalid_min_score = client.post(
        "/mcp",
        json=mcp_call_payload(
            "llmwiki_search",
            {"query": "invoice refund", "mode": "lexical", "min_score": "abc"},
        ),
    ).json()
    assert mcp_invalid_min_score["error"] == {
        "code": -32602,
        "message": "min_score must be a non-negative number",
    }
    mcp_vector_min_score = client.post(
        "/mcp",
        json=mcp_call_payload(
            "llmwiki_search",
            {"query": "invoice refund", "mode": "vector", "min_score": 0.1},
        ),
    ).json()
    assert mcp_vector_min_score["error"]["code"] == -32602
    assert "min_score is not supported" in mcp_vector_min_score["error"]["message"]

    cli_result = CliRunner().invoke(
        cli_app, ["search", str(root), "invoice refund", "--mode", "vector"]
    )
    assert cli_result.exit_code == 1
    assert "not enabled" in cli_result.output
    assert "llmwiki-serve[vector]" in cli_result.output


def test_public_schema_does_not_expose_provider_model_cache_or_download(tmp_path: Path) -> None:
    root = sample_vector_wiki(tmp_path / "wiki")
    properties = create_app(root).openapi()["components"]["schemas"]["QueryRequest"]["properties"]

    assert properties["mode"]["enum"] == ["lexical", "literal", "vector", "hybrid"]
    assert not {
        "vector_provider",
        "vector_model",
        "vector_cache_dir",
        "vector_model_cache_dir",
        "vector_model_download",
    } & set(properties)


def test_default_lexical_path_does_not_import_fastembed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = sample_vector_wiki(tmp_path / "wiki")
    monkeypatch.delitem(sys.modules, "fastembed", raising=False)

    result = LlmWikiService(root).search("Alpha overview")

    assert result[0]["page_id"] == "index"
    assert "fastembed" not in sys.modules


def test_missing_fastembed_extra_reports_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import_module = vector_module.importlib.import_module

    def fake_import_module(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "fastembed":
            raise ImportError("no fastembed")
        return original_import_module(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "fastembed", raising=False)
    monkeypatch.setattr(vector_module.importlib, "import_module", fake_import_module)

    with pytest.raises(LlmWikiUserError, match="llmwiki-serve\\[vector\\]"):
        FastEmbedProvider(model_name="explicit/model", model_download="never")


def test_fastembed_provider_uses_explicit_model_and_operator_download_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    fake_module = types.ModuleType("fastembed")

    class FakeTextEmbedding:
        @classmethod
        def list_supported_models(cls) -> list[dict[str, Any]]:
            return [
                {
                    "model": "explicit/model",
                    "sources": {"hf": "example/model"},
                    "dim": 3,
                }
            ]

        def __init__(self, **kwargs: Any) -> None:
            calls.append(kwargs)
            self.model = types.SimpleNamespace(
                _model_dir=(
                    "cache/models--example--model/snapshots/"
                    "1111111111111111111111111111111111111111"
                )
            )

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0, 0.0] for _text in texts]

        def query_embed(self, text: str) -> list[list[float]]:
            return [[1.0, 0.0, 0.0]]

    fake_module.TextEmbedding = FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fake_module)
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: {"fastembed": "0.8.0", "numpy": "2.4.6", "onnxruntime": "1.22.0"}[name],
    )

    never_provider = FastEmbedProvider(model_name="explicit/model", model_download="never")
    allow_provider = FastEmbedProvider(model_name="explicit/model", model_download="allow")

    assert calls[0]["model_name"] == "explicit/model"
    assert calls[0]["local_files_only"] is True
    assert calls[1]["local_files_only"] is False
    assert never_provider.model_revision == "hf:1111111111111111111111111111111111111111"
    assert allow_provider.dimension == 3
    assert never_provider.safe_metadata()["onnxruntime_version"] == "1.22.0"


def test_fastembed_safe_metadata_redacts_local_model_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = types.ModuleType("fastembed")

    class FakeTextEmbedding:
        @classmethod
        def list_supported_models(cls) -> list[dict[str, Any]]:
            return [
                {
                    "model": "/home/operator/private-model",
                    "sources": {"hf": "example/model"},
                    "dim": 3,
                }
            ]

        def __init__(self, **_kwargs: Any) -> None:
            self.model = types.SimpleNamespace(_model_dir="")

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0, 0.0] for _text in texts]

        def query_embed(self, text: str) -> list[list[float]]:
            return [[1.0, 0.0, 0.0]]

    fake_module.TextEmbedding = FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fake_module)
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: {"fastembed": "0.8.0", "numpy": "2.4.6", "onnxruntime": "1.22.0"}[name],
    )

    provider = FastEmbedProvider(
        model_name="/home/operator/private-model",
        model_download="never",
    )

    metadata = provider.safe_metadata()
    assert metadata["model_id"] == safe_model_label("/home/operator/private-model")
    assert metadata["onnxruntime_version"] == "1.22.0"
    assert str(metadata["model_id"]).startswith("digest:")
    assert "private-model" not in str(metadata["model_id"])


def test_fastembed_runtime_artifact_fingerprint_participates_in_cache_identity(
    tmp_path: Path,
) -> None:
    root = sample_vector_wiki(tmp_path / "wiki")
    pages = [vector_page("alpha", "alpha.md", "Alpha")]
    cache = VectorIndexCache(root=root, cache_dir=tmp_path / "cache")

    class RuntimeFingerprintProvider(FakeEmbeddingProvider):
        provider_id = "fastembed"

        def __init__(self, fastembed_version: str, onnxruntime_version: str = "1.22.0") -> None:
            super().__init__()
            self.fastembed_version = fastembed_version
            self.onnxruntime_version = onnxruntime_version

        def safe_metadata(self) -> dict[str, str | int]:
            return {
                **super().safe_metadata(),
                "provider_id": self.provider_id,
                "fastembed_version": self.fastembed_version,
                "numpy_version": r"C:\Users\operator\private-numpy",
                "onnxruntime_version": self.onnxruntime_version,
            }

    first = cache.identity(
        provider=RuntimeFingerprintProvider("0.8.0"),
        pages=pages,
        source_id="source",
        projection_signature="projection",
        visibility_scope="approved",
    )
    second = cache.identity(
        provider=RuntimeFingerprintProvider("0.9.0"),
        pages=pages,
        source_id="source",
        projection_signature="projection",
        visibility_scope="approved",
    )
    third = cache.identity(
        provider=RuntimeFingerprintProvider("0.8.0", "1.23.0"),
        pages=pages,
        source_id="source",
        projection_signature="projection",
        visibility_scope="approved",
    )

    assert first["provider_artifact_fingerprint"] != second["provider_artifact_fingerprint"]
    assert first["provider_artifact_fingerprint"] != third["provider_artifact_fingerprint"]
    encoded = json.dumps(first, sort_keys=True)
    assert "0.8.0" not in encoded
    assert "1.22.0" not in encoded
    assert "private-numpy" not in encoded
    assert "operator" not in encoded


@pytest.mark.parametrize(
    "model_name",
    [
        "/home/operator/private-model",
        r"C:\Users\operator\private-model",
        "file:///home/operator/private-model",
    ],
)
def test_fastembed_unsupported_model_error_redacts_paths(
    model_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = types.ModuleType("fastembed")

    class FakeTextEmbedding:
        @classmethod
        def list_supported_models(cls) -> list[dict[str, Any]]:
            return [{"model": "supported/model", "sources": {"hf": "example/model"}, "dim": 3}]

    fake_module.TextEmbedding = FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fake_module)
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: {"fastembed": "0.8.0", "numpy": "2.4.6", "onnxruntime": "1.22.0"}[name],
    )

    with pytest.raises(LlmWikiUserError) as excinfo:
        FastEmbedProvider(model_name=model_name, model_download="never")

    message = str(excinfo.value)
    assert "not supported" in message
    assert "digest:" in message
    assert model_name not in message
    assert "private-model" not in message


def test_fastembed_provider_suppresses_raw_provider_stderr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_module = types.ModuleType("fastembed")
    cache_dir = tmp_path / "private-cache"

    class FakeTextEmbedding:
        @classmethod
        def list_supported_models(cls) -> list[dict[str, Any]]:
            return [{"model": "explicit/model", "sources": {"hf": "example/model"}, "dim": 3}]

        def __init__(self, **_kwargs: Any) -> None:
            print(f"raw provider path {cache_dir}", file=sys.stderr)
            raise RuntimeError("provider failure")

    fake_module.TextEmbedding = FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", fake_module)
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: {"fastembed": "0.8.0", "numpy": "2.4.6", "onnxruntime": "1.22.0"}[name],
    )

    with pytest.raises(LlmWikiUserError, match="Pre-cache the configured FastEmbed model"):
        FastEmbedProvider(
            model_name="explicit/model",
            model_cache_dir=cache_dir,
            model_download="never",
        )

    captured = capsys.readouterr()
    assert str(cache_dir) not in captured.err


def test_vector_and_hybrid_work_through_http_mcp_streamable_and_managed_context(
    tmp_path: Path,
) -> None:
    root = sample_vector_wiki(tmp_path / "wiki")
    provider = FakeEmbeddingProvider()
    app = create_app(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
        vector_provider=provider,
        managed_context=ManagedContextConfig(enabled=True, state_dir=tmp_path / "state"),
    )
    client = TestClient(app)

    http_vector = client.post(
        "/search",
        json={"query": "invoice refund", "mode": "vector", "fields": "page_id,route"},
    ).json()
    http_hybrid = client.post(
        "/query",
        json={"query": "invoice refund", "mode": "hybrid", "fields": "page_id,route"},
    ).json()
    http_min_score = client.post(
        "/search",
        json={"query": "invoice refund", "mode": "vector", "min_score": 0.1},
    )
    mcp_vector = client.post(
        "/mcp",
        json=mcp_call_payload(
            "llmwiki_search",
            {"query": "invoice refund", "mode": "vector", "fields": "page_id,route"},
        ),
    ).json()

    assert http_vector["results"][0] == {"page_id": "billing", "route": "vector"}
    assert http_hybrid["evidence"][0]["page_id"] == "billing"
    assert http_hybrid["evidence"][0]["route"] == "hybrid"
    assert http_min_score.status_code == 400
    assert "min_score is not supported" in http_min_score.json()["detail"]
    assert mcp_vector["result"]["results"][0] == {"page_id": "billing", "route": "vector"}
    assert set(client.get("/health").json()["capabilities"]) >= {
        "llmwiki_search_mode_vector",
        "llmwiki_search_mode_hybrid",
    }
    assert set(client.get("/source-bundle").json()["capabilities"]) >= {
        "llmwiki_retrieval_v1",
        "llmwiki_search_mode_vector",
    }
    mcp_stream = create_mcp_stream_server(
        LlmWikiService(
            root,
            vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache-stream"),
            vector_provider=FakeEmbeddingProvider(),
        )
    )
    assert "llmwiki_search_mode_vector" in mcp_stream.instructions

    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }
    with TestClient(app, base_url="http://127.0.0.1:8000", follow_redirects=False) as stream_client:
        stream_response = stream_client.post(
            MCP_STREAM_PATH,
            headers=headers,
            json=mcp_call_payload(
                "llmwiki_search",
                {"query": "invoice refund", "mode": "vector", "fields": "page_id,route"},
            ),
        ).json()
    assert stream_response["result"]["isError"] is False
    assert stream_response["result"]["structuredContent"]["results"][0] == {
        "page_id": "billing",
        "route": "vector",
    }

    assert managed_context_hit_page_ids(
        [
            vector_search_result("billing", route="vector"),
            vector_search_result("billing", route="hybrid"),
            vector_search_result("billing", route="overview"),
        ]
    ) == ["billing", "billing"]


def test_vector_cache_rejects_source_root_corruption_drafts_and_concurrent_builds(
    tmp_path: Path,
) -> None:
    root = sample_vector_wiki(tmp_path / "wiki", include_draft=True)
    cache_dir = tmp_path / "cache"

    with pytest.raises(LlmWikiUserError, match="outside the served source root"):
        LlmWikiService(
            root,
            vector_config=VectorConfig(enabled=True, cache_dir=root / ".vector"),
            vector_provider=FakeEmbeddingProvider(),
        )

    approved_provider = FakeEmbeddingProvider()
    approved_service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=cache_dir),
        vector_provider=approved_provider,
    )
    approved_results = approved_service.search("draft semantic", mode="vector")
    assert all(item["page_id"] != "draft" for item in approved_results)
    assert approved_provider.document_calls == 1

    draft_service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=cache_dir),
        vector_provider=FakeEmbeddingProvider(),
    )
    assert (
        draft_service.search("draft semantic", mode="vector", include_drafts=True)[0]["page_id"]
        == "draft"
    )
    manifests = list(cache_dir.rglob("index.json"))
    identities = [json.loads(path.read_text(encoding="utf-8"))["identity"] for path in manifests]
    assert {identity["visibility_scope"] for identity in identities} == {"approved", "all"}

    for payload_path in cache_dir.rglob("vectors.*.npy"):
        payload_path.write_bytes(b"corrupt")
    rebuild_provider = FakeEmbeddingProvider()
    rebuild_service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=cache_dir),
        vector_provider=rebuild_provider,
    )
    rebuild_service.search("invoice refund", mode="vector")
    assert rebuild_provider.document_calls == 1

    refresh_provider = FakeEmbeddingProvider()
    refresh_service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "refresh-cache"),
        vector_provider=refresh_provider,
    )
    refresh_service.search("invoice refund", mode="vector")
    write_markdown(
        root / "billing.md",
        """
---
title: Other
review_state: approved
---
# Other

Changed content after refresh.
""",
    )
    refresh_service.index(refresh=True)
    refresh_service.search("invoice refund", mode="vector")
    assert refresh_provider.document_calls == 2

    concurrent_cache = tmp_path / "concurrent-cache"
    providers = [FakeEmbeddingProvider(), FakeEmbeddingProvider()]
    errors: list[BaseException] = []

    def run_search(provider: FakeEmbeddingProvider) -> None:
        try:
            LlmWikiService(
                root,
                vector_config=VectorConfig(enabled=True, cache_dir=concurrent_cache),
                vector_provider=provider,
            ).search("invoice refund", mode="vector")
        except BaseException as exc:  # pragma: no cover - asserted through errors list
            errors.append(exc)

    threads = [threading.Thread(target=run_search, args=(provider,)) for provider in providers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sum(provider.document_calls for provider in providers) >= 1
    assert list(concurrent_cache.rglob("index.json"))


def test_service_rejects_in_root_vector_cache_before_provider_or_cache_init(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import llmwiki_serve.service as service_module

    root = sample_vector_wiki(tmp_path / "wiki")

    def fail_create_embedding_provider(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("in-root vector cache validation must not initialize providers")

    class FailingVectorIndexCache:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("in-root vector cache validation must not create caches")

    monkeypatch.setattr(service_module, "create_embedding_provider", fail_create_embedding_provider)
    monkeypatch.setattr(service_module, "VectorIndexCache", FailingVectorIndexCache)

    with pytest.raises(LlmWikiUserError, match="vector cache directory"):
        LlmWikiService(
            root,
            vector_config=VectorConfig(enabled=True, cache_dir=root / ".vector"),
        )


def test_vector_cache_dir_rejects_in_root_relative_and_separator_styles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = sample_vector_wiki(tmp_path / "wiki")
    cache_paths = [Path(str(root / ".vector").replace(os.sep, "/"))]
    if os.name == "nt":
        cache_paths.append(Path(str(root / ".vector").replace("/", "\\")))

    for cache_path in cache_paths:
        with pytest.raises(LlmWikiUserError, match="vector cache directory"):
            resolve_vector_cache_dir(root, cache_path)
        with pytest.raises(LlmWikiUserError, match="vector cache directory"):
            LlmWikiService(root, vector_config=VectorConfig(enabled=True, cache_dir=cache_path))

    monkeypatch.chdir(tmp_path)
    with pytest.raises(LlmWikiUserError, match="vector cache directory"):
        LlmWikiService(
            Path("wiki"),
            vector_config=VectorConfig(enabled=True, cache_dir=Path("wiki") / ".vector"),
        )


def test_vector_cache_dir_rejects_symlink_and_case_alias_inside_source_root(
    tmp_path: Path,
) -> None:
    root = sample_vector_wiki(tmp_path / "Wiki")
    target = root / "linked-cache"
    target.mkdir()
    link = tmp_path / "cache-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable on this platform: {exc}")

    with pytest.raises(LlmWikiUserError, match="vector cache directory"):
        resolve_vector_cache_dir(root, link)
    with pytest.raises(LlmWikiUserError, match="vector cache directory"):
        LlmWikiService(root, vector_config=VectorConfig(enabled=True, cache_dir=link))

    if os.name == "nt" or sys.platform == "darwin":
        case_alias = Path(str(root / ".vector").swapcase())
        with pytest.raises(LlmWikiUserError, match="vector cache directory"):
            resolve_vector_cache_dir(root, case_alias)


def test_valid_vector_cache_config_stays_zero_init_until_needed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import llmwiki_serve.service as service_module

    root = sample_vector_wiki(tmp_path / "wiki")
    cache_dir = tmp_path / "cache"

    def fail_create_embedding_provider(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("context guidance must not initialize vector providers")

    class FailingVectorIndexCache:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("context guidance must not create vector caches")

    monkeypatch.setattr(service_module, "create_embedding_provider", fail_create_embedding_provider)
    monkeypatch.setattr(service_module, "VectorIndexCache", FailingVectorIndexCache)

    service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=cache_dir),
    )
    context = service.context("invoice refund", mode="lexical")

    assert context.retrieval_guidance is not None
    assert context.retrieval_guidance.fallback_modes == ["literal"]
    assert service.vector_config.cache_dir == cache_dir.resolve(strict=False)
    assert service._vector_provider is None  # noqa: SLF001
    assert service._vector_cache is None  # noqa: SLF001
    assert not cache_dir.exists()


def test_vector_model_cache_dir_must_stay_outside_source_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = sample_vector_wiki(tmp_path / "wiki")

    with pytest.raises(LlmWikiUserError, match="vector model cache directory"):
        LlmWikiService(
            root,
            vector_config=VectorConfig(enabled=True, model_cache_dir=root / "models"),
        )

    monkeypatch.chdir(tmp_path)
    with pytest.raises(LlmWikiUserError, match="vector model cache directory"):
        LlmWikiService(
            Path("wiki"),
            vector_config=VectorConfig(enabled=True, model_cache_dir=Path("wiki") / "models"),
        )

    assert resolve_vector_model_cache_dir(root, tmp_path / "models") == (
        tmp_path / "models"
    ).resolve(strict=False)

    link = tmp_path / "model-cache-link"
    target = root / "linked-model-cache"
    target.mkdir()
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable on this platform: {exc}")
    with pytest.raises(LlmWikiUserError, match="vector model cache directory"):
        resolve_vector_model_cache_dir(root, link)


def test_cli_rejects_vector_model_cache_dir_under_source_root(tmp_path: Path) -> None:
    root = sample_vector_wiki(tmp_path / "wiki")

    result = CliRunner().invoke(
        cli_app,
        [
            "search",
            str(root),
            "invoice refund",
            "--mode",
            "vector",
            "--vector-model-cache-dir",
            str(root / "models"),
        ],
    )

    assert result.exit_code == 1
    assert "vector model cache directory" in result.output


def test_vector_cache_stale_lock_and_dimension_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = sample_vector_wiki(tmp_path / "wiki")
    default_cache = resolve_vector_cache_dir(root, None)
    assert root.resolve(strict=False) not in [default_cache, *default_cache.parents]
    with pytest.raises(LlmWikiUserError, match="unexpected dimension"):
        LlmWikiService(
            root,
            vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "bad-dim-cache"),
            vector_provider=BadDimensionProvider(),
        ).search("invoice refund", mode="vector")

    lock_path = tmp_path / "locks" / "build.lock"
    lock_path.parent.mkdir()
    lock_path.write_text(
        json.dumps({"pid": os.getpid(), "created_at": time.time()}),
        encoding="utf-8",
    )
    old_timestamp = 1000.0
    os.utime(lock_path, (old_timestamp, old_timestamp))
    monkeypatch.setattr(vector_module, "VECTOR_LOCK_STALE_SECONDS", 0.0)
    vector_module.remove_stale_lock(lock_path)
    assert lock_path.exists()

    lock_path.write_text("stale", encoding="utf-8")
    os.utime(lock_path, (old_timestamp, old_timestamp))
    monkeypatch.setattr(vector_module, "VECTOR_LOCK_TIMEOUT_SECONDS", 1.0)

    with vector_cache_lock(lock_path):
        assert lock_path.exists()
    assert not lock_path.exists()


def test_cross_service_cold_build_lock_timeout_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = sample_vector_wiki(tmp_path / "wiki")
    cache_dir = tmp_path / "shared-cache"
    embedding_started = threading.Event()
    release_embedding = threading.Event()
    first_results: list[list[dict[str, Any]]] = []
    first_errors: list[BaseException] = []

    class BlockingDocumentProvider(FakeEmbeddingProvider):
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            self.document_calls += 1
            embedding_started.set()
            if not release_embedding.wait(timeout=3.0):
                raise AssertionError("timed out waiting to release document embedding")
            return [fake_vector(text) for text in texts]

    first_provider = BlockingDocumentProvider()
    first_service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=cache_dir),
        vector_provider=first_provider,
    )

    def build_from_first_service() -> None:
        try:
            first_results.append(
                first_service.search("invoice refund", mode="vector", fields=["page_id", "route"])
            )
        except BaseException as exc:  # pragma: no cover - asserted through holder_errors
            first_errors.append(exc)

    monkeypatch.setattr(vector_module, "VECTOR_LOCK_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(vector_module, "VECTOR_LOCK_RETRY_SECONDS", 0.001)
    thread = threading.Thread(target=build_from_first_service)
    thread.start()
    assert embedding_started.wait(timeout=1.0)
    competing_provider = FakeEmbeddingProvider()
    try:
        with pytest.raises(LlmWikiUserError, match="vector cache is busy; retry the request"):
            LlmWikiService(
                root,
                vector_config=VectorConfig(enabled=True, cache_dir=cache_dir),
                vector_provider=competing_provider,
            ).search("invoice refund", mode="vector")
    finally:
        release_embedding.set()
        thread.join(timeout=2.0)

    assert first_errors == []
    assert not thread.is_alive()
    assert first_results[0][0] == {"page_id": "billing", "route": "vector"}
    assert first_provider.document_calls == 1
    assert competing_provider.document_calls == 0
    assert list(cache_dir.rglob("index.json"))

    retry_provider = FakeEmbeddingProvider()
    retry_result = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=cache_dir),
        vector_provider=retry_provider,
    ).search("invoice refund", mode="vector", fields=["page_id", "route"])
    assert retry_result[0] == {"page_id": "billing", "route": "vector"}
    assert retry_provider.document_calls == 0
    assert retry_provider.query_calls == 1


def test_hybrid_rrf_and_exact_identifier_guard(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    write_markdown(
        root / "release.v1-beta.md",
        """
---
review_state: approved
---
# Release

The exact release note is here.
""",
    )
    write_markdown(
        root / "decoy.md",
        """
---
review_state: approved
---
# Decoy

semantic decoy release approximation should not satisfy exact identifiers.
""",
    )
    service = LlmWikiService(
        root,
        analyzer_profile="english",
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
        vector_provider=FakeEmbeddingProvider(),
    )

    result = service.search(
        "release.v1-beta.md", mode="hybrid", fields=["page_id", "route", "score"]
    )

    assert result == [
        {
            "page_id": "release.v1-beta",
            "score": round((1.0 / (HYBRID_RRF_K + 1)) * 2, 4),
            "route": "hybrid",
        }
    ]


def test_orientation_seeded_hybrid_lifts_relevant_linked_paraphrase(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Project Index

Refund workflow owners should read [[billing]] before answering support questions.
""",
    )
    write_markdown(
        root / "billing.md",
        """
---
title: Billing
review_state: approved
---
# Billing

Reimbursement approval and payment reversal rules for customer support.
""",
    )
    write_markdown(
        root / "decoy.md",
        """
---
title: Decoy
review_state: approved
---
# Decoy

Refund workflow wording appears here, but this page is only a glossary.
""",
    )
    provider = KeywordEmbeddingProvider(
        [
            ("refund workflow", [1.0, 0.0, 0.0]),
            ("billing", [1.0, 0.0, 0.0]),
            ("reimbursement", [1.0, 0.0, 0.0]),
            ("decoy", [0.9, 0.0, 0.0]),
        ]
    )
    service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
        vector_provider=provider,
    )

    hybrid = service.search("refund workflow", mode="hybrid", fields=["page_id", "route"])
    plain = plain_hybrid_dicts(service, "refund workflow", fields=["page_id", "route"])

    assert plain[0]["page_id"] != "billing"
    assert hybrid[0] == {"page_id": "billing", "route": "hybrid"}
    assert service.search("refund workflow", mode="hybrid") == service.search(
        "refund workflow", mode="hybrid"
    )


def test_orientation_related_vector_recovers_target_outside_global_depth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "wiki"
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Project Index

Refund workflow owners should read [[zz-billing]] before answering support questions.
""",
    )
    write_markdown(
        root / "zz-billing.md",
        """
---
title: Billing
review_state: approved
---
# Billing

Reimbursement approval and payment reversal rules for customer support.
""",
    )
    for index in range(300):
        write_markdown(
            root / f"aa-decoy-{index:03}.md",
            f"""
---
review_state: approved
---
# Decoy {index:03}

semantic-marker vector-only decoy {index:03}
""",
        )
    provider = KeywordEmbeddingProvider(
        [
            ("refund workflow", [1.0, 0.0, 0.0]),
            ("semantic-marker", [1.0, 0.0, 0.0]),
            ("reimbursement approval", [0.8, 0.6, 0.0]),
        ]
    )
    service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
        vector_provider=provider,
    )
    index = service.index()
    corpus = service._index_views(index).search_corpus(False)  # noqa: SLF001
    scored_row_counts: list[int] = []
    real_numpy_cosine_scores = vector_module.numpy_cosine_scores

    def counted_numpy_cosine_scores(
        matrix: Any,
        norms: Any,
        query_vector: tuple[float, ...],
        query_norm: float,
    ) -> tuple[float, ...] | None:
        scored_row_counts.append(int(matrix.shape[0]))
        return real_numpy_cosine_scores(matrix, norms, query_vector, query_norm)

    monkeypatch.setattr(vector_module, "numpy_cosine_scores", counted_numpy_cosine_scores)

    assert hybrid_candidate_depth(8, total_docs=len(corpus.documents)) == 256

    hybrid = service.search(
        "refund workflow",
        mode="hybrid",
        fields=["page_id", "route"],
    )

    assert provider.query_calls == 1
    assert scored_row_counts == [1, 1, len(corpus.pages)]
    assert hybrid[0] == {"page_id": "zz-billing", "route": "hybrid"}


def test_vector_subset_python_fallback_scores_only_eligible_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_page = vector_page("target", "target.md", "Target text")
    records = (
        vector_record("index", [1.0, 0.0, 0.0]),
        vector_record("target", [0.0, 1.0, 0.0]),
        vector_record("decoy", [0.0, 0.0, 1.0]),
    )
    vector_index = vector_module.VectorIndex(
        identity={},
        records=records,
        dimension=3,
        cache_hit=False,
        vector_matrix=None,
        vector_norms=None,
    )
    scored_document_vectors: list[tuple[float, ...]] = []
    real_cosine = vector_module.cosine

    def counted_cosine(
        query_vector: tuple[float, ...],
        query_norm: float,
        document_vector: tuple[float, ...],
        document_norm: float,
    ) -> float:
        scored_document_vectors.append(document_vector)
        return real_cosine(query_vector, query_norm, document_vector, document_norm)

    monkeypatch.setattr(vector_module, "cosine", counted_cosine)

    results = vector_module.search_vector_index_with_query_vector(
        vector_index,
        pages=[target_page],
        query="target",
        query_vector=(0.0, 1.0, 0.0),
        query_norm=1.0,
        limit=5,
    )

    assert [result.page_id for result in results] == ["target"]
    assert scored_document_vectors == [records[1].vector]


def test_large_filtered_vector_scores_use_block_slices_not_advanced_indexing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    total_records = 512
    records = tuple(
        vector_record(f"page-{index}", [1.0, 0.0, 0.0]) for index in range(total_records)
    )
    matrix_keys: list[Any] = []
    norm_keys: list[Any] = []

    class RecordingRows:
        def __init__(self, total: int, keys: list[Any], positions: list[int] | None = None) -> None:
            self._total = total
            self._keys = keys
            self.positions = list(range(total)) if positions is None else positions
            self.shape = (len(self.positions), 3)

        def __getitem__(self, key: Any) -> Any:
            self._keys.append(key)
            if isinstance(key, list):
                raise AssertionError("large filtered scoring used advanced indexing")
            if isinstance(key, slice):
                positions = list(range(self._total))[key]
                return RecordingRows(self._total, self._keys, positions)
            return [1.0, 0.0, 0.0]

    vector_index = vector_module.VectorIndex(
        identity={},
        records=records,
        dimension=3,
        cache_hit=False,
        vector_matrix=RecordingRows(total_records, matrix_keys),
        vector_norms=RecordingRows(total_records, norm_keys),
    )

    def fake_numpy_cosine_scores(
        matrix: Any,
        _norms: Any,
        _query_vector: tuple[float, ...],
        _query_norm: float,
    ) -> tuple[float, ...]:
        return tuple(float(position) for position in matrix.positions)

    monkeypatch.setattr(vector_module, "numpy_cosine_scores", fake_numpy_cosine_scores)
    positions = tuple(index for index in range(total_records) if index != 257)

    scores = vector_module.vector_record_scores(
        vector_index,
        (1.0, 0.0, 0.0),
        1.0,
        record_positions=positions,
    )

    assert len(scores) == total_records - 1
    assert scores[:3] == (0.0, 1.0, 2.0)
    assert 257.0 not in scores
    assert all(isinstance(key, slice) for key in matrix_keys)
    assert all(isinstance(key, slice) for key in norm_keys)


def test_noncontiguous_vector_score_positions_match_python_fallback_property() -> None:
    pytest.importorskip("numpy")
    total_records = 220
    records = tuple(
        vector_record(
            f"page-{index}",
            [
                1.0 + float(index % 5),
                0.25 + float(index % 7) / 10.0,
                0.5 + float(index % 11) / 20.0,
            ],
        )
        for index in range(total_records)
    )
    matrix_index = vector_module.VectorIndex(
        identity={},
        records=records,
        dimension=3,
        cache_hit=False,
        vector_matrix=vector_module.vector_matrix_for_records(records),
        vector_norms=vector_module.vector_norms_for_records(records),
    )
    python_index = vector_module.VectorIndex(
        identity={},
        records=records,
        dimension=3,
        cache_hit=False,
        vector_matrix=None,
        vector_norms=None,
    )
    query_vector = (0.7, 0.2, 0.4)
    query_norm = vector_module.vector_norm(query_vector)
    position_patterns = [
        tuple(index for index in range(total_records) if index % 2 == 0),
        tuple(index for index in range(total_records) if index % 3 != 1),
        tuple(index for index in range(17, total_records, 19)),
        (0, 2, 5, 129, 130, 219),
    ]

    for positions in position_patterns:
        matrix_scores = vector_module.vector_record_scores(
            matrix_index,
            query_vector,
            query_norm,
            record_positions=positions,
        )
        python_scores = vector_module.vector_record_scores(
            python_index,
            query_vector,
            query_norm,
            record_positions=positions,
        )

        assert matrix_scores == pytest.approx(python_scores)


def test_public_vector_search_exclude_one_streams_blocks_without_score_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("numpy")
    total_records = 150
    pages = [
        vector_page(f"page-{index:03}", f"page-{index:03}.md", f"# Page {index:03}")
        for index in range(total_records)
    ]
    records = tuple(
        vector_record(
            page.id,
            [1.0, 0.0, 0.0] if page.id in {"page-000", "page-149"} else [0.0, 1.0, 0.0],
        )
        for page in pages
    )
    vector_index = vector_module.VectorIndex(
        identity={},
        records=records,
        dimension=3,
        cache_hit=False,
        vector_matrix=vector_module.vector_matrix_for_records(records),
        vector_norms=vector_module.vector_norms_for_records(records),
    )
    scored_row_counts: list[int] = []
    real_numpy_cosine_scores = vector_module.numpy_cosine_scores

    def counted_numpy_cosine_scores(
        matrix: Any,
        norms: Any,
        query_vector: tuple[float, ...],
        query_norm: float,
    ) -> tuple[float, ...] | None:
        scored_row_counts.append(int(matrix.shape[0]))
        return real_numpy_cosine_scores(matrix, norms, query_vector, query_norm)

    def fail_vector_record_scores(*_args: Any, **_kwargs: Any) -> tuple[float, ...]:
        raise AssertionError("public vector search materialized record score tuple")

    monkeypatch.setattr(vector_module, "VECTOR_SCORE_BLOCK_ROWS", 32)
    monkeypatch.setattr(vector_module, "numpy_cosine_scores", counted_numpy_cosine_scores)
    monkeypatch.setattr(vector_module, "vector_record_scores", fail_vector_record_scores)

    results = vector_module.search_vector_index_with_query_vector(
        vector_index,
        pages=pages,
        query="target concept",
        query_vector=(1.0, 0.0, 0.0),
        query_norm=1.0,
        limit=3,
        exclude_page_ids=["page-000"],
    )

    assert [result.page_id for result in results] == ["page-149", "page-001", "page-002"]
    assert all(result.page_id != "page-000" for result in results)
    assert len(scored_row_counts) > 1
    assert sum(scored_row_counts) == total_records
    assert all(count <= 32 for count in scored_row_counts)


def test_small_subset_positions_are_bounded_by_matching_row_count() -> None:
    capped_records = tuple(
        vector_record(
            "long-alpha" if index % 2 == 0 else "long-beta",
            [1.0, 0.0, 0.0],
        )
        for index in range(vector_module.VECTOR_FILTERED_COPY_MAX_ROWS)
    )
    pages_by_id = {
        "long-alpha": vector_page("long-alpha", "long-alpha.md", "Alpha"),
        "long-beta": vector_page("long-beta", "long-beta.md", "Beta"),
    }
    capped_index = vector_module.VectorIndex(
        identity={},
        records=capped_records + (vector_record("outside", [0.0, 1.0, 0.0]),),
        dimension=3,
        cache_hit=False,
    )

    capped_positions = vector_module.filtered_record_positions_for_small_page_subset(
        capped_index,
        pages_by_id,
        excluded=set(),
    )

    assert capped_positions is not None
    assert len(capped_positions) == vector_module.VECTOR_FILTERED_COPY_MAX_ROWS

    overflow_index = vector_module.VectorIndex(
        identity={},
        records=capped_records
        + (
            vector_record("long-alpha", [1.0, 0.0, 0.0]),
            vector_record("outside", [0.0, 1.0, 0.0]),
        ),
        dimension=3,
        cache_hit=False,
    )

    assert (
        vector_module.filtered_record_positions_for_small_page_subset(
            overflow_index,
            pages_by_id,
            excluded=set(),
        )
        is None
    )


def test_public_hybrid_fallback_suppresses_orientation_answers_without_explicit_request(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

Where is refund material? This generic index text is not itself an answer.

"""
        + ("filler " * 120)
        + """

[[alpha]] [[beta]] [[gamma]]
""",
    )
    write_markdown(
        root / "hot.md",
        """
---
review_state: approved
---
# Hot

Refund material navigation cache text is not itself an answer.
""",
    )
    write_markdown(
        root / "answer.md",
        """
---
review_state: approved
---
# Answer

Refund material answer with implementation details for support.
""",
    )
    for name in ("alpha", "beta", "gamma"):
        write_markdown(
            root / f"{name}.md",
            f"""
---
review_state: approved
---
# {name.title()}

This unrelated page should not be promoted from a distant boilerplate link.
""",
        )
    service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
        vector_provider=KeywordEmbeddingProvider([("refund material", [1.0, 0.0, 0.0])]),
    )

    hybrid = service.search("refund material", mode="hybrid")
    unsuppressed_plain = plain_hybrid_dicts(service, "refund material")
    suppressed_plain = plain_hybrid_dicts(
        service,
        "refund material",
        suppress_orientation_answers=True,
    )
    explicit = service.search(
        "index",
        mode="hybrid",
        fields=["page_id", "role"],
        limit=1,
    )

    assert unsuppressed_plain[0]["role"] in {"hot", "index"}
    assert hybrid == suppressed_plain
    assert hybrid[0]["page_id"] == "answer"
    assert all(item["role"] not in {"hot", "index", "overview"} for item in hybrid)
    assert explicit == [{"page_id": "index", "role": "index"}]


def test_nested_topic_quickstart_is_not_hybrid_orientation_seed(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    write_markdown(
        root / "guide" / "quickstart.md",
        """
---
review_state: approved
---
# Nested Quickstart

Refund workflow owners should read [[billing]] before answering support questions.
""",
    )
    write_markdown(
        root / "billing.md",
        """
---
title: Billing
review_state: approved
---
# Billing

Reimbursement approval and payment reversal rules for customer support.
""",
    )
    write_markdown(
        root / "decoy.md",
        """
---
title: Decoy
review_state: approved
---
# Decoy

Refund workflow wording appears here, but this page is only a glossary.
""",
    )
    service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
        vector_provider=KeywordEmbeddingProvider(
            [
                ("refund workflow", [1.0, 0.0, 0.0]),
                ("billing", [1.0, 0.0, 0.0]),
                ("reimbursement", [1.0, 0.0, 0.0]),
                ("decoy", [0.9, 0.0, 0.0]),
            ]
        ),
    )

    quickstart = next(page for page in service.index().pages if page.path == "guide/quickstart.md")
    assert quickstart.role == "topic"
    assert service.search("refund workflow", mode="hybrid") == plain_hybrid_dicts(
        service, "refund workflow"
    )


def test_hybrid_without_orientation_is_plain_rrf_equivalent(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    write_markdown(
        root / "alpha.md",
        """
---
review_state: approved
---
# Alpha

Alpha refund evidence.
""",
    )
    write_markdown(
        root / "beta.md",
        """
---
review_state: approved
---
# Beta

Beta reimbursement evidence.
""",
    )
    provider = KeywordEmbeddingProvider(
        [("refund", [1.0, 0.0, 0.0]), ("reimbursement", [1.0, 0.0, 0.0])]
    )
    service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
        vector_provider=provider,
    )

    hybrid = service.search("refund", mode="hybrid")

    assert provider.query_calls == 1
    assert hybrid == plain_hybrid_dicts(service, "refund")


def test_bounded_plain_rrf_matches_full_candidates_when_corpus_fits_depth(
    tmp_path: Path,
) -> None:
    root = sample_vector_wiki(tmp_path / "wiki")
    service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
        vector_provider=FakeEmbeddingProvider(),
    )
    index = service.index()
    corpus = service._index_views(index).search_corpus(False)  # noqa: SLF001

    assert hybrid_candidate_depth(8, total_docs=len(corpus.documents)) == len(corpus.documents)
    assert plain_hybrid_dicts(service, "invoice refund") == plain_hybrid_dicts(
        service,
        "invoice refund",
        candidate_limit=len(corpus.documents),
    )


def test_orientation_source_ref_tag_and_draft_isolation(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
source_refs: ["SRC-PLAN"]
tags: ["payments"]
---
# Index

Architecture answers use SRC-PLAN and #payments context.
""",
    )
    write_markdown(
        root / "plan.md",
        """
---
review_state: approved
source_refs: ["SRC-PLAN"]
tags: ["payments"]
---
# Plan

Settlement and reimbursement design notes.
""",
    )
    write_markdown(
        root / "draft-plan.md",
        """
---
review_state: draft
source_refs: ["SRC-PLAN"]
tags: ["payments"]
---
# Draft Plan

Draft-only private settlement details.
""",
    )
    service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
        vector_provider=KeywordEmbeddingProvider(
            [
                ("architecture answers", [1.0, 0.0, 0.0]),
                ("settlement", [1.0, 0.0, 0.0]),
                ("draft-only", [1.0, 0.0, 0.0]),
            ]
        ),
    )

    result = service.search("architecture answers", mode="hybrid")

    assert result[0]["page_id"] == "plan"
    assert all(item["page_id"] != "draft-plan" for item in result)


def test_orientation_related_semantic_order_ignores_relation_declaration_order(
    tmp_path: Path,
) -> None:
    query = "CASE-GATE probe"
    target_id = "zz-semantic-target"
    noisy_ids = [f"noisy-related-{index:02}" for index in range(HYBRID_RELATED_PER_SEED_LIMIT + 1)]
    late_order = noisy_ids + [target_id]
    assert len(late_order) > HYBRID_RELATED_PER_SEED_LIMIT
    assert late_order.index(target_id) >= HYBRID_RELATED_PER_SEED_LIMIT
    relation_orders = {
        "late": late_order,
        "reversed": list(reversed(late_order)),
        "shuffled": noisy_ids[1::2] + noisy_ids[::2] + [target_id],
    }
    top_result_orders: list[list[str]] = []

    for variant, relation_order in relation_orders.items():
        root = tmp_path / f"wiki-{variant}"
        link_text = " ".join(f"[[{page_id}]]" for page_id in relation_order)
        write_markdown(
            root / "index.md",
            f"""
---
review_state: approved
---
# Project Index

{query} routes through this visible orientation hub.

{link_text} [[draft-private-target]]
""",
        )
        for page_id in noisy_ids:
            write_markdown(
                root / f"{page_id}.md",
                f"""
---
review_state: approved
---
# {page_id}

related noisy payload {page_id}
""",
            )
        write_markdown(
            root / f"{target_id}.md",
            """
---
review_state: approved
---
# Semantic Target

unique target-only payload
""",
        )
        write_markdown(
            root / "draft-private-target.md",
            """
---
review_state: draft
---
# Draft Private Target

global-dominant payload draft-only material
""",
        )
        for index in range(270):
            write_markdown(
                root / f"global-decoy-{index:03}.md",
                f"""
---
review_state: approved
---
# Global Decoy {index:03}

global-dominant payload {index:03}
""",
            )
        service = LlmWikiService(
            root,
            analyzer_profile="english",
            vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / f"cache-{variant}"),
            vector_provider=KeywordEmbeddingProvider(
                [
                    (query, [1.0, 0.0, 0.0]),
                    ("global-dominant payload", [1.0, 0.0, 0.0]),
                    ("unique target-only payload", [0.995, 0.1, 0.0]),
                    ("related noisy payload", [0.94, 0.34, 0.0]),
                ]
            ),
        )
        corpus = service._index_views(service.index()).search_corpus(False)  # noqa: SLF001
        candidate_depth = hybrid_candidate_depth(8, total_docs=len(corpus.documents))
        assert candidate_depth == 256
        global_vector_ids = [
            item["page_id"]
            for item in service.search(
                query,
                mode="vector",
                limit=candidate_depth,
                fields=["page_id"],
            )
        ]
        assert target_id not in global_vector_ids

        hybrid = service.search(
            query,
            mode="hybrid",
            fields=["page_id", "route"],
            snippet_chars=4_000,
        )

        assert hybrid[0] == {"page_id": target_id, "route": "hybrid"}
        assert all(item["page_id"] != "draft-private-target" for item in hybrid)
        top_result_orders.append([item["page_id"] for item in hybrid[:3]])
        if variant == "late":
            excluded = service.search(
                query,
                mode="hybrid",
                fields=["page_id"],
                snippet_chars=4_000,
                exclude_page_ids=[target_id],
            )
            assert all(item["page_id"] != target_id for item in excluded)

    assert top_result_orders[1:] == top_result_orders[:1] * (len(top_result_orders) - 1)


def test_graph_prior_candidates_follow_semantic_vector_order_not_relation_order(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    write_markdown(
        root / "alpha.md",
        """
---
review_state: approved
---
# Alpha

General background.
""",
    )
    write_markdown(
        root / "beta.md",
        """
---
review_state: approved
---
# Beta

Specific answer material.
""",
    )
    service = LlmWikiService(root)
    corpus = service._index_views(service.index()).search_corpus(False)  # noqa: SLF001
    relation_ordered_ids = ["alpha", "beta"]
    vector_ranked_results = [
        search_result("beta", "beta.md", score=0.92),
        search_result("alpha", "alpha.md", score=0.31),
    ]

    graph_prior = vector_module.graph_prior_results_by_vector_relevance(
        corpus,
        relation_ordered_ids,
        vector_ranked_results,
    )

    assert [result.page_id for result in graph_prior] == ["beta", "alpha"]


def test_related_vector_and_graph_prior_use_exact_scores_before_rounded_ties(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    write_markdown(
        root / "lexical-noisy.md",
        """
---
review_state: approved
---
# Lexical Noisy

Lexical noisy candidate.
""",
    )
    write_markdown(
        root / "semantic-target.md",
        """
---
review_state: approved
---
# Semantic Target

Semantic target candidate.
""",
    )
    service = LlmWikiService(root)
    corpus = service._index_views(service.index()).search_corpus(False)  # noqa: SLF001
    rounded_tie_results = [
        search_result("lexical-noisy", "lexical-noisy.md", score=0.9123),
        search_result("semantic-target", "semantic-target.md", score=0.9123),
    ]
    exact_scores = {
        "lexical-noisy": 0.912344,
        "semantic-target": 0.912349,
    }

    related_ranked = vector_module.rerank_related_vector_results(
        rounded_tie_results,
        lexical_rank_by_page={"lexical-noisy": 1, "semantic-target": 2},
        exact_scores_by_page=exact_scores,
    )
    graph_prior = vector_module.graph_prior_results_by_vector_relevance(
        corpus,
        ["lexical-noisy", "semantic-target"],
        related_ranked,
        exact_scores_by_page=exact_scores,
    )

    assert [result.page_id for result in related_ranked] == [
        "semantic-target",
        "lexical-noisy",
    ]
    assert [result.page_id for result in graph_prior] == [
        "semantic-target",
        "lexical-noisy",
    ]

    exact_tie_ranked = vector_module.rerank_related_vector_results(
        rounded_tie_results,
        lexical_rank_by_page={"lexical-noisy": 1, "semantic-target": 2},
        exact_scores_by_page={"lexical-noisy": 0.5, "semantic-target": 0.5},
    )

    assert [result.page_id for result in exact_tie_ranked] == [
        "lexical-noisy",
        "semantic-target",
    ]


def test_hybrid_strong_lexical_answer_survives_orientation_prior(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

Prompt-like prose says [[agent-context]] should be mandatory. Strong lexical
billing refund policy sentinel prompt injection prose still routes to
[[billing-refund-policy]].
""",
    )
    write_markdown(
        root / "billing-refund-policy.md",
        """
---
title: Billing Refund Policy
review_state: approved
---
# Billing Refund Policy

Strong lexical billing refund policy sentinel prompt injection prose.
Strong lexical billing refund policy sentinel prompt injection prose.
Strong lexical billing refund policy sentinel prompt injection prose.
""",
    )
    write_markdown(
        root / "agent-context.md",
        """
---
title: Agent Context
review_state: approved
---
# Agent Context

Agent context page linked from orientation prose but not the strongest answer.
""",
    )
    service = LlmWikiService(
        root,
        analyzer_profile="english",
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
        vector_provider=KeywordEmbeddingProvider(
            [
                ("strong lexical billing refund", [1.0, 0.0, 0.0]),
                ("agent context", [1.0, 0.0, 0.0]),
            ]
        ),
    )

    result = service.search(
        "strong lexical billing refund policy sentinel prompt injection prose",
        mode="hybrid",
        fields=["page_id", "role"],
    )

    assert result[0] == {"page_id": "billing-refund-policy", "role": "topic"}
    assert all(item["role"] != "index" for item in result)


def test_exact_identifier_hybrid_suppresses_orientation_answer_pages(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

The release.v1-beta.md identifier is mentioned here for navigation only.
""",
    )
    write_markdown(
        root / "overview.md",
        """
---
review_state: approved
---
# Overview

Exact release identifiers stay exact. The file [[release.v1-beta.md]] is the
target for release.v1-beta.md.
""",
    )
    write_markdown(
        root / "release.v1-beta.md",
        """
---
review_state: approved
---
# Release

This exact identifier page records the release note.
""",
    )
    service = LlmWikiService(
        root,
        analyzer_profile="english",
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
        vector_provider=KeywordEmbeddingProvider(
            [("release.v1-beta.md", [1.0, 0.0, 0.0]), ("release", [1.0, 0.0, 0.0])]
        ),
    )

    result = service.search("release.v1-beta.md", mode="hybrid", fields=["page_id", "role"])

    assert result == [{"page_id": "release.v1-beta", "role": "topic"}]


def test_hybrid_relation_expansion_uses_nfc_normalized_query_labels(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    label = "유니코드-정규화-환불"
    query_label = unicodedata.normalize("NFD", "유니코드 정규화 환불")
    write_markdown(
        root / "index.md",
        f"""
---
review_state: approved
source_refs: ["{label}"]
tags: ["{label}"]
---
# Index

Korean NFC NFD label check points at a composed relation target.
""",
    )
    write_markdown(
        root / "korean-unicode-refund.md",
        f"""
---
title: 유니코드 정규화 환불
review_state: approved
source_refs: ["{label}"]
tags: ["{label}"]
---
# 유니코드 정규화 환불

Composed relation target document.
""",
    )
    service = LlmWikiService(
        root,
        analyzer_profile="english",
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
        vector_provider=KeywordEmbeddingProvider(
            [
                ("Korean NFC NFD", [1.0, 0.0, 0.0]),
                ("Composed relation target", [0.8, 0.6, 0.0]),
            ]
        ),
    )

    result = service.search(f"Korean NFC NFD {query_label}", mode="hybrid", fields=["page_id"])

    assert {"page_id": "korean-unicode-refund"} in result


@pytest.mark.parametrize(
    ("label", "evidence_text"),
    [
        ("ai", "Use #ai for the retrieval notes."),
        ("go", "The standalone go label is visible here."),
        ("db", "Route database notes through db when asked."),
        ("r1", "Release lane r1 owns this topic."),
        ("SRC-PLAN", "Architecture answers use SRC-PLAN context."),
        ("환불 정책", "고객 지원은 환불정책 문서를 먼저 확인합니다."),
        ("docs/agent-context.md", "Related note: [[docs/agent-context|agent context]]."),
        ("guide/billing.md", "See [billing policy](guide/billing.md) before answering."),
    ],
)
def test_relation_label_visibility_matches_standalone_phrases_and_explicit_links(
    label: str,
    evidence_text: str,
) -> None:
    assert relation_label_visible(label, evidence_text) is True


def test_relation_label_visibility_normalizes_korean_unicode_nfc() -> None:
    decomposed_evidence = unicodedata.normalize(
        "NFD",
        "고객 지원은 환불정책 문서를 먼저 확인합니다.",
    )

    assert relation_label_visible("환불 정책", decomposed_evidence) is True
    assert vector_module.normalized_relation_label("환불정책") == (
        vector_module.normalized_relation_label(unicodedata.normalize("NFD", "환불정책"))
    )


@pytest.mark.parametrize(
    ("label", "evidence_text"),
    [
        ("ai", "The maintainer notes explain migrations."),
        ("go", "This ongoing rollout mentions no tag."),
        ("db", "The debugger section is unrelated."),
        ("r1", "The br1anch typo is not a release lane label."),
        ("SRC-PLAN", "This source plan phrase is prose, not an exact source ref."),
    ],
)
def test_relation_label_visibility_rejects_ascii_substring_false_positives(
    label: str,
    evidence_text: str,
) -> None:
    assert relation_label_visible(label, evidence_text) is False


def test_loaded_vector_index_is_reused_for_repeated_queries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = sample_vector_wiki(tmp_path / "wiki")
    provider = FakeEmbeddingProvider()
    service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
        vector_provider=provider,
    )
    original_load_or_build = VectorIndexCache.load_or_build
    load_calls = 0

    def counted_load_or_build(self: VectorIndexCache, *args: Any, **kwargs: Any) -> Any:
        nonlocal load_calls
        load_calls += 1
        return original_load_or_build(self, *args, **kwargs)

    monkeypatch.setattr(VectorIndexCache, "load_or_build", counted_load_or_build)

    assert service.search("invoice refund", mode="vector")[0]["page_id"] == "billing"
    assert service.search("invoice refund", mode="vector")[0]["page_id"] == "billing"
    assert service.search("invoice refund", mode="hybrid")[0]["page_id"] == "billing"

    assert load_calls == 1
    assert provider.document_calls == 1
    assert provider.query_calls == 3


def test_vector_search_uses_projection_signature_from_request_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = sample_vector_wiki(tmp_path / "wiki")
    provider = FakeEmbeddingProvider()
    service = LlmWikiService(
        root,
        refresh_interval_seconds=60.0,
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
        vector_provider=provider,
    )
    old_snapshot = service._index_snapshot()  # noqa: SLF001
    old_projection_digest = old_snapshot.projection_signature_digest
    write_markdown(
        root / "new-page.md",
        """
---
review_state: approved
---
# New Page

Fresh content that changes the projection signature.
""",
    )

    original_provider = service._vector_provider_or_error  # noqa: SLF001
    original_loaded_vector_index = service._loaded_vector_index  # noqa: SLF001
    refresh_once = True
    captured_projection_digests: list[str] = []

    def refresh_during_provider_lookup() -> FakeEmbeddingProvider:
        nonlocal refresh_once
        if refresh_once:
            refresh_once = False
            refreshed = service._index_snapshot(refresh=True)  # noqa: SLF001
            assert refreshed.projection_signature_digest != old_projection_digest
        return original_provider()  # type: ignore[return-value]

    def capture_loaded_vector_index(**kwargs: Any) -> Any:
        captured_projection_digests.append(str(kwargs["projection_signature"]))
        return original_loaded_vector_index(**kwargs)

    monkeypatch.setattr(service, "_vector_provider_or_error", refresh_during_provider_lookup)
    monkeypatch.setattr(service, "_loaded_vector_index", capture_loaded_vector_index)

    result = service.search("invoice refund", mode="vector", fields=["page_id"])

    assert result[0] == {"page_id": "billing"}
    assert captured_projection_digests == [old_projection_digest]


def test_lazy_vector_provider_is_constructed_once_for_concurrent_first_queries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import llmwiki_serve.service as service_module

    root = sample_vector_wiki(tmp_path / "wiki")
    provider = FakeEmbeddingProvider()
    create_calls = 0

    def fake_create_embedding_provider(_config: VectorConfig) -> FakeEmbeddingProvider:
        nonlocal create_calls
        time.sleep(0.02)
        create_calls += 1
        return provider

    monkeypatch.setattr(
        service_module,
        "create_embedding_provider",
        fake_create_embedding_provider,
    )
    service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
    )
    errors: list[BaseException] = []

    def run_search() -> None:
        try:
            service.search("invoice refund", mode="vector")
        except BaseException as exc:  # pragma: no cover - asserted through errors list
            errors.append(exc)

    threads = [threading.Thread(target=run_search) for _index in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert create_calls == 1
    assert provider.document_calls == 1


def test_vector_search_does_not_build_lexical_search_corpus(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import llmwiki_serve.service as service_module

    root = sample_vector_wiki(tmp_path / "wiki")
    build_calls = 0
    real_build_search_corpus = service_module.build_search_corpus

    def wrapped_build_search_corpus(*args: Any, **kwargs: Any) -> Any:
        nonlocal build_calls
        build_calls += 1
        return real_build_search_corpus(*args, **kwargs)

    monkeypatch.setattr(service_module, "build_search_corpus", wrapped_build_search_corpus)
    service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
        vector_provider=FakeEmbeddingProvider(),
    )

    results = service.search("invoice refund", mode="vector", limit=1)

    assert results[0]["page_id"] == "billing"
    assert build_calls == 0


def test_hybrid_candidate_depth_policy_is_bounded_and_limit_based() -> None:
    assert HYBRID_CANDIDATE_DEPTH_MIN == 256
    assert HYBRID_CANDIDATE_DEPTH_MULTIPLIER == 4
    assert HYBRID_CANDIDATE_DEPTH_CAP == 1024
    assert hybrid_candidate_depth(8, total_docs=10) == 10
    assert hybrid_candidate_depth(8, total_docs=300) == 256
    assert hybrid_candidate_depth(100, total_docs=5_183) == 400
    assert hybrid_candidate_depth(500, total_docs=5_183) == 1024
    assert hybrid_candidate_depth(2_048, total_docs=5_183) == 2_048


def test_hybrid_uses_bounded_candidate_depth_for_lexical_and_vector_channels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import llmwiki_serve.service as service_module

    root = tmp_path / "wiki"
    for index in range(300):
        write_markdown(
            root / f"page-{index:03}.md",
            f"""
---
review_state: approved
---
# Page {index:03}

common term evidence {index:03}
""",
        )
    service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
        vector_provider=FakeEmbeddingProvider(),
    )
    lexical_limits: list[int] = []
    hybrid_candidate_limits: list[int | None] = []
    real_search_corpus = service_module.search_corpus

    def counted_search_corpus(*args: Any, **kwargs: Any) -> Any:
        lexical_limits.append(int(kwargs["limit"]))
        return real_search_corpus(*args, **kwargs)

    def fake_hybrid_search_results(**kwargs: Any) -> list[Any]:
        hybrid_candidate_limits.append(kwargs.get("candidate_limit"))
        return []

    monkeypatch.setattr(service_module, "search_corpus", counted_search_corpus)
    monkeypatch.setattr(service_module, "hybrid_search_results", fake_hybrid_search_results)

    assert service.search("common term", mode="hybrid", limit=8) == []
    assert lexical_limits == [HYBRID_ORIENTATION_CANDIDATE_LIMIT, 256]
    assert hybrid_candidate_limits == [256]


def test_default_fastembed_model_constants_are_explicit() -> None:
    assert DEFAULT_FASTEMBED_MODEL == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    assert VECTOR_TEXT_SCHEMA_ID == "llmwiki-vector-text-v1"


def fake_vector(text: str) -> list[float]:
    lowered = text.casefold()
    if "draft semantic" in lowered or "draftonly" in lowered:
        return [0.0, 0.0, 1.0]
    if "invoice" in lowered or "refund" in lowered or "billing" in lowered:
        return [0.0, 1.0, 0.0]
    if "release.v1-beta.md" in lowered or "semantic decoy" in lowered:
        return [0.0, 1.0, 0.0]
    if "alpha" in lowered:
        return [1.0, 0.0, 0.0]
    return [0.2, 0.2, 0.2]


def sample_vector_wiki(root: Path, *, include_draft: bool = False) -> Path:
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Vector Fixture
review_state: approved
---
# Vector Fixture

Alpha overview content.
""",
    )
    write_markdown(
        root / "billing.md",
        """
---
title: Billing
review_state: approved
source_refs: ["SRC-BILLING"]
---
# Billing

semantic refund policy for reimbursement and payment handling.
""",
    )
    if include_draft:
        write_markdown(
            root / "draft.md",
            """
---
title: Draft
review_state: draft
---
# Draft

draft semantic private text.
""",
        )
    return root


def vector_page(
    page_id: str,
    path: str,
    text: str,
    *,
    source_refs: list[str] | None = None,
) -> WikiPage:
    return WikiPage(
        id=page_id,
        title="Alpha",
        path=path,
        role="topic",
        text=text,
        review_state="approved",
        source_refs=source_refs or [],
    )


def vector_record(page_id: str, vector: list[float]) -> vector_module.VectorChunkRecord:
    vector_tuple = tuple(vector)
    return vector_module.VectorChunkRecord(
        page_id=page_id,
        chunk_id=f"{page_id}:0",
        ordinal=0,
        start=0,
        end=20,
        heading_hash="",
        page_content_hash="",
        vector=vector_tuple,
        norm=vector_module.vector_norm(vector_tuple),
    )


def plain_hybrid_dicts(
    service: LlmWikiService,
    query: str,
    *,
    fields: list[str] | None = None,
    candidate_limit: int | None = None,
    suppress_orientation_answers: bool = False,
) -> list[dict[str, Any]]:
    index = service.index()
    views = service._index_views(index)  # noqa: SLF001
    corpus = views.search_corpus(False)
    resolved_candidate_limit = (
        hybrid_candidate_depth(8, total_docs=len(corpus.documents))
        if candidate_limit is None
        else candidate_limit
    )
    vector_results = service._vector_result_objects(  # noqa: SLF001
        index,
        corpus.pages,
        query,
        limit=resolved_candidate_limit,
        include_drafts=False,
        snippet_chars=None,
        exclude_page_ids=None,
    )
    lexical_results = search_corpus(
        corpus,
        query,
        limit=resolved_candidate_limit,
        mode="lexical",
        snippet_chars=None,
        min_score=None,
        exclude_page_ids=None,
    )
    return [
        item.model_dump(
            mode="json",
            include=set(fields) if fields is not None else None,
        )
        for item in plain_hybrid_search_results(
            lexical_results=lexical_results,
            vector_results=vector_results,
            corpus=corpus,
            query=query,
            limit=8,
            suppress_orientation_answers=suppress_orientation_answers,
        )
    ]


def write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")


def single_path(paths: Iterable[Path]) -> Path:
    items = list(paths)
    assert len(items) == 1
    return items[0]


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def mcp_call_payload(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def search_result(page_id: str, path: str, *, score: float) -> SearchResult:
    return SearchResult(
        page_id=page_id,
        title=page_id.replace("-", " ").title(),
        path=path,
        score=score,
        snippet="",
        role="topic",
        source_refs=[],
        route="vector",
    )


def vector_search_result(page_id: str, *, route: str) -> Any:
    return type("Result", (), {"page_id": page_id, "route": route})()
