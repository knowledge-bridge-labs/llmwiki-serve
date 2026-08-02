from __future__ import annotations

import inspect
import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from llmwiki_serve import LlmWikiService
from llmwiki_serve.models import WikiIndex
from scripts.benchmark_adapters import beir_scifact_acquire
from scripts.benchmark_adapters import scifact_runner as runner
from scripts.benchmark_adapters.beir_scifact import (
    MaterializeBeirScifactResult,
    materialize_beir_scifact,
)

ARCHIVE_SHA256 = "b" * 64
FAKE_IMPLEMENTATION_REVISION = "git:" + "1" * 40


def test_run_scifact_benchmark_uses_materialized_bundle_and_actual_service(
    tmp_path: Path,
) -> None:
    materialized = materialize_fixture(tmp_path, "actual-service")

    first = runner.run_scifact_benchmark(
        wiki_dir=materialized.wiki_dir,
        bundle_dir=materialized.bundle_dir,
        output_report=tmp_path / "report-1.json",
        implementation_revision=FAKE_IMPLEMENTATION_REVISION,
        run_manifest=tmp_path / "run-manifest.json",
        analyzer_profile="english",
    )
    second = runner.run_scifact_benchmark(
        wiki_dir=materialized.wiki_dir,
        bundle_dir=materialized.bundle_dir,
        output_report=tmp_path / "report-2.json",
        implementation_revision=FAKE_IMPLEMENTATION_REVISION,
        run_manifest=tmp_path / "run-manifest.json",
        analyzer_profile="english",
    )

    assert stable_report(first.report) == stable_report(second.report)
    assert first.wiki_before_sha256 == first.wiki_after_sha256
    assert first.bundle_before_sha256 == first.bundle_after_sha256
    assert first.report["schema_id"] == runner.REPORT_SCHEMA_ID
    assert first.report["schema_version"] == runner.REPORT_SCHEMA_VERSION
    assert first.report["analyzer_profile"] == "english"
    assert first.report["implementation_revision"] == FAKE_IMPLEMENTATION_REVISION
    assert first.report["dataset"] == "beir-scifact"
    assert first.report["corpus_count"] == 3
    assert first.report["query_count"] == 2
    assert first.report["qrel_count"] == 2
    assert first.report["judged_query_count"] == 2
    assert first.report["retrieval_limit"] == runner.RETRIEVAL_LIMIT
    assert first.report["freshness_policy"] == runner.freshness_policy_metadata()
    assert first.report["primary_metrics"] == {"Recall@100": 1.0, "nDCG@10": 1.0}
    assert first.report["product_secondary_metrics"] == {
        "Hit@5": 1.0,
        "MAP@100": 1.0,
        "MRR@10": 1.0,
        "Recall@5": 1.0,
    }
    assert first.report["quality_metrics"]["MAP@100"] == 1.0
    assert first.report["retrieval_mode"] == "lexical"
    assert first.report["languages_evaluated"] == ["en"]
    assert first.report["korean_quality"] == "not_evaluated"
    assert first.report["vector_provider"] == {"status": "not_used"}
    assert first.report["external_reference_rows"] == runner.external_reference_rows(
        cast(dict[str, float], first.report["primary_metrics"])
    )
    assert first.report["external_reference_rows"] == [
        {
            "delta_product_minus_reference": {
                "Recall@100": 0.092,
                "nDCG@10": 0.335,
            },
            "label": "BEIR paper BM25",
            "primary_metrics": {
                "Recall@100": 0.908,
                "nDCG@10": 0.665,
            },
            "reference_id": "beir-paper-bm25",
            "run_by_llmwiki_serve": False,
            "source_detail": "BEIR paper SciFact BM25 reference metrics.",
            "source_url": runner.BEIR_PAPER_BM25_SOURCE_URL,
            "status": runner.EXTERNAL_REFERENCE_STATUS,
        },
        {
            "delta_product_minus_reference": {
                "Recall@100": 0.0747,
                "nDCG@10": 0.3211,
            },
            "label": "Anserini/Pyserini flat BM25",
            "primary_metrics": {
                "Recall@100": 0.9253,
                "nDCG@10": 0.6789,
            },
            "reference_id": "anserini-pyserini-flat-bm25",
            "run_by_llmwiki_serve": False,
            "source_detail": "Anserini BEIR v1.0.0 SciFact flat BM25 regression metrics.",
            "source_url": runner.ANSERINI_SCIFACT_FLAT_BM25_SOURCE_URL,
            "status": runner.EXTERNAL_REFERENCE_STATUS,
        },
    ]
    assert first.report["python_version"]
    assert first.report["limitations"] == runner.report_limitations()
    assert (
        "Latency is warm fixed-index retrieval excluding source re-scan and "
        "one-time lexical/vector index warmup; index_build_ms and timings_ms "
        "report those setup costs separately."
    ) in first.report["limitations"]

    written_report = json.loads((tmp_path / "report-1.json").read_text(encoding="utf-8"))
    assert written_report == first.report
    provenance = json.loads(
        (materialized.bundle_dir / "provenance.json").read_text(encoding="utf-8")
    )
    assert written_report["component_licenses"] == provenance["component_licenses"]
    assert written_report["source_archive"] == {
        "content_sha256": f"sha256:{ARCHIVE_SHA256}",
        "content_sha256_source": "provenance.source_revision",
        "published_md5": {
            "reference_status": "published-reference",
            "value": beir_scifact_acquire.PUBLISHED_MD5,
        },
        "source_url": beir_scifact_acquire.SOURCE_URL,
    }
    report_text = json.dumps(written_report, sort_keys=True)
    assert str(materialized.wiki_dir) not in report_text
    assert str(materialized.bundle_dir) not in report_text
    assert "alpha therapy biomarker" not in report_text
    assert "beta marker treatment" not in report_text
    assert "unrelated gamma appendix" not in report_text
    assert "observed_md5" not in report_text
    source_archive_text = json.dumps(written_report["source_archive"], sort_keys=True)
    assert str(materialized.wiki_dir) not in source_archive_text
    assert str(materialized.bundle_dir) not in source_archive_text
    runner.validate_public_report(written_report)

    run_manifest = json.loads((tmp_path / "run-manifest.json").read_text(encoding="utf-8"))
    assert run_manifest["schema_id"] == runner.RUN_MANIFEST_SCHEMA_ID
    assert run_manifest["benchmark_configuration"] == {
        "analyzer_profile": "english",
        "implementation_revision": FAKE_IMPLEMENTATION_REVISION,
        "retrieval_mode": "lexical",
    }
    assert run_manifest["public_report_summary"]["analyzer_profile"] == "english"
    assert (
        run_manifest["public_report_summary"]["implementation_revision"]
        == FAKE_IMPLEMENTATION_REVISION
    )
    assert run_manifest["public_report_summary"]["schema_version"] == runner.REPORT_SCHEMA_VERSION
    assert run_manifest["tree_immutability"] == {
        "bundle_after_sha256": first.bundle_after_sha256,
        "bundle_before_sha256": first.bundle_before_sha256,
        "bundle_mutated": False,
        "wiki_after_sha256": first.wiki_after_sha256,
        "wiki_before_sha256": first.wiki_before_sha256,
        "wiki_mutated": False,
    }
    assert run_manifest["local_paths"]["wiki_dir"] == str(materialized.wiki_dir)


def test_default_service_factory_records_fixed_index_refresh_interval(tmp_path: Path) -> None:
    service = cast(LlmWikiService, runner.default_service_factory(tmp_path))
    english_service = cast(LlmWikiService, runner.default_service_factory(tmp_path, "english"))

    assert isinstance(service, LlmWikiService)
    assert service.refresh_interval_seconds == runner.BENCHMARK_FIXED_INDEX_REFRESH_INTERVAL_SECONDS
    assert service.analyzer_profile == runner.DEFAULT_ANALYZER_PROFILE
    assert runner.DEFAULT_ANALYZER_PROFILE == "legacy"
    assert english_service.analyzer_profile == "english"
    assert runner.BENCHMARK_FIXED_INDEX_REFRESH_INTERVAL_SECONDS > 0


def test_programmatic_public_report_generation_requires_explicit_metadata() -> None:
    for function in (
        runner.run_scifact_benchmark,
        runner.build_aggregate_report,
        runner.build_run_manifest,
    ):
        signature = inspect.signature(function)

        assert signature.parameters["analyzer_profile"].default is inspect.Parameter.empty
        assert signature.parameters["implementation_revision"].default is inspect.Parameter.empty


def test_public_report_freshness_policy_is_strict_and_public_safe(tmp_path: Path) -> None:
    materialized = materialize_fixture(tmp_path, "freshness-policy")

    result = runner.run_scifact_benchmark(
        wiki_dir=materialized.wiki_dir,
        bundle_dir=materialized.bundle_dir,
        output_report=tmp_path / "freshness-policy-report.json",
        implementation_revision=FAKE_IMPLEMENTATION_REVISION,
        analyzer_profile="english",
    )

    assert set(result.report) == runner.REPORT_TOP_LEVEL_FIELDS
    assert result.report["freshness_policy"] == {
        "mode": "warm-fixed-index-retrieval",
        "mutation_detection": "pre/post wiki and bundle tree SHA-256 digests",
        "refresh_interval_seconds": runner.BENCHMARK_FIXED_INDEX_REFRESH_INTERVAL_SECONDS,
        "service_configuration": (
            "LlmWikiService refresh_interval_seconds is fixed for the benchmark default "
            "factory so per-query service.search timing excludes source freshness scans."
        ),
    }
    runner.validate_public_report(result.report)


def test_extended_public_report_fields_and_mode_contract(tmp_path: Path) -> None:
    materialized = materialize_fixture(tmp_path, "extended-contract")
    result = runner.run_scifact_benchmark(
        wiki_dir=materialized.wiki_dir,
        bundle_dir=materialized.bundle_dir,
        output_report=tmp_path / "extended-contract-report.json",
        implementation_revision=FAKE_IMPLEMENTATION_REVISION,
        analyzer_profile="english",
    )

    assert set(result.report) == runner.REPORT_TOP_LEVEL_FIELDS
    assert result.report["schema_version"] == runner.REPORT_SCHEMA_VERSION
    assert result.report["retrieval_configuration"] == {
        "analyzer_profile": "english",
        "hybrid_channel_weights": {},
        "hybrid_candidate_depth_policy": None,
        "hybrid_plain_rrf_role": None,
        "hybrid_rrf_k": None,
        "hybrid_strategy": None,
        "retrieval_limit": runner.RETRIEVAL_LIMIT,
        "search_mode": "lexical",
        "vector_provider_required": False,
    }
    assert result.report["retrieval_schema"]["text_schema"] == runner.VECTOR_TEXT_SCHEMA_ID
    assert result.report["retrieval_schema"]["index_schema"] == runner.VECTOR_INDEX_SCHEMA_ID
    assert "MAP@100" in result.report["quality_metrics"]
    assert "cache_dir" not in json.dumps(result.report, sort_keys=True)
    runner.validate_public_report(result.report)

    vector_without_provider = cast(dict[str, Any], json.loads(json.dumps(result.report)))
    vector_without_provider["retrieval_mode"] = "vector"
    vector_without_provider["retrieval_configuration"]["search_mode"] = "vector"
    with pytest.raises(runner.ScifactRunnerError, match="configured vector_provider"):
        runner.validate_public_report(vector_without_provider)

    unknown_mode = cast(dict[str, Any], json.loads(json.dumps(result.report)))
    unknown_mode["retrieval_mode"] = "semantic"
    with pytest.raises(runner.ScifactRunnerError, match="search_mode"):
        runner.validate_public_report(unknown_mode)


def test_plain_rrf_benchmark_mode_executes_and_records_distinct_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    materialized = materialize_fixture(tmp_path, "plain-rrf")
    telemetry = runner.EmbeddingTelemetry(model_cache_bytes_before=0)

    class FakeProvider:
        provider_id = "fake"
        model_id = "fake-model"
        model_revision = "fake-revision"
        dimension = 3
        distance_metric = "cosine"

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            telemetry.document_calls += 1
            telemetry.document_texts += len(texts)
            return [self._vector(text) for text in texts]

        def embed_query(self, text: str) -> list[float]:
            telemetry.query_calls += 1
            return self._vector(text)

        def safe_metadata(self) -> dict[str, str | int]:
            return {
                "provider_id": self.provider_id,
                "model_id": self.model_id,
                "model_revision": self.model_revision,
                "dimension": self.dimension,
                "distance_metric": self.distance_metric,
                "fastembed_version": "fake",
                "numpy_version": "fake",
            }

        def _vector(self, text: str) -> list[float]:
            lowered = text.casefold()
            if "alpha" in lowered:
                return [1.0, 0.0, 0.0]
            if "beta" in lowered:
                return [0.0, 1.0, 0.0]
            return [0.0, 0.0, 1.0]

    fake_runtime = runner.VectorRuntime(
        cache_root=tmp_path / "plain-rrf-vector-cache",
        model_cache_root=tmp_path / "plain-rrf-model-cache",
        provider=cast(runner.TimedEmbeddingProvider, FakeProvider()),
        provider_metadata=FakeProvider().safe_metadata(),
        model_license="apache-2.0",
        model_source="fake/model",
        embedding_telemetry=telemetry,
    )
    monkeypatch.setattr(
        runner,
        "prepare_vector_runtime",
        lambda **_kwargs: fake_runtime,
    )

    result = runner.run_scifact_benchmark(
        wiki_dir=materialized.wiki_dir,
        bundle_dir=materialized.bundle_dir,
        output_report=tmp_path / "plain-rrf-report.json",
        implementation_revision=FAKE_IMPLEMENTATION_REVISION,
        analyzer_profile="english",
        search_mode="plain-rrf",
    )

    assert result.report["retrieval_mode"] == "plain-rrf"
    assert result.report["retrieval_configuration"]["search_mode"] == "plain-rrf"
    assert result.report["retrieval_configuration"]["hybrid_strategy"] == (
        "plain-lexical-vector-rrf-baseline"
    )
    assert result.report["retrieval_configuration"]["hybrid_candidate_depth_policy"] == {
        "cap": runner.HYBRID_CANDIDATE_DEPTH_CAP,
        "formula": ("min(total_docs, max(limit, min(cap, max(minimum, multiplier * limit))))"),
        "minimum": runner.HYBRID_CANDIDATE_DEPTH_MIN,
        "multiplier": runner.HYBRID_CANDIDATE_DEPTH_MULTIPLIER,
        "preserves_requested_limit": True,
    }
    candidate_depth = cast(dict[str, Any], result.report["timings_ms"])["hybrid_candidate_depth"]
    assert candidate_depth["count"] == 2
    assert candidate_depth["p50"] == 3
    assert candidate_depth["p95"] == 3
    assert result.report["retrieval_configuration"]["vector_provider_required"] is True
    assert result.report["vector_provider"]["status"] == "configured"
    assert result.report["primary_metrics"] == {"Recall@100": 1.0, "nDCG@10": 1.0}
    runner.validate_public_report(result.report)


def test_legacy_scifact_reports_remain_schema_compatible() -> None:
    reports = [
        Path("benchmarks/verified_sources/reports/beir-scifact-windows-2026-08-01.json"),
        Path("benchmarks/verified_sources/reports/beir-scifact-dgx-spark-ubuntu-2026-08-01.json"),
    ]
    for report_path in reports:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["schema_version"] == runner.LEGACY_REPORT_SCHEMA_VERSION
        runner.validate_public_report(report)


def test_public_report_rejects_reference_or_delta_tampering(tmp_path: Path) -> None:
    materialized = materialize_fixture(tmp_path, "reference-tamper")
    result = runner.run_scifact_benchmark(
        wiki_dir=materialized.wiki_dir,
        bundle_dir=materialized.bundle_dir,
        output_report=tmp_path / "reference-tamper-report.json",
        implementation_revision=FAKE_IMPLEMENTATION_REVISION,
        analyzer_profile="english",
    )

    tampered_url = cast(dict[str, Any], json.loads(json.dumps(result.report)))
    reference_rows = cast(list[dict[str, Any]], tampered_url["external_reference_rows"])
    reference_rows[0]["source_url"] = "https://example.com/not-the-beir-paper"
    with pytest.raises(runner.ScifactRunnerError, match="external_reference_rows"):
        runner.validate_public_report(tampered_url)

    tampered_metric = cast(dict[str, Any], json.loads(json.dumps(result.report)))
    reference_rows = cast(list[dict[str, Any]], tampered_metric["external_reference_rows"])
    reference_rows[1]["primary_metrics"]["nDCG@10"] = 0.1
    with pytest.raises(runner.ScifactRunnerError, match="external_reference_rows"):
        runner.validate_public_report(tampered_metric)

    tampered_delta = cast(dict[str, Any], json.loads(json.dumps(result.report)))
    reference_rows = cast(list[dict[str, Any]], tampered_delta["external_reference_rows"])
    reference_rows[0]["delta_product_minus_reference"]["Recall@100"] = 0.0
    with pytest.raises(runner.ScifactRunnerError, match="external_reference_rows"):
        runner.validate_public_report(tampered_delta)


def test_public_report_rejects_invalid_implementation_revision(tmp_path: Path) -> None:
    materialized = materialize_fixture(tmp_path, "invalid-revision")

    with pytest.raises(runner.ScifactRunnerError, match="all-zero placeholder"):
        runner.validate_implementation_revision("git:" + "0" * 40)

    with pytest.raises(runner.ScifactRunnerError, match="implementation_revision"):
        runner.run_scifact_benchmark(
            wiki_dir=materialized.wiki_dir,
            bundle_dir=materialized.bundle_dir,
            output_report=tmp_path / "invalid-revision-report.json",
            implementation_revision="main",
            analyzer_profile="english",
        )

    result = runner.run_scifact_benchmark(
        wiki_dir=materialized.wiki_dir,
        bundle_dir=materialized.bundle_dir,
        output_report=tmp_path / "valid-revision-report.json",
        implementation_revision="git:" + "a" * 40,
        analyzer_profile="english",
    )
    assert result.report["implementation_revision"] == "git:" + "a" * 40

    tampered_report = cast(dict[str, Any], json.loads(json.dumps(result.report)))
    tampered_report["implementation_revision"] = "git:" + "A" * 40
    with pytest.raises(runner.ScifactRunnerError, match="implementation_revision"):
        runner.validate_public_report(tampered_report)

    zero_revision_report = cast(dict[str, Any], json.loads(json.dumps(result.report)))
    zero_revision_report["implementation_revision"] = "git:" + "0" * 40
    with pytest.raises(runner.ScifactRunnerError, match="all-zero placeholder"):
        runner.validate_public_report(zero_revision_report)


def test_public_report_rejects_non_public_analyzer_profiles(tmp_path: Path) -> None:
    materialized = materialize_fixture(tmp_path, "invalid-analyzer")
    result = runner.run_scifact_benchmark(
        wiki_dir=materialized.wiki_dir,
        bundle_dir=materialized.bundle_dir,
        output_report=tmp_path / "valid-analyzer-report.json",
        implementation_revision=FAKE_IMPLEMENTATION_REVISION,
        analyzer_profile="english",
    )

    for analyzer_profile in ("english_additive", "english_flatlike", "unknown"):
        tampered_report = cast(dict[str, Any], json.loads(json.dumps(result.report)))
        tampered_report["analyzer_profile"] = analyzer_profile

        with pytest.raises(runner.ScifactRunnerError, match="analyzer_profile"):
            runner.validate_public_report(tampered_report)

    with pytest.raises(runner.ScifactRunnerError, match="analyzer_profile"):
        runner.run_scifact_benchmark(
            wiki_dir=materialized.wiki_dir,
            bundle_dir=materialized.bundle_dir,
            output_report=tmp_path / "internal-analyzer-report.json",
            implementation_revision=FAKE_IMPLEMENTATION_REVISION,
            analyzer_profile=cast(runner.AnalyzerProfile, "english_additive"),
        )


def test_runner_calls_search_once_per_query_with_limit_100(tmp_path: Path) -> None:
    materialized = materialize_fixture(tmp_path, "tracked-service")
    calls: list[tuple[str, int]] = []
    profiles: list[str] = []

    class TrackingService:
        def __init__(
            self,
            wiki_dir: Path,
            analyzer_profile: runner.AnalyzerProfile = runner.DEFAULT_ANALYZER_PROFILE,
        ) -> None:
            profiles.append(analyzer_profile)
            self._service = LlmWikiService(wiki_dir, analyzer_profile=analyzer_profile)

        def index(self) -> WikiIndex:
            return self._service.index()

        def search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
            calls.append((query, limit))
            return self._service.search(query, limit=limit)

    runner.run_scifact_benchmark(
        wiki_dir=materialized.wiki_dir,
        bundle_dir=materialized.bundle_dir,
        output_report=tmp_path / "tracked-report.json",
        implementation_revision=FAKE_IMPLEMENTATION_REVISION,
        service_factory=TrackingService,
        analyzer_profile="english",
    )

    assert profiles == ["english"]
    assert calls == [
        ("alpha therapy biomarker response", 100),
        ("beta marker treatment response", 100),
    ]


def test_plain_rrf_reuses_service_search_corpus_and_avoids_model_revalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import llmwiki_serve.service as service_module
    from llmwiki_serve.models import SearchResult
    from llmwiki_serve.vector import VectorConfig

    materialized = materialize_fixture(tmp_path, "plain-rrf-corpus-reuse")
    telemetry = runner.EmbeddingTelemetry(model_cache_bytes_before=0)
    build_calls = 0
    real_build_search_corpus = service_module.build_search_corpus

    class FakeProvider:
        provider_id = "fake"
        model_id = "fake-model"
        model_revision = "fake-revision"
        dimension = 3
        distance_metric = "cosine"

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            telemetry.document_calls += 1
            return [self._vector(text) for text in texts]

        def embed_query(self, text: str) -> list[float]:
            telemetry.query_calls += 1
            return self._vector(text)

        def safe_metadata(self) -> dict[str, str | int]:
            return {
                "provider_id": self.provider_id,
                "model_id": self.model_id,
                "model_revision": self.model_revision,
                "dimension": self.dimension,
                "distance_metric": self.distance_metric,
            }

        def _vector(self, text: str) -> list[float]:
            lowered = text.casefold()
            if "alpha" in lowered:
                return [1.0, 0.0, 0.0]
            if "beta" in lowered:
                return [0.0, 1.0, 0.0]
            return [0.0, 0.0, 1.0]

    def counted_build_search_corpus(*args: Any, **kwargs: Any) -> Any:
        nonlocal build_calls
        build_calls += 1
        return real_build_search_corpus(*args, **kwargs)

    def reject_model_revalidate(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("plain-RRF benchmark wrapper should not revalidate result dicts")

    monkeypatch.setattr(service_module, "build_search_corpus", counted_build_search_corpus)
    monkeypatch.setattr(SearchResult, "model_validate", reject_model_revalidate)
    service = LlmWikiService(
        materialized.wiki_dir,
        refresh_interval_seconds=runner.BENCHMARK_FIXED_INDEX_REFRESH_INTERVAL_SECONDS,
        analyzer_profile="english",
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
        vector_provider=FakeProvider(),
    )
    wrapper = runner.PlainRrfBenchmarkService(service, analyzer_profile="english")

    first = wrapper.search("alpha therapy biomarker response", limit=100)
    second = wrapper.search("beta marker treatment response", limit=100)

    assert first[0]["page_id"]
    assert second[0]["page_id"]
    assert build_calls == 1
    assert telemetry.document_calls == 1
    assert telemetry.query_calls == 2

    (materialized.wiki_dir / "doc-refresh.md").write_text(
        """---
original_id: "doc-refresh"
review_state: approved
---
# Refresh Evidence

gamma refresh evidence
""",
        encoding="utf-8",
    )
    service.index(refresh=True)
    refreshed = wrapper.search("gamma refresh evidence", limit=100)

    assert refreshed[0]["page_id"]
    assert build_calls == 2


def test_metric_cutoffs_include_rank_100_but_not_rank_11_for_at_10_metrics() -> None:
    ranked = [f"d{rank}" for rank in range(1, 101)]

    metrics = runner.compute_query_metrics(
        ranked,
        {
            "d11": 1.0,
            "d100": 1.0,
        },
    )

    assert metrics.recall_at_100 == 1.0
    assert metrics.recall_at_5 == 0.0
    assert metrics.hit_at_5 == 0.0
    assert metrics.mrr_at_10 == 0.0
    assert metrics.ndcg_at_10 == 0.0
    assert metrics.map_at_100 == pytest.approx(((1 / 11) + (2 / 100)) / 2)


def test_metric_cutoffs_include_rank_5_and_rank_10_boundaries() -> None:
    ranked = [f"d{rank}" for rank in range(1, 11)]

    rank_5_metrics = runner.compute_query_metrics(ranked, {"d5": 1.0, "d6": 1.0})
    assert rank_5_metrics.recall_at_5 == 0.5
    assert rank_5_metrics.hit_at_5 == 1.0

    rank_10_metrics = runner.compute_query_metrics(ranked, {"d10": 1.0})
    assert rank_10_metrics.mrr_at_10 == 0.1


def test_ndcg_uses_standard_exponential_gain() -> None:
    observed = runner.ndcg_at_k(
        ["low-relevance", "high-relevance"],
        {"high-relevance": 2.0, "low-relevance": 1.0},
        10,
    )
    expected = (1.0 / math.log2(2) + 3.0 / math.log2(3)) / (3.0 / math.log2(2) + 1.0 / math.log2(3))

    assert observed == pytest.approx(expected)


def test_path_to_original_id_mapping_rejects_missing_and_duplicate_original_id(
    tmp_path: Path,
) -> None:
    missing = materialize_fixture(tmp_path, "missing-original-id")
    remove_original_id(next(missing.wiki_dir.glob("*.md")))

    with pytest.raises(runner.ScifactRunnerError, match="missing frontmatter original_id"):
        runner.run_scifact_benchmark(
            wiki_dir=missing.wiki_dir,
            bundle_dir=missing.bundle_dir,
            output_report=tmp_path / "missing-report.json",
            implementation_revision=FAKE_IMPLEMENTATION_REVISION,
            analyzer_profile="english",
        )
    assert not (tmp_path / "missing-report.json").exists()

    duplicate = materialize_fixture(tmp_path, "duplicate-original-id")
    wiki_files = sorted(duplicate.wiki_dir.glob("*.md"))
    first_id = read_original_id(wiki_files[0])
    set_original_id(wiki_files[1], first_id)

    with pytest.raises(runner.ScifactRunnerError, match="duplicate indexed page original_id"):
        runner.run_scifact_benchmark(
            wiki_dir=duplicate.wiki_dir,
            bundle_dir=duplicate.bundle_dir,
            output_report=tmp_path / "duplicate-report.json",
            implementation_revision=FAKE_IMPLEMENTATION_REVISION,
            analyzer_profile="english",
        )
    assert not (tmp_path / "duplicate-report.json").exists()


def test_report_and_run_manifest_repo_path_policies(tmp_path: Path) -> None:
    materialized = materialize_fixture(tmp_path, "path-policy")
    repo_root = tmp_path / "repo"

    with pytest.raises(runner.ScifactRunnerError, match="benchmarks/verified_sources/reports"):
        runner.run_scifact_benchmark(
            wiki_dir=materialized.wiki_dir,
            bundle_dir=materialized.bundle_dir,
            output_report=repo_root / "reports" / "scifact.json",
            implementation_revision=FAKE_IMPLEMENTATION_REVISION,
            repo_root=repo_root,
            analyzer_profile="english",
        )

    with pytest.raises(runner.ScifactRunnerError, match="benchmark-adapters"):
        runner.run_scifact_benchmark(
            wiki_dir=materialized.wiki_dir,
            bundle_dir=materialized.bundle_dir,
            output_report=tmp_path / "manifest-policy-report.json",
            implementation_revision=FAKE_IMPLEMENTATION_REVISION,
            run_manifest=repo_root / ".llmwiki-work" / "other" / "run-manifest.json",
            repo_root=repo_root,
            analyzer_profile="english",
        )

    allowed_report = repo_root / "benchmarks" / "verified_sources" / "reports" / "scifact.json"
    allowed_manifest = (
        repo_root / ".llmwiki-work" / "benchmark-adapters" / "scifact" / "run-manifest.json"
    )
    result = runner.run_scifact_benchmark(
        wiki_dir=materialized.wiki_dir,
        bundle_dir=materialized.bundle_dir,
        output_report=allowed_report,
        implementation_revision=FAKE_IMPLEMENTATION_REVISION,
        run_manifest=allowed_manifest,
        repo_root=repo_root,
        analyzer_profile="english",
    )

    assert json.loads(allowed_report.read_text(encoding="utf-8")) == result.report
    assert json.loads(allowed_manifest.read_text(encoding="utf-8"))["schema_id"] == (
        runner.RUN_MANIFEST_SCHEMA_ID
    )


def test_cli_requires_public_profile_and_writes_report(tmp_path: Path) -> None:
    materialized = materialize_fixture(tmp_path, "cli")
    missing_profile_report = tmp_path / "cli-missing-profile-report.json"
    implementation_revision = "git:" + "1" * 40

    with pytest.raises(SystemExit) as missing_profile:
        runner.main(
            [
                "--wiki-dir",
                str(materialized.wiki_dir),
                "--bundle-dir",
                str(materialized.bundle_dir),
                "--output-report",
                str(missing_profile_report),
                "--implementation-revision",
                implementation_revision,
            ]
        )
    assert missing_profile.value.code == 2
    assert not missing_profile_report.exists()

    english_report = tmp_path / "cli-english-report.json"
    exit_code = runner.main(
        [
            "--wiki-dir",
            str(materialized.wiki_dir),
            "--bundle-dir",
            str(materialized.bundle_dir),
            "--output-report",
            str(english_report),
            "--implementation-revision",
            implementation_revision,
            "--analyzer-profile",
            "english",
        ]
    )

    assert exit_code == 0
    report = json.loads(english_report.read_text(encoding="utf-8"))
    assert report["schema_id"] == runner.REPORT_SCHEMA_ID
    assert report["analyzer_profile"] == "english"
    assert report["implementation_revision"] == implementation_revision

    for analyzer_profile in ("english_additive", "english_flatlike"):
        with pytest.raises(SystemExit) as internal:
            runner.main(
                [
                    "--wiki-dir",
                    str(materialized.wiki_dir),
                    "--bundle-dir",
                    str(materialized.bundle_dir),
                    "--output-report",
                    str(tmp_path / f"{analyzer_profile}-profile-report.json"),
                    "--implementation-revision",
                    implementation_revision,
                    "--analyzer-profile",
                    analyzer_profile,
                ]
            )
        assert internal.value.code == 2

    with pytest.raises(SystemExit) as invalid:
        runner.main(
            [
                "--wiki-dir",
                str(materialized.wiki_dir),
                "--bundle-dir",
                str(materialized.bundle_dir),
                "--output-report",
                str(tmp_path / "invalid-profile-report.json"),
                "--implementation-revision",
                implementation_revision,
                "--analyzer-profile",
                "unknown",
            ]
        )
    assert invalid.value.code == 2


def test_cli_requires_and_validates_implementation_revision(tmp_path: Path) -> None:
    materialized = materialize_fixture(tmp_path, "cli-revision")

    with pytest.raises(SystemExit) as missing:
        runner.main(
            [
                "--wiki-dir",
                str(materialized.wiki_dir),
                "--bundle-dir",
                str(materialized.bundle_dir),
                "--output-report",
                str(tmp_path / "missing-revision-report.json"),
                "--analyzer-profile",
                "english",
            ]
        )
    assert missing.value.code == 2

    invalid_exit_code = runner.main(
        [
            "--wiki-dir",
            str(materialized.wiki_dir),
            "--bundle-dir",
            str(materialized.bundle_dir),
            "--output-report",
            str(tmp_path / "bad-revision-report.json"),
            "--implementation-revision",
            "git:" + "G" * 40,
            "--analyzer-profile",
            "english",
        ]
    )

    assert invalid_exit_code == 2
    assert not (tmp_path / "bad-revision-report.json").exists()


def test_runner_fails_when_wiki_or_bundle_mutates_during_run(tmp_path: Path) -> None:
    materialized = materialize_fixture(tmp_path, "immutability")

    class WikiMutatingService:
        def __init__(
            self,
            wiki_dir: Path,
            analyzer_profile: runner.AnalyzerProfile = runner.DEFAULT_ANALYZER_PROFILE,
        ) -> None:
            self._wiki_dir = wiki_dir
            self._service = LlmWikiService(wiki_dir, analyzer_profile=analyzer_profile)

        def index(self) -> WikiIndex:
            return self._service.index()

        def search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
            (self._wiki_dir / "mutation.txt").write_text("mutation\n", encoding="utf-8")
            return self._service.search(query, limit=limit)

    with pytest.raises(runner.ScifactRunnerError, match="wiki tree mutated"):
        runner.run_scifact_benchmark(
            wiki_dir=materialized.wiki_dir,
            bundle_dir=materialized.bundle_dir,
            output_report=tmp_path / "wiki-mutated-report.json",
            implementation_revision=FAKE_IMPLEMENTATION_REVISION,
            service_factory=WikiMutatingService,
            analyzer_profile="english",
        )
    assert not (tmp_path / "wiki-mutated-report.json").exists()

    materialized = materialize_fixture(tmp_path, "bundle-immutability")

    class BundleMutatingService:
        def __init__(
            self,
            wiki_dir: Path,
            analyzer_profile: runner.AnalyzerProfile = runner.DEFAULT_ANALYZER_PROFILE,
        ) -> None:
            self._service = LlmWikiService(wiki_dir, analyzer_profile=analyzer_profile)

        def index(self) -> WikiIndex:
            return self._service.index()

        def search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
            (materialized.bundle_dir / "mutation.txt").write_text("mutation\n", encoding="utf-8")
            return self._service.search(query, limit=limit)

    with pytest.raises(runner.ScifactRunnerError, match="bundle tree mutated"):
        runner.run_scifact_benchmark(
            wiki_dir=materialized.wiki_dir,
            bundle_dir=materialized.bundle_dir,
            output_report=tmp_path / "bundle-mutated-report.json",
            implementation_revision=FAKE_IMPLEMENTATION_REVISION,
            service_factory=BundleMutatingService,
            analyzer_profile="english",
        )
    assert not (tmp_path / "bundle-mutated-report.json").exists()


def materialize_fixture(tmp_path: Path, name: str) -> MaterializeBeirScifactResult:
    input_dir = make_scifact_input(tmp_path / f"{name}-input")
    return materialize_beir_scifact(
        input_dir,
        tmp_path / f"{name}-materialized",
        ARCHIVE_SHA256,
        enforce_official_canonical_invariants=False,
    )


def make_scifact_input(root: Path) -> Path:
    root.mkdir(parents=True)
    write_jsonl(
        root / "corpus.jsonl",
        [
            {
                "_id": "doc-alpha",
                "metadata": {},
                "text": "alpha therapy biomarker response evidence for claim one",
                "title": "Alpha Therapy Evidence",
            },
            {
                "_id": "doc-beta",
                "metadata": {},
                "text": "beta marker treatment response evidence for claim two",
                "title": "Beta Marker Evidence",
            },
            {
                "_id": "doc-gamma",
                "metadata": {},
                "text": "unrelated gamma appendix without matching retrieval terms",
                "title": "Gamma Appendix",
            },
        ],
    )
    write_jsonl(
        root / "queries.jsonl",
        [
            {"_id": "q-alpha", "metadata": {}, "text": "alpha therapy biomarker response"},
            {"_id": "q-beta", "metadata": {}, "text": "beta marker treatment response"},
        ],
    )
    qrels_dir = root / "qrels"
    qrels_dir.mkdir()
    (qrels_dir / "test.tsv").write_text(
        "query-id\tcorpus-id\tscore\nq-alpha\tdoc-alpha\t1\nq-beta\tdoc-beta\t1\n",
        encoding="utf-8",
    )
    return root


def write_jsonl(path: Path, rows: list[Mapping[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def remove_original_id(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(
        re.sub(r"^original_id: .*\n", "", text, count=1, flags=re.MULTILINE),
        encoding="utf-8",
    )


def read_original_id(path: Path) -> str:
    match = re.search(
        r"^original_id: \"(?P<original_id>[^\"]+)\"$",
        path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match is not None
    return match.group("original_id")


def set_original_id(path: Path, original_id: str) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(
        re.sub(
            r"^original_id: .*$",
            f"original_id: {json.dumps(original_id)}",
            text,
            count=1,
            flags=re.MULTILINE,
        ),
        encoding="utf-8",
    )


def stable_report(report: Mapping[str, object]) -> dict[str, object]:
    stable = cast(dict[str, object], json.loads(json.dumps(report, sort_keys=True)))
    stable["index_build_ms"] = "<timing>"
    latency = cast(dict[str, object], stable["search_latency_ms_top100_result_payloads"])
    latency["p50"] = "<timing>"
    latency["p95"] = "<timing>"
    timings = cast(dict[str, object], stable["timings_ms"])
    timings["cold_projection_index_build_ms"] = "<timing>"
    timings["lexical_corpus_build_or_load_ms"] = "<timing>"
    timings["rss_observed_peak_bytes"] = "<rss>"
    end_to_end = cast(dict[str, object], timings["warm_query_end_to_end_ms"])
    end_to_end["p50"] = "<timing>"
    end_to_end["p95"] = "<timing>"
    lexical = cast(dict[str, object], timings["lexical_search_ms"])
    lexical["p50"] = "<timing>"
    lexical["p95"] = "<timing>"
    return stable
