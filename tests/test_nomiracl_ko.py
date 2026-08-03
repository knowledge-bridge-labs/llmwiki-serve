from __future__ import annotations

import gzip
import hashlib
import io
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pytest

from scripts.benchmark_adapters import bundle_validator as validator
from scripts.benchmark_adapters import nomiracl_ko
from scripts.benchmark_adapters import nomiracl_ko_runner as runner
from scripts.benchmark_adapters.scifact_runner import EmbeddingTelemetry, VectorRuntime

FAKE_IMPLEMENTATION_REVISION = "git:" + "1" * 40


def test_acquire_nomiracl_ko_verifies_revision_license_tree_digest_and_counts(
    tmp_path: Path,
) -> None:
    payloads = fixture_payloads()
    expected_files = expected_files_for_payloads(payloads)
    expected_counts = nomiracl_ko.NomiraclKoSourceCounts(
        corpus_row_count=7,
        unique_document_count=6,
        duplicate_corpus_row_count=1,
        dev_relevant_query_count=2,
        dev_relevant_qrel_count=3,
        dev_relevant_positive_qrel_count=2,
        dev_non_relevant_query_count=3,
        dev_non_relevant_qrel_count=6,
        dev_non_relevant_positive_qrel_count=0,
    )

    result = nomiracl_ko.acquire_nomiracl_ko(
        nomiracl_ko.AcquireNomiraclKoConfig(source_dir=tmp_path / "source"),
        source_opener=source_opener_for(payloads),
        expected_files=expected_files,
        expected_counts=expected_counts,
    )

    assert result.cache_status == "downloaded"
    assert result.counts == expected_counts
    public_text = json.dumps(result.as_public_json(), ensure_ascii=False, sort_keys=True)
    assert "알파 치료 문서" not in public_text
    assert str(tmp_path) not in public_text

    bad_expected = list(expected_files)
    bad_expected[0] = nomiracl_ko.OfficialSourceFile(
        bad_expected[0].relative_path,
        "0" * 64,
        bad_expected[0].size_bytes,
    )
    with pytest.raises(nomiracl_ko.NomiraclKoError, match="SHA-256 mismatch|LFS digest"):
        nomiracl_ko.acquire_nomiracl_ko(
            nomiracl_ko.AcquireNomiraclKoConfig(source_dir=tmp_path / "bad-source"),
            source_opener=source_opener_for(payloads),
            expected_files=tuple(bad_expected),
            expected_counts=expected_counts,
        )


def test_materialize_nomiracl_ko_deduplicates_samples_and_avoids_orientation_pages(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_dir = make_nomiracl_source_fixture(tmp_path / "source")

    result = nomiracl_ko.materialize_nomiracl_ko(
        source_dir,
        tmp_path / "out",
        non_relevant_sample_size=2,
        enforce_official_canonical_invariants=False,
    )

    assert result.corpus_count == 4
    assert result.source_counts.corpus_row_count == 7
    assert result.source_counts.unique_document_count == 6
    assert result.evaluation_pool.protocol == "judged_pool"
    assert result.evaluation_pool.full_corpus is False
    assert result.relevant_query_count == 2
    assert result.relevant_qrel_count == 3
    assert result.positive_qrel_count == 2
    assert result.non_relevant_sample_query_count == 2
    assert result.non_relevant_sample_qrel_count == 4
    assert not result.source_mutated
    assert result.non_relevant_sample_query_ids_sha256 == nomiracl_ko.sample_query_ids_sha256(
        nomiracl_ko.deterministic_non_relevant_sample(
            [
                nomiracl_ko.SourceQueryRow("q-negative-1", "관련 없는 질문 하나"),
                nomiracl_ko.SourceQueryRow("q-negative-2", "관련 없는 질문 둘"),
                nomiracl_ko.SourceQueryRow("q-negative-3", "관련 없는 질문 셋"),
            ],
            sample_size=2,
        )
    )

    wiki_files = sorted(path.name for path in result.wiki_dir.glob("*.md"))
    assert len(wiki_files) == 4
    assert not {"hot.md", "index.md", "overview.md", "quickstart.md"} & set(wiki_files)
    assert all(name.startswith("doc-") and name.endswith(".md") for name in wiki_files)
    markdown_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(result.wiki_dir.glob("*.md"))
    )
    first_markdown = next(result.wiki_dir.glob("*.md")).read_text(encoding="utf-8")
    assert "original_id:" in first_markdown
    assert "review_state: approved" in first_markdown
    assert "doc-train-only" not in markdown_text
    assert "doc-test-only" not in markdown_text

    bundle = validator.validate_bundle(result.bundle_dir)
    assert len(bundle.corpus_ids) == 4
    assert len(bundle.query_ids) == 4
    assert bundle.qrel_count == 7
    assert bundle.corpus_ids == frozenset({"doc-alpha", "doc-beta", "doc-gamma", "doc-delta"})
    assert "doc-train-only" not in bundle.corpus_ids
    assert "doc-test-only" not in bundle.corpus_ids
    provenance = json.loads((result.bundle_dir / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["benchmark_protocol"] == "judged_pool"
    assert provenance["full_corpus"] is False
    assert provenance["official_full_corpus"] == nomiracl_ko.OFFICIAL_SOURCE_COUNTS.as_public_json()
    assert provenance["evaluation_pool"] == result.evaluation_pool.as_public_json()
    assert provenance["evaluation_pool"]["document_count"] == 4
    assert provenance["evaluation_pool"]["qrel_count"] == 7
    assert provenance["evaluation_pool"]["qrel_rows_sha256"] == (
        "sha256:" + validator.canonical_text_file_sha256(result.bundle_dir / "qrels.jsonl")
    )
    assert provenance["source_revision"] == f"git:{nomiracl_ko.HF_REVISION}"
    assert provenance["component_licenses"][0]["license_spdx"] == "Apache-2.0"

    repeated = nomiracl_ko.materialize_nomiracl_ko(
        source_dir,
        tmp_path / "out-repeat",
        non_relevant_sample_size=2,
        enforce_official_canonical_invariants=False,
    )
    assert (
        repeated.evaluation_pool.document_ids_sha256 == result.evaluation_pool.document_ids_sha256
    )
    assert repeated.evaluation_pool.pool_sha256 == result.evaluation_pool.pool_sha256

    exit_code = nomiracl_ko.run(
        [
            "materialize",
            "--source-dir",
            str(source_dir),
            "--output-dir",
            str(tmp_path / ".llmwiki-work" / "benchmark-adapters" / "cli-out"),
            "--repo-root",
            str(tmp_path),
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "official NoMIRACL-ko" in captured.err
    assert "알파 치료 문서" not in captured.out
    assert str(source_dir) not in captured.out


def test_materialize_nomiracl_ko_fails_when_selected_qrel_doc_is_missing(
    tmp_path: Path,
) -> None:
    payloads = fixture_payloads()
    payloads["data/korean/qrels/dev.relevant.tsv"] = (
        b"q-alpha\tQ0\tdoc-alpha\t1\nq-beta\tQ0\tdoc-missing\t1\n"
    )
    source_dir = make_nomiracl_source_fixture(tmp_path / "source", payloads=payloads)

    with pytest.raises(nomiracl_ko.NomiraclKoError, match="unknown corpus_id|missing corpus"):
        nomiracl_ko.materialize_nomiracl_ko(
            source_dir,
            tmp_path / "out",
            non_relevant_sample_size=2,
            enforce_official_canonical_invariants=False,
        )


def test_run_nomiracl_ko_benchmark_all_modes_public_safe_with_fake_provider(
    tmp_path: Path,
) -> None:
    materialized = nomiracl_ko.materialize_nomiracl_ko(
        make_nomiracl_source_fixture(tmp_path / "source"),
        tmp_path / "materialized",
        non_relevant_sample_size=2,
        enforce_official_canonical_invariants=False,
    )
    telemetry = EmbeddingTelemetry(model_cache_bytes_before=0)
    provider = FakeProvider(telemetry)

    def fake_vector_runtime_factory(**_kwargs: Any) -> VectorRuntime:
        return VectorRuntime(
            cache_root=tmp_path / "vector-cache",
            model_cache_root=tmp_path / "model-cache",
            provider=cast(Any, provider),
            provider_metadata=provider.safe_metadata(),
            model_license="Apache-2.0",
            model_source="fake",
            embedding_telemetry=telemetry,
        )

    progress_events: list[Mapping[str, object]] = []
    result = runner.run_nomiracl_ko_benchmark(
        wiki_dir=materialized.wiki_dir,
        bundle_dir=materialized.bundle_dir,
        output_report=tmp_path / "report.json",
        implementation_revision=FAKE_IMPLEMENTATION_REVISION,
        search_modes=("lexical", "vector", "plain-rrf", "hybrid"),
        relevant_query_limit=2,
        non_relevant_query_limit=2,
        vector_runtime_factory=fake_vector_runtime_factory,
        progress_callback=progress_events.append,
    )

    assert result.wiki_before_sha256 == result.wiki_after_sha256
    assert result.bundle_before_sha256 == result.bundle_after_sha256
    mode_results = cast(dict[str, Any], result.report["mode_results"])
    assert set(mode_results) == {"lexical", "vector", "plain-rrf", "hybrid"}
    assert result.report["benchmark_claim_scope"] == "judged-pool-only"
    assert result.report["protocol"] == "judged_pool"
    assert result.report["full_corpus"] is False
    assert result.report["abstention_policy"] == {
        "calibrated_threshold": None,
        "evaluated": False,
        "no_abstention_threshold_claim": True,
        "no_calibrated_threshold_claim": True,
        "reason": (
            "LlmWikiService.search returns ranked retrieval results; this runner does not "
            "convert scores into answer/abstain decisions."
        ),
        "supported": False,
    }
    assert (
        result.report["official_full_corpus"] == nomiracl_ko.OFFICIAL_SOURCE_COUNTS.as_public_json()
    )
    assert result.report["evaluation_pool"]["document_count"] == 4
    assert result.report["evaluation_pool"]["query_count"] == 4
    assert result.report["evaluation_pool"]["qrel_count"] == 7
    assert result.report["languages_evaluated"] == ["ko"]
    assert result.report["positive_qrel_count"] == 2
    assert result.report["report_policy"]["policy_id"] == (
        "nomiracl-ko-judged-pool-public-report-policy-v1"
    )
    assert result.report["report_policy"]["calibrated_threshold_claim_allowed"] is False
    assert result.report["report_policy"]["public_report_gate_passed"] is True
    assert result.report["retrieval_schema"]["vector_search_backend"] == (
        "exact-cosine-over-loaded-chunk-vectors"
    )
    assert result.report["retrieval_schema"]["approximate_vector_index"] is False
    assert result.report["tested_size_envelope"] == {
        "answerable_query_count": 2,
        "benchmark_claim_scope": "judged-pool-only",
        "corpus_count": 4,
        "full_corpus": False,
        "non_relevant_qrel_count": 4,
        "positive_qrel_count": 2,
        "protocol": "judged_pool",
        "qrel_count": 7,
        "query_count": 4,
        "relevant_qrel_count": 3,
        "retrieval_limit": runner.RETRIEVAL_LIMIT,
        "size_claim": "tested rows in this report only",
        "unanswerable_diagnostic_query_count": 2,
    }
    assert result.report["unanswerable_diagnostics"] == {
        "abstention_supported": False,
        "answerability_label": "unanswerable",
        "diagnostic_only": True,
        "metric_use": "non-relevant retrieval exposure and score separation only",
        "positive_qrel_count": 0,
        "qrel_count": 4,
        "qrel_label": "official zero-relevance qrels only",
        "query_count": 2,
        "source_split": "dev.non_relevant",
    }
    assert result.report["embedding_telemetry"]["document_embedding_calls"] == 1
    assert result.report["embedding_telemetry"]["query_embedding_calls"] >= 1
    assert provider.document_calls == 1
    assert provider.query_calls >= 1
    assert [event["stage"] for event in progress_events if event["stage"] == "mode_finished"] == [
        "mode_finished",
        "mode_finished",
        "mode_finished",
        "mode_finished",
    ]
    assert any(
        event.get("stage") == "bundle_loaded" and event.get("corpus_count") == 4
        for event in progress_events
    )

    for mode, mode_report in mode_results.items():
        assert mode_report["retrieval_mode"] == mode
        assert mode_report["quality_metrics"]["Recall@100"] >= 0.0
        assert mode_report["non_relevant_diagnostics"]["abstention_supported"] is False
        assert mode_report["non_relevant_diagnostics"]["answerability_label"] == "unanswerable"
        assert mode_report["non_relevant_diagnostics"]["no_abstention_threshold_claim"] is True
        assert mode_report["non_relevant_diagnostics"]["no_calibrated_threshold_claim"] is True
        assert "judged_nonrelevant_hits_at_10" in mode_report["non_relevant_diagnostics"]
        assert "results_returned_at_10" in mode_report["non_relevant_diagnostics"]
        assert "top_score" in mode_report["non_relevant_diagnostics"]
        assert mode_report["score_separation"]["diagnostic_only"] is True
        assert mode_report["score_separation"]["no_calibrated_threshold_claim"] is True
        assert mode_report["score_separation"]["no_threshold_claim"] is True
    hybrid_orientation = mode_results["hybrid"]["orientation_diagnostics"]
    assert hybrid_orientation["orientation_seeded_count"] == 0
    assert (
        hybrid_orientation["plain_rrf_fallback_count"]
        == hybrid_orientation["observed_search_count"]
    )

    report_text = json.dumps(result.report, ensure_ascii=False, sort_keys=True)
    assert "알파 치료 문서" not in report_text
    assert "베타 치료 질문" not in report_text
    assert str(tmp_path) not in report_text
    runner.validate_public_report(result.report)
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8")) == result.report


def test_nomiracl_ko_public_report_rejects_missing_limitations(tmp_path: Path) -> None:
    materialized = nomiracl_ko.materialize_nomiracl_ko(
        make_nomiracl_source_fixture(tmp_path / "source"),
        tmp_path / "materialized",
        non_relevant_sample_size=2,
        enforce_official_canonical_invariants=False,
    )
    result = runner.run_nomiracl_ko_benchmark(
        wiki_dir=materialized.wiki_dir,
        bundle_dir=materialized.bundle_dir,
        output_report=tmp_path / "report.json",
        implementation_revision=FAKE_IMPLEMENTATION_REVISION,
        search_modes=("lexical",),
        relevant_query_limit=2,
        non_relevant_query_limit=2,
    )
    tampered = cast(dict[str, Any], json.loads(json.dumps(result.report)))
    tampered["limitations"] = ["too vague"]

    with pytest.raises(runner.NomiraclKoRunnerError, match="limitations"):
        runner.validate_public_report(tampered)


def test_nomiracl_ko_public_report_rejects_calibrated_threshold_claim(
    tmp_path: Path,
) -> None:
    materialized = nomiracl_ko.materialize_nomiracl_ko(
        make_nomiracl_source_fixture(tmp_path / "source"),
        tmp_path / "materialized",
        non_relevant_sample_size=2,
        enforce_official_canonical_invariants=False,
    )
    result = runner.run_nomiracl_ko_benchmark(
        wiki_dir=materialized.wiki_dir,
        bundle_dir=materialized.bundle_dir,
        output_report=tmp_path / "report.json",
        implementation_revision=FAKE_IMPLEMENTATION_REVISION,
        search_modes=("lexical",),
        relevant_query_limit=2,
        non_relevant_query_limit=2,
    )

    threshold_report = cast(dict[str, Any], json.loads(json.dumps(result.report)))
    threshold_report["abstention_policy"]["calibrated_threshold"] = 0.42
    with pytest.raises(runner.NomiraclKoRunnerError, match="calibrated threshold"):
        runner.validate_public_report(threshold_report)

    mode_claim_report = cast(dict[str, Any], json.loads(json.dumps(result.report)))
    mode_claim_report["mode_results"]["lexical"]["calibrated_threshold_claim"] = "score >= 0.42"
    with pytest.raises(runner.NomiraclKoRunnerError, match="calibrated threshold"):
        runner.validate_public_report(mode_claim_report)

    text_claim_report = cast(dict[str, Any], json.loads(json.dumps(result.report)))
    text_claim_report["limitations"].append("calibrated threshold: score >= 0.42")
    with pytest.raises(runner.NomiraclKoRunnerError, match="calibrated threshold"):
        runner.validate_public_report(text_claim_report)


def test_nomiracl_ko_bundle_rejects_judged_pool_and_full_corpus_label_drift(
    tmp_path: Path,
) -> None:
    materialized = nomiracl_ko.materialize_nomiracl_ko(
        make_nomiracl_source_fixture(tmp_path / "source"),
        tmp_path / "materialized",
        non_relevant_sample_size=2,
        enforce_official_canonical_invariants=False,
    )
    provenance_path = materialized.bundle_dir / "provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["evaluation_pool"]["full_corpus"] = True
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(runner.NomiraclKoRunnerError, match="full_corpus"):
        runner.load_nomiracl_ko_bundle(materialized.bundle_dir)


def test_nomiracl_ko_bundle_rejects_unanswerable_label_drift(tmp_path: Path) -> None:
    materialized = nomiracl_ko.materialize_nomiracl_ko(
        make_nomiracl_source_fixture(tmp_path / "source"),
        tmp_path / "materialized",
        non_relevant_sample_size=2,
        enforce_official_canonical_invariants=False,
    )
    queries_path = materialized.bundle_dir / "queries.jsonl"
    rows = [
        json.loads(line)
        for line in queries_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for row in rows:
        if row["answerability"] == "unanswerable":
            row["source_split"] = "dev.relevant"
            break
    queries_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    provenance_path = materialized.bundle_dir / "provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["checksums"]["queries.jsonl"] = "sha256:" + validator.canonical_text_file_sha256(
        queries_path
    )
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(runner.NomiraclKoRunnerError, match="dev.non_relevant"):
        runner.load_nomiracl_ko_bundle(materialized.bundle_dir)


class FakeProvider:
    provider_id = "fake"
    model_id = "fake-model"
    model_revision = "fake-revision"
    dimension = 3
    distance_metric = "cosine"

    def __init__(self, telemetry: EmbeddingTelemetry) -> None:
        self._telemetry = telemetry
        self.document_calls = 0
        self.query_calls = 0

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.document_calls += 1
        self._telemetry.document_calls += 1
        self._telemetry.document_texts += len(texts)
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        self._telemetry.query_calls += 1
        return self._vector(text)

    def safe_metadata(self) -> dict[str, str | int]:
        return {
            "dimension": self.dimension,
            "distance_metric": self.distance_metric,
            "fastembed_version": "fake",
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "numpy_version": "fake",
            "provider_id": self.provider_id,
        }

    def _vector(self, text: str) -> list[float]:
        if "알파" in text:
            return [1.0, 0.0, 0.0]
        if "베타" in text:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]


def make_nomiracl_source_fixture(
    root: Path,
    *,
    payloads: Mapping[str, bytes] | None = None,
) -> Path:
    payloads = fixture_payloads() if payloads is None else dict(payloads)
    for relative_path, payload in payloads.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return root


def fixture_payloads() -> dict[str, bytes]:
    corpus_rows = [
        {"docid": "doc-alpha", "text": "알파 치료 문서 본문", "title": "알파 치료"},
        {"docid": "doc-alpha", "text": "알파 치료 문서 본문", "title": "알파 치료"},
        {"docid": "doc-beta", "text": "베타 치료 문서 본문", "title": "베타 치료"},
        {"docid": "doc-gamma", "text": "감마 배경 문서", "title": "감마 배경"},
        {"docid": "doc-delta", "text": "델타 참고 문서", "title": "델타 참고"},
        {"docid": "doc-train-only", "text": "학습 전용 문서", "title": "학습 전용"},
        {"docid": "doc-test-only", "text": "테스트 전용 문서", "title": "테스트 전용"},
    ]
    return {
        "data/korean/corpus.jsonl.gz": gzip_payload(corpus_rows),
        "data/korean/topics/dev.relevant.tsv": (
            "q-alpha\t알파 치료 질문\nq-beta\t베타 치료 질문\n"
        ).encode(),
        "data/korean/qrels/dev.relevant.tsv": (
            b"q-alpha\tQ0\tdoc-alpha\t1\nq-alpha\tQ0\tdoc-gamma\t0\nq-beta\tQ0\tdoc-beta\t1\n"
        ),
        "data/korean/topics/dev.non_relevant.tsv": (
            "q-negative-1\t관련 없는 질문 하나\n"
            "q-negative-2\t관련 없는 질문 둘\n"
            "q-negative-3\t관련 없는 질문 셋\n"
        ).encode(),
        "data/korean/qrels/dev.non_relevant.tsv": (
            b"q-negative-1\tQ0\tdoc-gamma\t0\n"
            b"q-negative-1\tQ0\tdoc-delta\t0\n"
            b"q-negative-2\tQ0\tdoc-gamma\t0\n"
            b"q-negative-2\tQ0\tdoc-delta\t0\n"
            b"q-negative-3\tQ0\tdoc-gamma\t0\n"
            b"q-negative-3\tQ0\tdoc-delta\t0\n"
        ),
    }


def gzip_payload(rows: Sequence[Mapping[str, object]]) -> bytes:
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as handle:
        handle.write(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
            ).encode("utf-8")
        )
    return buffer.getvalue()


def expected_files_for_payloads(
    payloads: Mapping[str, bytes],
) -> tuple[nomiracl_ko.OfficialSourceFile, ...]:
    return tuple(
        nomiracl_ko.OfficialSourceFile(
            relative_path=relative_path,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )
        for relative_path, payload in sorted(payloads.items())
    )


def source_opener_for(payloads: Mapping[str, bytes]) -> nomiracl_ko.SourceOpener:
    expected_files = expected_files_for_payloads(payloads)
    tree = [
        {
            "lfs": {"oid": item.sha256} if item.relative_path.endswith(".gz") else None,
            "path": item.relative_path,
            "size": item.size_bytes,
            "type": "file",
        }
        for item in expected_files
    ]
    metadata = {
        "cardData": {"license": ["apache-2.0"]},
        "id": nomiracl_ko.HF_DATASET_REPO,
        "sha": nomiracl_ko.HF_REVISION,
        "tags": ["license:apache-2.0"],
    }

    def opener(url: str) -> io.BytesIO:
        if url == nomiracl_ko.HF_REVISION_API_URL:
            return io.BytesIO(json.dumps(metadata).encode("utf-8"))
        if url == nomiracl_ko.HF_TREE_API_URL:
            return io.BytesIO(json.dumps(tree).encode("utf-8"))
        for relative_path, payload in payloads.items():
            if url.endswith(relative_path):
                return io.BytesIO(payload)
        raise AssertionError(f"unexpected URL: {url}")

    return opener
