from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from scripts import verified_source_benchmark as bench

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "verified_source_benchmarks" / "tiny"
TOKENIZER = bench.TokenizerProvenance(
    tokenizer_id="Qwen/Qwen3-fixture-tokenizer",
    tokenizer_revision="fixture-rev-1",
)


def test_tiny_fixture_cli_builds_public_safe_report(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "verified_source_benchmark.py"),
            "--input-dir",
            str(FIXTURE),
            "--output",
            str(output),
            "--hardware-bucket",
            "windows-local",
            "--tokenizer-id",
            TOKENIZER.tokenizer_id,
            "--tokenizer-revision",
            TOKENIZER.tokenizer_revision,
            "--baseline-run-id",
            "raw_full",
            "--bootstrap-samples",
            "50",
            "--seed",
            "123",
            "--source-root",
            str(FIXTURE / "source"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))

    bench.validate_report(report)
    assert report["schema"] == bench.SCHEMA
    assert report["evidence_track"] == "quality-benchmark"
    assert report["hardware_bucket"] == "windows-local"
    assert report["tokenizer"]["policy"] == "qwen-tokenizer-required-no-byte-proxy"
    assert report["tokenizer"]["evidence_level"] == "declared-qwen-provenance"
    assert report["tokenizer"]["verified_by_harness"] is False
    assert report["hard_failures"] == []
    assert report["source_mutation"]["mutated"] is False
    assert set(report["input_artifacts"]) == {
        "corpus.jsonl",
        "queries.jsonl",
        "qrels.jsonl",
        "runs.jsonl",
    }
    assert report["quality_gates"]["overall_status"] == "fail"
    assert report["quality_gates"]["public_quality_claim"] is False
    assert any(
        "tokenizer evidence is declared provenance only" in failure
        for failure in report["quality_gates"]["common_failures"]
    )
    assert any(
        "recall_at_5" in failure
        for failure in report["quality_gates"]["runs"]["serve_query"]["failures"]
    )
    assert "raw_full->serve_query" in report["deltas"]

    serve_metrics = report["metrics"]["serve_query"]
    assert serve_metrics["query_count"] == 5
    assert serve_metrics["judged_query_count"] == 4
    assert serve_metrics["recall_at_5"] == pytest.approx(0.875)
    assert serve_metrics["hit_at_5"] == 1.0
    assert serve_metrics["mrr"] == 1.0
    assert serve_metrics["ndcg_at_10"] == pytest.approx(0.946789, abs=0.000001)
    assert serve_metrics["citation_precision"] == 1.0
    assert serve_metrics["citation_recall"] == 0.8
    assert serve_metrics["negative_false_positive_rate"] == 0.0
    assert serve_metrics["context_tokens"]["p95"] == 34.0
    assert serve_metrics["payload_tokens"]["p50"] == 68.0
    assert serve_metrics["evaluation_mode"] == "retrieval"
    assert set(serve_metrics["query_classes"]) == {
        "citation",
        "known-item",
        "korean-numeric",
        "multi-hop",
        "negative",
    }
    assert serve_metrics["query_classes"]["known-item"]["mrr"] == 1.0
    assert serve_metrics["query_classes"]["multi-hop"]["recall_at_5"] == 0.5
    assert serve_metrics["query_classes"]["negative"]["negative_false_positive_rate"] == 0.0
    assert serve_metrics["query_classes"]["citation"]["payload_tokens"]["p50"] == 66.0


def test_global_map_query_class_is_reported_separately(tmp_path: Path) -> None:
    copied = copy_fixture(tmp_path)
    queries_path = copied / "queries.jsonl"
    queries = read_jsonl(queries_path)
    for query in queries:
        if query["query_id"] == "q-known-release":
            query["class"] = "global-map"
            break
    else:  # pragma: no cover - guarded by fixture constants.
        raise AssertionError("fixture query not found")
    write_jsonl(queries_path, queries)

    report = bench.run_benchmark(
        copied,
        hardware_bucket="windows-local",
        tokenizer=TOKENIZER,
        seed=123,
        bootstrap_samples=5,
        baseline_run_id="raw_full",
        source_root=copied / "source",
    )

    query_classes = report["metrics"]["serve_query"]["query_classes"]
    assert "global-map" in query_classes
    assert "topical" not in query_classes
    assert query_classes["global-map"]["query_class"] == "global-map"
    assert query_classes["global-map"]["query_count"] == 1
    bench.validate_report(report)


def test_checked_in_global_map_queries_use_canonical_query_class() -> None:
    cases_root = ROOT / "benchmarks" / "verified_sources" / "cases"
    query_files = sorted(cases_root.glob("*/queries*.jsonl"))
    assert query_files
    observed_global_queries: set[str] = set()

    for query_path in query_files:
        for query in read_jsonl(query_path):
            query_id = str(query["query_id"])
            if "-global-" in query_id:
                assert query["class"] == "global-map", query_path
                observed_global_queries.add(query_id)

    for manifest_path in sorted(cases_root.glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        coverage_counts = manifest.get("query_coverage_counts", {})
        class_counts = manifest.get("benchmark_schema_query_class_counts", {})
        if isinstance(coverage_counts, dict) and "global" in coverage_counts:
            assert class_counts.get("global-map") == coverage_counts["global"]

    assert {
        "q-dendron-global-alternatives",
        "q-logseq-global-graph-fixture",
        "q-openwiki-global-map",
        "q-pratiyush-global-map",
    } <= observed_global_queries


def test_checked_in_case_artifacts_use_lf_line_endings() -> None:
    cases_root = ROOT / "benchmarks" / "verified_sources" / "cases"
    artifact_files = sorted(
        path
        for path in cases_root.glob("*/*")
        if path.is_file() and path.suffix in {".json", ".jsonl", ".md"}
    )
    assert artifact_files

    offenders = [
        path.relative_to(ROOT).as_posix() for path in artifact_files if b"\r" in path.read_bytes()
    ]
    assert offenders == []


def test_checked_in_manifest_artifact_digests_match_files() -> None:
    cases_root = ROOT / "benchmarks" / "verified_sources" / "cases"
    observed: set[str] = set()

    for manifest_path in sorted(cases_root.glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        artifacts = manifest.get("benchmark_artifacts")
        if not isinstance(artifacts, dict):
            continue
        observed.add(manifest_path.parent.name)
        for artifact in artifacts.values():
            assert isinstance(artifact, dict)
            artifact_path = manifest_path.parent / str(artifact["path"])
            assert artifact_path.is_file()
            records = [
                line
                for line in artifact_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            assert artifact["record_count"] == len(records)
            assert artifact["sha256"] == bench.canonical_text_file_sha256(artifact_path)

    assert {"openwiki", "pratiyush"} <= observed


def test_openwiki_qrels_exclude_audit_listed_weak_judgments() -> None:
    case_root = ROOT / "benchmarks" / "verified_sources" / "cases" / "openwiki"
    native_qrels = read_jsonl(case_root / "qrels.jsonl")
    shadow_qrels = read_jsonl(case_root / "qrels-shadow.jsonl")
    native_pairs = {(str(row["query_id"]), str(row["doc_id"])) for row in native_qrels}
    shadow_pairs = {(str(row["query_id"]), str(row["doc_id"])) for row in shadow_qrels}

    removed_pairs = {
        ("q-openwiki-numeric-backend-limits", "agent/workflow"),
        ("q-openwiki-numeric-five-templates", "integrations/connectors"),
        ("q-openwiki-multihop-local-brain-profile", "integrations/connectors"),
        ("q-openwiki-korean-scheduled-workflows", "cli/usage"),
    }
    assert removed_pairs.isdisjoint(native_pairs)
    assert removed_pairs.isdisjoint(shadow_pairs)
    assert {
        ("q-openwiki-native-root-hubs", "agent/index"),
        ("q-openwiki-native-root-hubs", "architecture/index"),
        ("q-openwiki-native-root-hubs", "cli/index"),
        ("q-openwiki-native-root-hubs", "integrations/index"),
        ("q-openwiki-native-root-hubs", "operations/index"),
    }.isdisjoint(native_pairs)

    shadow_global_grades = {
        str(row["doc_id"]): row["relevance"]
        for row in shadow_qrels
        if row["query_id"] == "q-openwiki-global-map"
    }
    assert shadow_global_grades["architecture/overview"] == 2
    assert shadow_global_grades["cli/usage"] == 2

    for qrels, minimum in ((native_qrels, 50), (shadow_qrels, 50)):
        judged_query_ids = {str(row["query_id"]) for row in qrels}
        assert len(judged_query_ids) >= minimum


def test_pratiyush_qrels_apply_independent_audit_findings() -> None:
    case_root = ROOT / "benchmarks" / "verified_sources" / "cases" / "pratiyush"
    native_qrels = read_jsonl(case_root / "qrels.jsonl")
    shadow_qrels = read_jsonl(case_root / "qrels-shadow.jsonl")

    def relevance(rows: list[dict[str, object]], query_id: str, doc_id: str) -> int | None:
        for row in rows:
            if row["query_id"] == query_id and row["doc_id"] == doc_id:
                return int(row["relevance"])
        return None

    for qrels in (native_qrels, shadow_qrels):
        assert relevance(qrels, "q-pratiyush-local-gpt5-context", "entities/OpenAI") == 1
        assert (
            relevance(qrels, "q-pratiyush-known-claude-context-window", "entities/Anthropic") == 1
        )

    native_pairs = {(str(row["query_id"]), str(row["doc_id"])) for row in native_qrels}
    assert ("q-pratiyush-citation-critical-facts", "sources/_context") not in native_pairs
    assert ("q-pratiyush-native-index-counts", "overview") not in native_pairs

    for qrels, minimum in ((native_qrels, 50), (shadow_qrels, 50)):
        judged_query_ids = {str(row["query_id"]) for row in qrels}
        assert len(judged_query_ids) >= minimum


def test_metric_computation_is_hand_checked() -> None:
    data = bench.load_benchmark_data(FIXTURE, TOKENIZER)
    metrics, query_maps = bench.compute_run_metrics(
        "serve_query",
        data.runs_by_id["serve_query"],
        corpus=data.corpus,
        queries=data.queries,
        qrels_by_query=data.qrels_by_query,
    )

    assert metrics["recall_at_5"] == pytest.approx(0.875)
    assert metrics["hit_at_5"] == 1.0
    assert metrics["mrr"] == 1.0
    assert metrics["ndcg_at_10"] == pytest.approx(0.946789, abs=0.000001)
    assert metrics["payload_bytes"]["p50"] == 780.0
    assert metrics["payload_tokens"]["p50"] == 68.0
    assert query_maps["recall_at_5"]["q-multihop-onboarding-release"] == 0.5
    assert query_maps["hit_at_5"]["q-multihop-onboarding-release"] == 1.0
    assert query_maps["context_tokens"]["q-negative-private-token"] == 0.0
    assert query_maps["payload_tokens"]["q-negative-private-token"] == 0.0
    assert query_maps["latency_ms"]["q-multihop-onboarding-release"] == 8.5
    assert metrics["query_classes"]["known-item"]["query_count"] == 1
    assert metrics["query_classes"]["korean-numeric"]["payload_tokens"]["p50"] == 68.0


def test_top_k_metrics_use_explicit_rank_values() -> None:
    data = bench.load_benchmark_data(FIXTURE, TOKENIZER)
    row_at_rank_six = replace(data.runs_by_id["raw_full"][0], rank=6)
    qrels = data.qrels_by_query["q-known-release"]
    relevant = {"release": 3}

    assert bench.recall_at_k([row_at_rank_six], relevant, k=5) == 0.0
    assert bench.hit_at_k([row_at_rank_six], relevant, k=5) == 0.0
    assert bench.ndcg_at_k([row_at_rank_six], qrels, k=5) == 0.0
    assert bench.reciprocal_rank([row_at_rank_six], relevant) == pytest.approx(1 / 6)

    higher_rank_relevant = replace(data.runs_by_id["raw_full"][1], rank=6)
    lower_rank_relevant = data.runs_by_id["raw_full"][2]
    multihop_relevant = {"release": 2, "onboarding": 3}

    assert bench.reciprocal_rank(
        [higher_rank_relevant, lower_rank_relevant], multihop_relevant
    ) == pytest.approx(1 / 2)


def test_run_schema_rejects_noncontiguous_ranks(tmp_path: Path) -> None:
    copied = copy_fixture(tmp_path)
    run_path = copied / "runs.jsonl"
    records = [json.loads(line) for line in run_path.read_text(encoding="utf-8").splitlines()]
    for record in records:
        if (
            record["run_id"] == "raw_full"
            and record["query_id"] == "q-multihop-onboarding-release"
            and record["rank"] == 2
        ):
            record["rank"] = 3
            break
    write_jsonl(run_path, records)

    with pytest.raises(bench.BenchmarkValidationError, match="contiguous from 1"):
        bench.load_benchmark_data(copied, TOKENIZER)


def test_run_schema_rejects_mixed_query_level_payload_latency(tmp_path: Path) -> None:
    copied = copy_fixture(tmp_path)
    run_path = copied / "runs.jsonl"
    records = [json.loads(line) for line in run_path.read_text(encoding="utf-8").splitlines()]
    for record in records:
        if (
            record["run_id"] == "raw_full"
            and record["query_id"] == "q-multihop-onboarding-release"
            and record["rank"] == 2
        ):
            record["payload_bytes"] = 4801
            break
    write_jsonl(run_path, records)

    with pytest.raises(bench.BenchmarkValidationError, match="payload_bytes must be query-level"):
        bench.load_benchmark_data(copied, TOKENIZER)


def test_run_schema_rejects_mixed_query_level_payload_tokens(tmp_path: Path) -> None:
    copied = copy_fixture(tmp_path)
    run_path = copied / "runs.jsonl"
    records = [json.loads(line) for line in run_path.read_text(encoding="utf-8").splitlines()]
    for record in records:
        if (
            record["run_id"] == "raw_full"
            and record["query_id"] == "q-multihop-onboarding-release"
            and record["rank"] == 2
        ):
            record["payload_tokens"] = int(record["payload_tokens"]) + 1
            break
    write_jsonl(run_path, records)

    with pytest.raises(bench.BenchmarkValidationError, match="payload_tokens must be query-level"):
        bench.load_benchmark_data(copied, TOKENIZER)


def test_run_schema_requires_source_bytes_only_for_raw_surfaces(tmp_path: Path) -> None:
    copied = copy_fixture(tmp_path)
    run_path = copied / "runs.jsonl"
    records = [json.loads(line) for line in run_path.read_text(encoding="utf-8").splitlines()]
    records[0]["source_bytes_scanned"] = None
    records[-1]["source_bytes_scanned"] = 12
    write_jsonl(run_path, records)

    with pytest.raises(bench.BenchmarkValidationError, match="source_bytes_scanned"):
        bench.load_benchmark_data(copied, TOKENIZER)


def test_run_schema_accepts_context_orientation_and_bundle_with_distinct_meanings(
    tmp_path: Path,
) -> None:
    copied = copy_fixture(tmp_path)
    run_path = copied / "runs.jsonl"
    records = [json.loads(line) for line in run_path.read_text(encoding="utf-8").splitlines()]
    served_records = [record for record in records if record["run_id"] == "serve_query"]
    records.extend(
        {**record, "run_id": "serve_context_evidence", "surface": "service-context"}
        for record in served_records
    )
    queries = [
        json.loads(line)
        for line in (copied / "queries.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    orientation_rows = []
    bundle_rows = []
    for index, query in enumerate(queries, start=1):
        orientation_rows.append(
            {
                "run_id": "serve_context_orientation",
                "query_id": query["query_id"],
                "rank": 1,
                "doc_id": "index",
                "score": 1.0,
                "citation_ids": ["SRC-INDEX"],
                "context_tokens": 6,
                "payload_tokens": 40 + index,
                "payload_bytes": 500 + index,
                "latency_ms": 3.0,
                "surface": "service-context-orientation",
                "tokenizer_id": TOKENIZER.tokenizer_id,
                "tokenizer_revision": TOKENIZER.tokenizer_revision,
                "source_bytes_scanned": None,
            }
        )
        bundle_rows.append(
            {
                "run_id": "serve_context_bundle",
                "query_id": query["query_id"],
                "rank": 1,
                "doc_id": "index",
                "score": 1.0,
                "citation_ids": ["SRC-INDEX"],
                "context_tokens": 6,
                "payload_tokens": 100 + index,
                "payload_bytes": 800 + index,
                "latency_ms": 4.0,
                "surface": "service-context-bundle",
                "tokenizer_id": TOKENIZER.tokenizer_id,
                "tokenizer_revision": TOKENIZER.tokenizer_revision,
                "source_bytes_scanned": None,
            }
        )
    records.extend(orientation_rows)
    records.extend(bundle_rows)
    write_jsonl(run_path, records)

    data = bench.load_benchmark_data(copied, TOKENIZER)
    assert {row.surface for row in data.runs_by_id["serve_context_evidence"]} == {"service-context"}
    assert {row.surface for row in data.runs_by_id["serve_context_orientation"]} == {
        "service-context-orientation"
    }
    assert {row.surface for row in data.runs_by_id["serve_context_bundle"]} == {
        "service-context-bundle"
    }

    report = bench.run_benchmark(
        copied,
        hardware_bucket="windows-local",
        tokenizer=TOKENIZER,
        seed=123,
        bootstrap_samples=5,
        baseline_run_id="raw_full",
        source_root=copied / "source",
    )
    assert "serve_context_bundle" in report["inputs"]["run_ids"]
    assert report["metrics"]["serve_context_evidence"]["evaluation_mode"] == "retrieval"
    assert report["metrics"]["serve_context_orientation"]["evaluation_mode"] == "retrieval"
    assert report["metrics"]["serve_context_orientation"]["recall_at_5"] == 0.0
    assert report["metrics"]["serve_context_bundle"]["evaluation_mode"] == "telemetry-only"
    assert report["metrics"]["serve_context_bundle"]["run_id"] == "serve_context_bundle"
    assert set(report["metrics"]["serve_context_bundle"]) == {
        "run_id",
        "surface",
        "evaluation_mode",
        "query_count",
        "run_row_count",
        "payload_tokens",
        "payload_bytes",
        "latency_ms",
        "query_classes",
    }
    assert "mrr" not in report["metrics"]["serve_context_bundle"]
    assert (
        report["metrics"]["serve_context_bundle"]["query_classes"]["negative"]["payload_tokens"][
            "count"
        ]
        == 1
    )
    assert "mrr" not in report["metrics"]["serve_context_bundle"]["query_classes"]["known-item"]
    bundle_gate = report["quality_gates"]["runs"]["serve_context_bundle"]
    assert bundle_gate["gate_scope"] == "telemetry-only"
    assert bundle_gate["failures"] == []
    assert set(report["deltas"]["raw_full->serve_context_bundle"]) == {
        "latency_ms",
        "payload_bytes",
        "payload_tokens",
    }


def test_citation_precision_counts_existing_but_unsupported_citations(tmp_path: Path) -> None:
    copied = copy_fixture(tmp_path)
    run_path = copied / "runs.jsonl"
    records = [json.loads(line) for line in run_path.read_text(encoding="utf-8").splitlines()]
    records.append(
        {
            "run_id": "serve_query",
            "query_id": "q-negative-private-token",
            "rank": 1,
            "doc_id": "release",
            "score": 1.0,
            "citation_ids": ["SRC-REL"],
            "context_tokens": 10,
            "payload_tokens": 20,
            "payload_bytes": 100,
            "latency_ms": 1.0,
            "surface": "http-query",
            "tokenizer_id": TOKENIZER.tokenizer_id,
            "tokenizer_revision": TOKENIZER.tokenizer_revision,
            "source_bytes_scanned": None,
        }
    )
    write_jsonl(run_path, records)
    data = bench.load_benchmark_data(copied, TOKENIZER)
    metrics, _query_maps = bench.compute_run_metrics(
        "serve_query",
        data.runs_by_id["serve_query"],
        corpus=data.corpus,
        queries=data.queries,
        qrels_by_query=data.qrels_by_query,
    )

    assert metrics["citation_precision"] == pytest.approx(4 / 5)
    assert metrics["negative_false_positive_rate"] == 1.0


def test_paired_bootstrap_is_seeded_and_requires_matching_query_ids() -> None:
    baseline = {"q1": 1.0, "q2": 0.0, "q3": 1.0}
    candidate = {"q1": 1.0, "q2": 1.0, "q3": 1.0}

    first = bench.paired_bootstrap_delta_ci(baseline, candidate, seed=7, samples=25)
    second = bench.paired_bootstrap_delta_ci(baseline, candidate, seed=7, samples=25)

    assert first == second
    assert first["mean_delta"] == pytest.approx(1 / 3)
    assert first["sample_count"] == 25
    with pytest.raises(bench.BenchmarkValidationError, match="identical query ids"):
        bench.paired_bootstrap_delta_ci(baseline, {"q1": 1.0}, seed=7, samples=25)


def test_schema_validation_rejects_missing_tokenizer_provenance(tmp_path: Path) -> None:
    copied = copy_fixture(tmp_path)
    run_path = copied / "runs.jsonl"
    records = [json.loads(line) for line in run_path.read_text(encoding="utf-8").splitlines()]
    records[0].pop("tokenizer_id")
    write_jsonl(run_path, records)

    with pytest.raises(bench.BenchmarkValidationError, match="missing required fields"):
        bench.load_benchmark_data(copied, TOKENIZER)


def test_schema_validation_rejects_missing_payload_tokens(tmp_path: Path) -> None:
    copied = copy_fixture(tmp_path)
    run_path = copied / "runs.jsonl"
    records = [json.loads(line) for line in run_path.read_text(encoding="utf-8").splitlines()]
    records[0].pop("payload_tokens")
    write_jsonl(run_path, records)

    with pytest.raises(bench.BenchmarkValidationError, match="missing required fields"):
        bench.load_benchmark_data(copied, TOKENIZER)


def test_schema_validation_rejects_byte_proxy_tokenizer(tmp_path: Path) -> None:
    copied = copy_fixture(tmp_path)
    run_path = copied / "runs.jsonl"
    records = [json.loads(line) for line in run_path.read_text(encoding="utf-8").splitlines()]
    records[0]["tokenizer_id"] = "Qwen/byte/4-heuristic"
    records[0]["tokenizer_revision"] = "byte_div_4"
    write_jsonl(run_path, records)

    with pytest.raises(bench.BenchmarkValidationError, match="byte/4"):
        bench.load_benchmark_data(copied, TOKENIZER)


def test_schema_validation_rejects_unknown_nested_support_span_field(tmp_path: Path) -> None:
    copied = copy_fixture(tmp_path)
    qrels_path = copied / "qrels.jsonl"
    records = [json.loads(line) for line in qrels_path.read_text(encoding="utf-8").splitlines()]
    records[0]["support_spans"][0]["raw_text"] = "not public report schema"
    write_jsonl(qrels_path, records)

    with pytest.raises(bench.BenchmarkValidationError, match="unknown fields"):
        bench.load_benchmark_data(copied, TOKENIZER)


def test_source_root_validation_rejects_stale_corpus_hash(tmp_path: Path) -> None:
    copied = copy_fixture(tmp_path)
    corpus_path = copied / "corpus.jsonl"
    records = [json.loads(line) for line in corpus_path.read_text(encoding="utf-8").splitlines()]
    records[0]["sha256"] = "0" * 64
    write_jsonl(corpus_path, records)

    with pytest.raises(bench.BenchmarkValidationError, match="sha256 does not match"):
        bench.run_benchmark(
            copied,
            hardware_bucket="windows-local",
            tokenizer=TOKENIZER,
            seed=123,
            bootstrap_samples=5,
            baseline_run_id="raw_full",
            source_root=copied / "source",
        )


def test_canonical_markdown_hash_normalizes_bom_and_newlines(tmp_path: Path) -> None:
    lf_content = b"# Canonical\n\nSame content.\n"
    crlf_with_bom = b"\xef\xbb\xbf# Canonical\r\n\r\nSame content.\r\n"

    assert bench.canonical_markdown_sha256(lf_content) == bench.canonical_markdown_sha256(
        crlf_with_bom
    )
    assert bench.canonical_text_sha256(lf_content) == bench.canonical_markdown_sha256(crlf_with_bom)

    source = tmp_path / "source"
    source.mkdir()
    source_file = source / "canonical.md"
    source_file.write_bytes(crlf_with_bom)
    row = bench.CorpusRow(
        doc_id="canonical",
        path="canonical.md",
        title="Canonical",
        adapter="generic-markdown",
        role="page",
        approved=True,
        sha256=bench.canonical_markdown_sha256(lf_content),
        source_ref_ids=("SRC-CANONICAL",),
        license="synthetic-fixture",
        public_source="https://example.invalid/llmwiki-serve/canonical",
    )

    bench.validate_corpus_source_hashes({"canonical": row}, source)


def test_input_artifact_digests_use_canonical_text_newlines(tmp_path: Path) -> None:
    lf_content = b'{"record":1}\n{"record":2}\n'
    crlf_content = b"\xef\xbb\xbf" + lf_content.replace(b"\n", b"\r\n")
    raw_crlf_hash = hashlib.sha256(crlf_content).hexdigest()

    for file_name in bench.BENCHMARK_INPUT_FILES:
        (tmp_path / file_name).write_bytes(crlf_content)

    digests = bench.compute_input_artifact_digests(tmp_path)
    assert set(digests) == set(bench.BENCHMARK_INPUT_FILES)
    for digest in digests.values():
        assert digest == bench.canonical_text_sha256(lf_content)
        assert digest != raw_crlf_hash


def test_canonical_markdown_hash_rejects_actual_content_changes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    source_file = source / "canonical.md"
    source_file.write_text("# Canonical\n\nOriginal content.\n", encoding="utf-8")
    row = bench.CorpusRow(
        doc_id="canonical",
        path="canonical.md",
        title="Canonical",
        adapter="generic-markdown",
        role="page",
        approved=True,
        sha256=bench.canonical_markdown_file_sha256(source_file),
        source_ref_ids=("SRC-CANONICAL",),
        license="synthetic-fixture",
        public_source="https://example.invalid/llmwiki-serve/canonical",
    )

    source_file.write_text("# Canonical\n\nChanged content.\n", encoding="utf-8")

    with pytest.raises(bench.BenchmarkValidationError, match="canonical Markdown"):
        bench.validate_corpus_source_hashes({"canonical": row}, source)


def test_openwiki_corpus_hashes_match_canonical_git_blob_bytes() -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    commit = "9c253af17f264ac2589ab6781e79e9bb5b5d1238"
    checkout = (
        ROOT
        / ".llmwiki-work"
        / "verified-source-checkouts"
        / "langchain-openwiki-self-docs"
        / commit
    )
    if not checkout.is_dir():
        pytest.skip("OpenWiki pinned checkout is not available")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout,
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()
    assert head == commit

    corpus = read_jsonl(
        ROOT / "benchmarks" / "verified_sources" / "cases" / "openwiki" / "corpus.jsonl"
    )
    for row in corpus:
        result = subprocess.run(
            ["git", "show", f"HEAD:openwiki/{row['path']}"],
            cwd=checkout,
            capture_output=True,
            check=True,
        )
        assert row["sha256"] == bench.canonical_markdown_sha256(result.stdout), row["doc_id"]


def test_redaction_hard_fails_private_paths_and_tokens() -> None:
    unsafe = {
        "path": "C:" + "\\Users\\redacted\\private-wiki\\index.md",
        "endpoint": "http://" + "127.0.0.1:8000/query",
        "bare_endpoint": "localhost:8000/query",
        "scratch": ".llmwiki-work/cache",
        "tailnet": "private-host.ts.net:443",
        "env_secret": "OPENAI_API_KEY=sk-proj-redactedSecret123456",
        "authorization": "Bearer " + "headerSecretToken123",
    }

    with pytest.raises(bench.BenchmarkValidationError, match="private or sensitive"):
        bench.assert_public_safe_value(unsafe, "unsafe-report")


def test_source_tree_digest_detects_mutation_and_ignores_git_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "index.md").write_text("# Index\n", encoding="utf-8")
    (source / ".git").mkdir()
    (source / ".git" / "index").write_text("metadata-v1\n", encoding="utf-8")
    before = bench.compute_tree_digest(source)

    (source / ".git" / "index").write_text("metadata-v2\n", encoding="utf-8")
    assert bench.compute_tree_digest(source) == before

    (source / "index.md").write_text("# Index\n\nChanged.\n", encoding="utf-8")
    assert bench.compute_tree_digest(source) != before


def test_report_validation_rejects_unknown_public_bucket() -> None:
    report = build_fixture_report()
    report["hardware_bucket"] = "developer-laptop"

    with pytest.raises(bench.BenchmarkValidationError, match="hardware_bucket"):
        bench.validate_report(report)


def test_report_validation_rejects_unknown_top_level_fields() -> None:
    report = build_fixture_report()
    report["local_debug_path"] = "public-looking-but-unknown"

    with pytest.raises(bench.BenchmarkValidationError, match="unknown fields"):
        bench.validate_report(report)


def test_report_validation_rejects_unknown_query_class_metric_key() -> None:
    report = build_fixture_report()
    known_item_metrics = report["metrics"]["serve_query"]["query_classes"]["known-item"]
    report["metrics"]["serve_query"]["query_classes"]["local"] = {
        **known_item_metrics,
        "query_class": "local",
    }

    with pytest.raises(bench.BenchmarkValidationError, match="unknown query class"):
        bench.validate_report(report)


def test_report_validation_rejects_distribution_count_mismatch() -> None:
    report = build_fixture_report()
    report["metrics"]["serve_query"]["payload_tokens"]["count"] = 999

    with pytest.raises(bench.BenchmarkValidationError, match="count must match"):
        bench.validate_report(report)


def test_cli_rejects_output_inside_source_root(tmp_path: Path) -> None:
    copied = copy_fixture(tmp_path)
    output = copied / "source" / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "verified_source_benchmark.py"),
            "--input-dir",
            str(copied),
            "--output",
            str(output),
            "--hardware-bucket",
            "windows-local",
            "--tokenizer-id",
            TOKENIZER.tokenizer_id,
            "--tokenizer-revision",
            TOKENIZER.tokenizer_revision,
            "--source-root",
            str(copied / "source"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "output must be outside source_root" in result.stderr
    assert not output.exists()


def test_unknown_citation_ids_are_hard_failures(tmp_path: Path) -> None:
    copied = copy_fixture(tmp_path)
    run_path = copied / "runs.jsonl"
    records = [json.loads(line) for line in run_path.read_text(encoding="utf-8").splitlines()]
    records[0]["citation_ids"] = ["SRC-PRIVATE-OUTSIDE"]
    write_jsonl(run_path, records)

    with pytest.raises(bench.BenchmarkValidationError, match="citation outside corpus"):
        bench.load_benchmark_data(copied, TOKENIZER)


def test_cross_doc_citation_ids_are_hard_failures(tmp_path: Path) -> None:
    copied = copy_fixture(tmp_path)
    run_path = copied / "runs.jsonl"
    records = [json.loads(line) for line in run_path.read_text(encoding="utf-8").splitlines()]
    records[0]["citation_ids"] = ["SRC-ONB"]
    write_jsonl(run_path, records)

    with pytest.raises(bench.BenchmarkValidationError, match="citation not attached"):
        bench.load_benchmark_data(copied, TOKENIZER)


def copy_fixture(tmp_path: Path) -> Path:
    copied = tmp_path / "tiny"
    shutil.copytree(FIXTURE, copied)
    return copied


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def build_fixture_report() -> dict[str, object]:
    return bench.run_benchmark(
        FIXTURE,
        hardware_bucket="windows-local",
        tokenizer=TOKENIZER,
        seed=123,
        bootstrap_samples=5,
        baseline_run_id="raw_full",
        source_root=FIXTURE / "source",
    )
