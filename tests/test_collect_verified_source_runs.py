from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from scripts import verified_source_benchmark as bench

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "verified_source_benchmarks" / "collector_tiny"
SCRIPT = ROOT / "scripts" / "collect_verified_source_runs.py"
TOKENIZER_ID = "Qwen/Qwen3-collector-fixture-tokenizer"
TOKENIZER_REVISION = "fixture-rev-collector"
PHASES = ("cold", "warm", "primed")
SURFACES = (
    "service-context",
    "service-context-orientation",
    "service-context-bundle",
    "service-search-read",
)
VARIANTS = ("native", "generic-shadow-managed-off", "generic-shadow-managed-on")
RUN_VARIANTS = (
    "native",
    "native-managed-on",
    "generic-shadow-managed-off",
    "generic-shadow-managed-on",
)
EXPECTED_RUN_IDS = {
    f"{variant}_{phase}_{surface}".replace("-", "_")
    for variant in RUN_VARIANTS
    for phase in PHASES
    for surface in SURFACES
}
CASE_METADATA_FIELDS = (
    "case_id",
    "product",
    "official_link",
    "source_kind",
    "evidence_label",
    "pinned_commit",
    "license",
)


def test_collector_tiny_fixture_declares_explicit_inputs_and_variants() -> None:
    case_manifest = read_json(FIXTURE / "case_manifest.json")
    corpus = read_jsonl(FIXTURE / "corpus.jsonl")
    queries = read_jsonl(FIXTURE / "queries.jsonl")
    qrels = read_jsonl(FIXTURE / "qrels.jsonl")

    assert case_manifest["schema"] == "llmwiki-serve-verified-source-collector-case-v1"
    assert case_manifest["source_kind"] == "synthetic-fixture"
    assert case_manifest["evidence_label"] == "benchmark-fixture-not-actual-product"
    assert case_manifest["checkout_root"] == "upstream_cache/collector-product"
    assert case_manifest["source_root"] == "upstream_cache/collector-product/wiki"
    assert not Path(str(case_manifest["source_root"])).is_absolute()
    assert case_manifest["inputs"] == {
        "corpus": "corpus.jsonl",
        "queries": "queries.jsonl",
        "qrels": "qrels.jsonl",
    }
    assert case_manifest["surfaces"] == list(SURFACES)
    assert case_manifest["phases"] == list(PHASES)
    assert case_manifest["tokenizer"]["id"] == TOKENIZER_ID
    assert case_manifest["tokenizer"]["revision"] == TOKENIZER_REVISION
    assert case_manifest["tokenizer"]["path"] == "tokenizer_stub"
    assert case_manifest["tokenizer"]["byte_proxy_allowed"] is False

    variants = {variant["variant_id"]: variant for variant in case_manifest["variants"]}
    assert set(variants) == set(RUN_VARIANTS)
    assert variants["native"]["adapter"] == "llmwiki-markdown"
    assert variants["native"]["expected_managed_delta"] == "no-op-non-generic"
    assert variants["native-managed-on"]["adapter"] == "llmwiki-markdown"
    assert variants["native-managed-on"]["managed_context"] is True
    assert variants["native-managed-on"]["expected_managed_delta"] == "no-op-non-generic"
    assert variants["generic-shadow-managed-off"]["adapter"] == "generic-markdown"
    assert variants["generic-shadow-managed-on"]["managed_context"] is True
    assert (
        variants["generic-shadow-managed-on"]["materialization"] == "benchmark-only-generic-shadow"
    )

    query_ids = {query["query_id"] for query in queries}
    assert query_ids == {
        "q-release-explicit-root",
        "q-managed-shadow-materialization",
        "q-korean-phase-contract",
        "q-negative-private-path",
    }
    assert {query["class"] for query in queries} >= {
        "known-item",
        "plain-markdown",
        "korean-numeric",
        "negative",
    }
    assert {qrel["query_id"] for qrel in qrels} == query_ids
    assert {qrel["doc_id"] for qrel in qrels} <= {row["doc_id"] for row in corpus}

    source_root = FIXTURE / str(case_manifest["source_root"])
    assert source_root.is_dir()
    for row in corpus:
        source_path = source_root / str(row["path"])
        assert source_path.is_file()
        assert row["sha256"] == bench.canonical_markdown_file_sha256(source_path)
        assert row["public_source"] == case_manifest["official_link"]


def test_collector_import_and_fixture_tokenizer_do_not_require_transformers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "transformers", None)
    collector = import_collector()

    tokenizer = collector.FixtureTokenizer(TOKENIZER_ID, TOKENIZER_REVISION)
    assert tokenizer.count_tokens("alpha beta\n한국어") == 3
    assert sys.modules["transformers"] is None


def test_service_payload_helpers_collect_context_search_and_read_payloads() -> None:
    collector = import_collector()
    source_root = FIXTURE / "upstream_cache" / "collector-product" / "wiki"
    inputs = collector.load_inputs(
        FIXTURE / "corpus.jsonl",
        FIXTURE / "queries.jsonl",
        FIXTURE / "qrels.jsonl",
        source_root,
    )
    tokenizer = collector.FixtureTokenizer(TOKENIZER_ID, TOKENIZER_REVISION)
    service = collector.LlmWikiService(source_root)
    release_query = inputs.queries["q-release-explicit-root"]
    citation_policy = collector.citation_policy_from_case_manifest(
        collector.load_case_manifest(FIXTURE / "case_manifest.json")
    )

    context_payload, context_results = collector.collect_context_payload(
        service,
        release_query.text,
        limit=3,
    )
    assert context_payload["adapter"] == "llmwiki-markdown"
    assert context_payload["orientation"]
    assert context_payload["evidence"]
    assert context_results[0]["page_id"] == "release"

    orientation_payload, orientation_results = collector.collect_context_orientation_payload(
        service,
        release_query.text,
        limit=3,
    )
    assert orientation_payload["orientation"]
    assert orientation_payload["evidence"]
    assert all(item["route"] == "orientation" for item in orientation_results)

    bundle_payload, bundle_results = collector.collect_context_bundle_payload(
        service,
        release_query.text,
        limit=3,
    )
    assert bundle_payload["orientation"]
    assert bundle_payload["evidence"]
    assert [item["route"] for item in bundle_results[: len(bundle_payload["orientation"])]] == [
        "orientation"
    ] * len(bundle_payload["orientation"])

    search_payload, search_results = collector.collect_search_read_payload(
        service,
        release_query.text,
        limit=3,
    )
    assert search_payload["search"][0]["page_id"] == "release"
    assert search_payload["reads"][0]["id"] == "release"
    assert search_results[0]["read"]["id"] == "release"
    assert search_results[0]["source_refs"] == ["SRC-COLLECTOR-REL"]

    context_rows = collector.timed_collect_query(
        service,
        release_query,
        run_id="native_cold_service_context",
        surface="service-context",
        corpus=inputs.corpus,
        tokenizer=tokenizer,
        limit=3,
        citation_policy=citation_policy,
    ).rows
    orientation_rows = collector.timed_collect_query(
        service,
        release_query,
        run_id="native_cold_service_context_orientation",
        surface="service-context-orientation",
        corpus=inputs.corpus,
        tokenizer=tokenizer,
        limit=3,
        citation_policy=citation_policy,
    ).rows
    bundle_rows = collector.timed_collect_query(
        service,
        release_query,
        run_id="native_cold_service_context_bundle",
        surface="service-context-bundle",
        corpus=inputs.corpus,
        tokenizer=tokenizer,
        limit=3,
        citation_policy=citation_policy,
    ).rows
    search_read_rows = collector.timed_collect_query(
        service,
        release_query,
        run_id="native_cold_service_search_read",
        surface="service-search-read",
        corpus=inputs.corpus,
        tokenizer=tokenizer,
        limit=3,
        citation_policy=citation_policy,
    ).rows

    assert context_rows[0]["doc_id"] == "release"
    assert [row["doc_id"] for row in orientation_rows] == [
        item["page_id"] for item in orientation_results
    ]
    assert all(row["surface"] == "service-context-orientation" for row in orientation_rows)
    assert bundle_rows[0]["doc_id"] == "hot"
    assert [row["doc_id"] for row in bundle_rows[:2]] == ["hot", "index"]
    assert any(row["doc_id"] == "release" for row in bundle_rows)
    assert len({row["doc_id"] for row in bundle_rows}) == len(bundle_rows)
    assert search_read_rows[0]["doc_id"] == "release"
    all_rows = context_rows + orientation_rows + bundle_rows + search_read_rows
    assert {
        row["run_id"] for row in (context_rows[:1] + orientation_rows[:1] + bundle_rows[:1])
    } == {
        "native_cold_service_context",
        "native_cold_service_context_orientation",
        "native_cold_service_context_bundle",
    }
    assert {row["surface"] for row in all_rows} == set(SURFACES)
    assert all(row["source_bytes_scanned"] is None for row in all_rows)
    assert all(row["tokenizer_id"] == TOKENIZER_ID for row in all_rows)
    assert all(row["context_tokens"] > 0 for row in all_rows)
    assert all(row["payload_tokens"] > row["context_tokens"] for row in all_rows)
    assert_query_level_values_are_stable(
        all_rows,
        fields=("payload_tokens", "payload_bytes", "latency_ms", "surface"),
    )
    assert_no_byte_proxy_markers(all_rows)


def test_citation_mode_accepts_only_canonical_deterministic_public_path_id(
    tmp_path: Path,
) -> None:
    collector = import_collector()
    fixture = copy_fixture(tmp_path)

    set_citation_mode(fixture, collector.DETERMINISTIC_PUBLIC_PATH_CITATION_MODE)
    policy = collector.citation_policy_from_case_manifest(
        collector.load_case_manifest(fixture / "case_manifest.json")
    )
    assert policy.mode == "path-derived-deterministic"
    assert policy.declared_citation_mode == collector.DETERMINISTIC_PUBLIC_PATH_CITATION_MODE
    assert policy.deterministic_public_path_ids is True

    set_citation_mode(fixture, "page-id citation fallback")
    with pytest.raises(collector.CollectorError, match="citation_mode must be one of"):
        collector.citation_policy_from_case_manifest(
            collector.load_case_manifest(fixture / "case_manifest.json")
        )


def test_checked_in_verified_source_case_manifests_use_canonical_citation_modes() -> None:
    collector = import_collector()
    cases_root = ROOT / "benchmarks" / "verified_sources" / "cases"
    manifests = sorted(cases_root.glob("*/manifest.json"))
    assert manifests

    observed_modes: dict[str, str] = {}
    for manifest_path in manifests:
        payload = read_json(manifest_path)
        behavior = payload.get("source_ref_behavior")
        citation_mode = behavior.get("citation_mode") if isinstance(behavior, dict) else None
        if citation_mode is not None:
            assert citation_mode == collector.DETERMINISTIC_PUBLIC_PATH_CITATION_MODE
            observed_modes[manifest_path.parent.name] = str(citation_mode)
        policy = collector.citation_policy_from_case_manifest(
            collector.load_case_manifest(manifest_path)
        )
        if citation_mode is None:
            assert policy.mode == "authored-service-source-refs"
        else:
            assert policy.mode == "path-derived-deterministic"

    assert {"dendron", "logseq"} <= set(observed_modes)


def test_service_context_bundle_orientation_changes_qrel_rank_metrics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collector = import_collector()
    source_root = FIXTURE / "upstream_cache" / "collector-product" / "wiki"
    inputs = collector.load_inputs(
        FIXTURE / "corpus.jsonl",
        FIXTURE / "queries.jsonl",
        FIXTURE / "qrels.jsonl",
        source_root,
    )
    tokenizer = collector.FixtureTokenizer(TOKENIZER_ID, TOKENIZER_REVISION)
    citation_policy = collector.citation_policy_from_case_manifest(
        collector.load_case_manifest(FIXTURE / "case_manifest.json")
    )
    query = inputs.queries["q-release-explicit-root"]
    args = collector.parse_args(
        [
            "--source-root",
            str(source_root),
            "--case-manifest",
            str(FIXTURE / "case_manifest.json"),
            "--output-dir",
            str(tmp_path / "out"),
            "--tokenizer-id",
            TOKENIZER_ID,
            "--tokenizer-revision",
            TOKENIZER_REVISION,
            "--scratch-dir",
            str(tmp_path / "llmwiki-bench-bundle"),
        ]
    )
    captured_context: dict[str, object] = {}
    captured_bundle: dict[str, object] = {}
    original_context_payload = collector.collect_context_payload
    original_bundle_payload = collector.collect_context_bundle_payload

    def capture_context_payload(
        service: object,
        query_text: str,
        *,
        limit: int,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        payload, result_payloads = original_context_payload(
            service,
            query_text,
            limit=limit,
        )
        captured_context["payload"] = payload
        captured_context["result_payloads"] = result_payloads
        return payload, result_payloads

    def capture_bundle_payload(
        service: object,
        query_text: str,
        *,
        limit: int,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        payload, result_payloads = original_bundle_payload(
            service,
            query_text,
            limit=limit,
        )
        captured_bundle["payload"] = payload
        captured_bundle["result_payloads"] = result_payloads
        return payload, result_payloads

    monkeypatch.setattr(collector, "collect_context_payload", capture_context_payload)
    monkeypatch.setattr(collector, "collect_context_bundle_payload", capture_bundle_payload)

    with collector.materialization_stack(
        args,
        source_root,
        inputs,
        selected_variant_ids=("generic-shadow-managed-on",),
    ) as variants:
        variant = variants[0]
        context_run_id = collector.run_id_for(variant.id, "cold", "service-context")
        bundle_run_id = collector.run_id_for(variant.id, "cold", "service-context-bundle")
        context_service = collector.LlmWikiService(
            variant.root,
            managed_context=collector.managed_context_for_run(
                variant,
                run_id=context_run_id,
                query_id=query.query_id,
            ),
        )
        bundle_service = collector.LlmWikiService(
            variant.root,
            managed_context=collector.managed_context_for_run(
                variant,
                run_id=bundle_run_id,
                query_id=query.query_id,
            ),
        )
        context_rows = collector.timed_collect_query(
            context_service,
            query,
            run_id=context_run_id,
            surface="service-context",
            corpus=inputs.corpus,
            tokenizer=tokenizer,
            limit=3,
            citation_policy=citation_policy,
        ).rows
        bundle_rows = collector.timed_collect_query(
            bundle_service,
            query,
            run_id=bundle_run_id,
            surface="service-context-bundle",
            corpus=inputs.corpus,
            tokenizer=tokenizer,
            limit=3,
            citation_policy=citation_policy,
        ).rows

    context_payload = captured_context["payload"]
    context_result_payloads = captured_context["result_payloads"]
    bundle_payload = captured_bundle["payload"]
    bundle_result_payloads = captured_bundle["result_payloads"]
    assert isinstance(context_payload, dict)
    assert isinstance(bundle_payload, dict)
    assert isinstance(context_result_payloads, list)
    assert isinstance(bundle_result_payloads, list)

    orientation_payloads = [
        item for item in bundle_result_payloads if item.get("route") == "orientation"
    ]
    evidence_payloads = [item for item in bundle_result_payloads if item.get("route") == "search"]
    orientation_doc_ids = [str(item["page_id"]) for item in orientation_payloads]
    assert 0 <= len(orientation_doc_ids) <= 3
    assert len(orientation_doc_ids) == len(set(orientation_doc_ids))
    assert set(orientation_doc_ids) <= set(inputs.corpus)
    assert [item["page_id"] for item in evidence_payloads] == [
        "release",
        "managed-context",
        "contract",
    ]
    assert bundle_result_payloads == [*orientation_payloads, *evidence_payloads]
    assert [item["page_id"] for item in bundle_result_payloads].count("release") == 2

    expected_payloads: list[dict[str, object]] = []
    expected_doc_ids: list[str] = []
    seen_doc_ids: set[str] = set()
    for result_payload in bundle_result_payloads:
        doc_id = collector.require_doc_id(
            result_payload,
            inputs.corpus,
            run_id=bundle_run_id,
            query_id=query.query_id,
        )
        if doc_id in seen_doc_ids:
            continue
        seen_doc_ids.add(doc_id)
        expected_doc_ids.append(doc_id)
        expected_payloads.append(result_payload)

    assert [row["doc_id"] for row in bundle_rows] == expected_doc_ids
    assert [row["rank"] for row in bundle_rows] == list(range(1, len(bundle_rows) + 1))
    assert expected_doc_ids == list(
        dict.fromkeys([*orientation_doc_ids, "release", "managed-context", "contract"])
    )
    assert [row["score"] for row in bundle_rows] == [
        float(payload.get("score") or 0.0) for payload in expected_payloads
    ]
    assert [row["context_tokens"] for row in bundle_rows] == [
        tokenizer.count_tokens(collector.json_text(payload)) for payload in expected_payloads
    ]
    assert [row["payload_tokens"] for row in bundle_rows] == [
        tokenizer.count_tokens(collector.json_text(bundle_payload))
    ] * len(bundle_rows)
    assert bundle_rows[0]["payload_tokens"] > sum(int(row["context_tokens"]) for row in bundle_rows)
    assert [row["citation_ids"] for row in bundle_rows] == [
        collector.citation_ids(
            payload,
            inputs.corpus[doc_id],
            citation_policy=citation_policy,
        )
        for payload, doc_id in zip(expected_payloads, expected_doc_ids, strict=True)
    ]

    assert all(payload.get("route") == "search" for payload in context_result_payloads)
    assert [row["score"] for row in context_rows] == [
        float(payload.get("score") or 0.0) for payload in context_result_payloads
    ]
    assert [row["context_tokens"] for row in context_rows] == [
        tokenizer.count_tokens(collector.json_text(payload)) for payload in context_result_payloads
    ]
    assert [row["payload_tokens"] for row in context_rows] == [
        tokenizer.count_tokens(collector.json_text(context_payload))
    ] * len(context_rows)
    assert context_rows[0]["payload_tokens"] > context_rows[0]["context_tokens"]
    assert [row["doc_id"] for row in context_rows] == [
        "release",
        "managed-context",
        "contract",
    ]
    assert context_rows[0]["doc_id"] == "release"
    relevant_doc_ids = {
        qrel.doc_id
        for qrel in inputs.qrels_by_query[query.query_id]
        if qrel.relevance >= collector.benchmark.RELEVANT_THRESHOLD
    }
    assert relevant_doc_ids == {"release"}
    context_rank_by_doc = {str(row["doc_id"]): int(row["rank"]) for row in context_rows}
    bundle_rank_by_doc = {str(row["doc_id"]): int(row["rank"]) for row in bundle_rows}
    assert context_rank_by_doc["release"] == 1
    if orientation_doc_ids and orientation_doc_ids[0] != "release":
        assert bundle_rank_by_doc["release"] > context_rank_by_doc["release"]
    else:
        assert bundle_rank_by_doc["release"] == context_rank_by_doc["release"]

    context_benchmark_rows = benchmark_rows_from_dicts(collector, context_rows)
    bundle_benchmark_rows = benchmark_rows_from_dicts(collector, bundle_rows)
    qrels = inputs.qrels_by_query[query.query_id]
    relevant = {
        qrel.doc_id: qrel.relevance
        for qrel in qrels
        if qrel.relevance >= collector.benchmark.RELEVANT_THRESHOLD
    }
    assert collector.benchmark.reciprocal_rank(context_benchmark_rows, relevant) == 1.0
    assert collector.benchmark.reciprocal_rank(bundle_benchmark_rows, relevant) == 0.5
    assert (
        collector.benchmark.ndcg_at_k(
            context_benchmark_rows,
            qrels,
            k=10,
        )
        == 1.0
    )
    bundle_metrics, _bundle_maps = collector.benchmark.compute_run_metrics(
        bundle_run_id,
        bundle_benchmark_rows,
        corpus=inputs.corpus,
        queries={query.query_id: query},
        qrels_by_query={query.query_id: qrels},
    )
    assert bundle_metrics["evaluation_mode"] == "telemetry-only"
    assert "mrr" not in bundle_metrics
    assert "ndcg_at_10" not in bundle_metrics
    assert "context_tokens" not in bundle_metrics

    assert bundle_rows[0]["payload_bytes"] == len(collector.json_bytes(bundle_payload))
    assert bundle_rows[0]["payload_tokens"] == tokenizer.count_tokens(
        collector.json_text(bundle_payload)
    )
    assert context_rows[0]["payload_bytes"] == len(collector.json_bytes(context_payload))
    assert context_rows[0]["payload_tokens"] == tokenizer.count_tokens(
        collector.json_text(context_payload)
    )
    assert_query_level_values_are_stable(
        bundle_rows,
        fields=("payload_tokens", "payload_bytes", "latency_ms", "surface"),
    )
    assert_query_level_values_are_stable(
        context_rows,
        fields=("payload_tokens", "payload_bytes", "latency_ms", "surface"),
    )


def test_generic_shadow_materialization_removes_root_hubs_and_preserves_doc_ids(
    tmp_path: Path,
) -> None:
    collector = import_collector()
    source_root = FIXTURE / "upstream_cache" / "collector-product" / "wiki"
    inputs = collector.load_inputs(
        FIXTURE / "corpus.jsonl",
        FIXTURE / "queries.jsonl",
        FIXTURE / "qrels.jsonl",
        source_root,
    )

    materialization = collector.materialize_generic_shadow(source_root, inputs, tmp_path)
    report = materialization.report

    assert report["shadow_page_count"] == 3
    assert report["skipped_authored_orientation_hub_count"] == 2
    assert {hub["doc_id"] for hub in report["skipped_authored_orientation_hubs"]} == {
        "hot",
        "index",
    }
    assert not (materialization.root / "pages" / "hot.md").exists()
    assert not (materialization.root / "pages" / "index.md").exists()

    service = collector.LlmWikiService(materialization.root)
    manifest = service.manifest()
    assert manifest.adapter == "generic-markdown"
    assert manifest.page_count == 3
    results = service.search(
        "release evidence explicit source roots",
        limit=3,
        fields=collector.SEARCH_RESULT_FIELDS,
    )
    assert results[0]["page_id"] == "release"
    assert {result["page_id"] for result in results}.isdisjoint({"hot", "index"})


def test_materialization_stack_does_not_build_generic_shadow_for_native_selection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collector = import_collector()
    source_root = FIXTURE / "upstream_cache" / "collector-product" / "wiki"
    inputs = collector.load_inputs(
        FIXTURE / "corpus.jsonl",
        FIXTURE / "queries.jsonl",
        FIXTURE / "qrels.jsonl",
        source_root,
    )
    calls: list[object] = []

    def fail_if_materialized(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        pytest.fail("generic shadow materializer should not be invoked")

    monkeypatch.setattr(collector, "materialize_generic_shadow", fail_if_materialized)
    scratch_dir = tmp_path / "llmwiki-bench-native-only"
    args = collector.parse_args(
        [
            "--source-root",
            str(source_root),
            "--case-manifest",
            str(FIXTURE / "case_manifest.json"),
            "--output-dir",
            str(tmp_path / "out"),
            "--tokenizer-id",
            TOKENIZER_ID,
            "--tokenizer-revision",
            TOKENIZER_REVISION,
            "--scratch-dir",
            str(scratch_dir),
        ]
    )

    with collector.materialization_stack(
        args,
        source_root,
        inputs,
        selected_variant_ids=("native", "native-managed-on"),
    ) as variants:
        assert [variant.id for variant in variants] == ["native", "native-managed-on"]
        assert not (scratch_dir / "generic-shadow").exists()

    assert calls == []


def test_scratch_dir_uses_owned_child_and_rejects_source_tree(tmp_path: Path) -> None:
    collector = import_collector()
    fixture = copy_fixture(tmp_path)
    source_root = fixture / "upstream_cache" / "collector-product" / "wiki"
    inputs = collector.load_inputs(
        fixture / "corpus.jsonl",
        fixture / "queries.jsonl",
        fixture / "qrels.jsonl",
        source_root,
    )
    scratch_parent = tmp_path / "llmwiki-bench-existing"
    scratch_parent.mkdir()
    keep_file = scratch_parent / "keep.txt"
    keep_file.write_text("user-owned\n", encoding="utf-8")
    args = collector.parse_args(
        [
            "--source-root",
            str(source_root),
            "--case-manifest",
            str(fixture / "case_manifest.json"),
            "--output-dir",
            str(tmp_path / "out"),
            "--tokenizer-id",
            TOKENIZER_ID,
            "--tokenizer-revision",
            TOKENIZER_REVISION,
            "--scratch-dir",
            str(scratch_parent),
        ]
    )

    with collector.materialization_stack(
        args,
        source_root,
        inputs,
        selected_variant_ids=("native",),
    ):
        owned_children = [
            child for child in scratch_parent.iterdir() if child.name.startswith("collector-owned-")
        ]
        assert len(owned_children) == 1
        assert (owned_children[0] / collector.SCRATCH_SENTINEL).is_file()

    assert keep_file.read_text(encoding="utf-8") == "user-owned\n"
    assert not any(child.name.startswith("collector-owned-") for child in scratch_parent.iterdir())

    unsafe_args = collector.parse_args(
        [
            "--source-root",
            str(source_root),
            "--case-manifest",
            str(fixture / "case_manifest.json"),
            "--output-dir",
            str(tmp_path / "unsafe-out"),
            "--tokenizer-id",
            TOKENIZER_ID,
            "--tokenizer-revision",
            TOKENIZER_REVISION,
            "--scratch-dir",
            str(source_root / "llmwiki-bench-danger"),
        ]
    )
    with (
        pytest.raises(collector.CollectorError, match="scratch-dir"),
        collector.materialization_stack(
            unsafe_args,
            source_root,
            inputs,
            selected_variant_ids=("native",),
        ),
    ):
        pass


def test_collect_verified_source_runs_cli_native_only_allows_positive_root_hub_qrel(
    tmp_path: Path,
) -> None:
    fixture = copy_fixture(tmp_path)
    source_root = fixture / "upstream_cache" / "collector-product" / "wiki"
    output_dir = tmp_path / "collector-native-hub-output"
    append_qrel(
        fixture / "qrels.jsonl",
        {
            "query_id": "q-release-explicit-root",
            "doc_id": "index",
            "relevance": 3,
            "support_spans": [{"start": 0, "end": 80}],
            "citation_required": True,
        },
    )

    result = run_cli(
        fixture,
        output_dir,
        source_root=source_root,
        scratch_dir=tmp_path / "llmwiki-bench-native-hub",
        variants=("native",),
        phases=("cold",),
        limit=1,
        extra_args=["--allow-fixture-tokenizer"],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = read_json(output_dir / "collection-report.json")
    runs = read_jsonl(output_dir / "runs.jsonl")
    assert [variant["variant_id"] for variant in report["variants"]] == ["native"]
    assert {row["run_id"] for row in runs} == {
        "native_cold_service_context",
        "native_cold_service_context_orientation",
        "native_cold_service_context_bundle",
        "native_cold_service_search_read",
    }


def test_shadow_doc_id_paths_are_windows_safe_and_collision_resistant() -> None:
    collector = import_collector()

    assert collector.doc_id_to_shadow_path("pages/release").as_posix() == "pages/release.md"
    assert collector.doc_id_to_shadow_path("pages/release.md").as_posix() == ("pages/release.md.md")
    reserved = collector.doc_id_to_shadow_path("pages/aux")
    assert reserved.as_posix().startswith("pages/aux--")
    assert collector.doc_id_to_shadow_path("pages/con.txt").name.lower().startswith("con--")
    assert collector.doc_id_to_shadow_path("pages/COM1.md").name.lower().startswith("com1--")
    unsafe = collector.doc_id_to_shadow_path("pages/a*b?")
    assert "*" not in unsafe.as_posix()
    assert "?" not in unsafe.as_posix()

    for doc_id in ("", "/absolute", r"pages\bad", "pages/../bad", "pages//bad", "C:/bad"):
        with pytest.raises(collector.CollectorError):
            collector.doc_id_to_shadow_path(doc_id)


def test_collect_verified_source_runs_cli_normal_run_writes_public_safe_artifacts(
    tmp_path: Path,
) -> None:
    if not SCRIPT.exists():
        pytest.skip("scripts/collect_verified_source_runs.py has not been added yet")

    fixture = copy_fixture(tmp_path)
    source_root = fixture / "upstream_cache" / "collector-product" / "wiki"
    output_dir = tmp_path / "collector-output"
    scratch_dir = tmp_path / "llmwiki-bench-scratch"
    before_digest = tree_digest(source_root)
    before_qrels_digest = file_digest(fixture / "qrels.jsonl")
    result = run_cli(
        fixture,
        output_dir,
        source_root=source_root,
        scratch_dir=scratch_dir,
        extra_args=["--allow-fixture-tokenizer"],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert tree_digest(source_root) == before_digest
    assert file_digest(fixture / "qrels.jsonl") == before_qrels_digest

    report = read_json(output_dir / "collection-report.json")
    runs = read_jsonl(output_dir / "runs.jsonl")
    copied_case_manifest = read_json(output_dir / "case-manifest.json")
    fixture_case_manifest = read_json(fixture / "case_manifest.json")
    assert (output_dir / "corpus.jsonl").read_text(encoding="utf-8") == (
        fixture / "corpus.jsonl"
    ).read_text(encoding="utf-8")
    assert (output_dir / "queries.jsonl").read_text(encoding="utf-8") == (
        fixture / "queries.jsonl"
    ).read_text(encoding="utf-8")
    assert (output_dir / "qrels.jsonl").read_text(encoding="utf-8") == (
        fixture / "qrels.jsonl"
    ).read_text(encoding="utf-8")
    assert copied_case_manifest == expected_public_case_manifest(fixture_case_manifest)

    assert report["schema"] == "llmwiki-serve-verified-source-run-collection-v1"
    assert report["mode"] == "collect"
    assert report["evidence_track"] == "quality-benchmark-run-collection"
    case_metadata = expected_case_metadata(fixture_case_manifest)
    assert report["case"] == case_metadata
    assert report["source"] == {"mode": "explicit-source-root", **case_metadata}
    assert report["inputs"] == {"corpus_records": 5, "query_records": 4, "qrel_records": 4}
    assert report["output_files"] == [
        "case-manifest.json",
        "corpus.jsonl",
        "queries.jsonl",
        "qrels.jsonl",
        "runs.jsonl",
    ]
    assert report["source_mutation"]["mutated"] is False
    assert report["source_mutation"]["before_sha256"] == report["source_mutation"]["after_sha256"]
    assert report["source_mutation"]["before_sha256"] == before_digest
    assert report["tokenizer"] == {
        "collection_evidence": "fixture-tokenizer-test-only",
        "id": TOKENIZER_ID,
        "policy": "qwen-tokenizer-required-no-byte-proxy",
        "revision": TOKENIZER_REVISION,
    }
    assert report["citation_evidence"]["mode"] == "authored-service-source-refs"
    assert report["citation_evidence"]["declared_citation_mode"] is None
    assert "empty service source_refs stay empty" in report["citation_evidence"]["policy"]

    variant_summaries = {variant["variant_id"]: variant for variant in report["variants"]}
    assert set(variant_summaries) == set(RUN_VARIANTS)
    assert variant_summaries["native"]["adapter"] == "llmwiki-markdown"
    assert variant_summaries["native"]["managed_context_effective"] is False
    assert variant_summaries["native-managed-on"]["adapter"] == "llmwiki-markdown"
    assert variant_summaries["native-managed-on"]["managed_context_effective"] is False
    assert variant_summaries["generic-shadow-managed-off"]["adapter"] == "generic-markdown"
    assert variant_summaries["generic-shadow-managed-off"]["managed_context_effective"] is False
    assert variant_summaries["generic-shadow-managed-on"]["adapter"] == "generic-markdown"
    assert variant_summaries["generic-shadow-managed-on"]["managed_context_effective"] is True
    assert (
        variant_summaries["generic-shadow-managed-on"]["materialization"]["kind"]
        == "generic-shadow"
    )
    assert (
        variant_summaries["generic-shadow-managed-on"]["materialization"]["page_id_mapping"]
        == "manifest-doc-id-preserved-in-scratch-frontmatter"
    )
    assert (
        variant_summaries["generic-shadow-managed-on"]["materialization"][
            "skipped_authored_orientation_hub_count"
        ]
        == 2
    )

    assert {row["run_id"] for row in runs} == EXPECTED_RUN_IDS
    assert {row["surface"] for row in runs} == set(SURFACES)
    assert all(row["source_bytes_scanned"] is None for row in runs)
    assert all(row["tokenizer_id"] == TOKENIZER_ID for row in runs)
    assert all(row["tokenizer_revision"] == TOKENIZER_REVISION for row in runs)
    assert all(isinstance(row["context_tokens"], int) for row in runs)
    assert all(row["context_tokens"] > 0 for row in runs)
    assert all(isinstance(row["payload_tokens"], int) for row in runs)
    assert all(row["payload_tokens"] > 0 for row in runs)
    assert any(row["citation_ids"] == ["SRC-COLLECTOR-REL"] for row in runs)
    assert_query_level_values_are_stable(
        runs,
        fields=("payload_tokens", "payload_bytes", "latency_ms", "surface"),
    )
    assert_run_summaries_cover_queries_and_phases(report["runs"])
    assert_negative_query_has_no_rows(report["runs"], runs)
    assert_no_byte_proxy_markers(report)
    assert_no_byte_proxy_markers(runs)
    assert_public_safe(report, fixture, output_dir, scratch_dir)
    assert_public_safe(runs, fixture, output_dir, scratch_dir)
    assert_public_safe(copied_case_manifest, fixture, output_dir, scratch_dir)
    assert_private_operational_fields_are_absent(report)


def test_deterministic_public_path_citation_mode_fills_empty_service_refs_without_mixing(
    tmp_path: Path,
) -> None:
    fixture = copy_fixture(tmp_path)
    source_root = fixture / "upstream_cache" / "collector-product" / "wiki"
    output_dir = tmp_path / "collector-path-citation-output"
    strip_authored_source_refs(fixture, except_doc_ids={"release"})
    add_corpus_source_ref(fixture, "release", "collector-path:release.md")
    set_citation_mode(fixture, import_collector().DETERMINISTIC_PUBLIC_PATH_CITATION_MODE)

    result = run_cli(
        fixture,
        output_dir,
        source_root=source_root,
        scratch_dir=tmp_path / "llmwiki-bench-path-citation",
        variants=("native",),
        phases=("cold",),
        limit=3,
        extra_args=["--allow-fixture-tokenizer"],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = read_json(output_dir / "collection-report.json")
    runs = read_jsonl(output_dir / "runs.jsonl")
    corpus_by_doc = {row["doc_id"]: row for row in read_jsonl(fixture / "corpus.jsonl")}

    assert report["citation_evidence"]["mode"] == "path-derived-deterministic"
    assert (
        report["citation_evidence"]["declared_citation_mode"]
        == import_collector().DETERMINISTIC_PUBLIC_PATH_CITATION_MODE
    )
    assert "corpus.source_ref_ids" in report["citation_evidence"]["policy"]
    assert "not mixed with derived IDs" in report["citation_evidence"]["policy"]

    release_rows = [row for row in runs if row["doc_id"] == "release"]
    derived_rows = [row for row in runs if row["doc_id"] != "release"]
    assert release_rows
    assert derived_rows
    assert all(row["citation_ids"] == ["SRC-COLLECTOR-REL"] for row in release_rows)
    for row in derived_rows:
        assert row["citation_ids"] == corpus_by_doc[row["doc_id"]]["source_ref_ids"]


def test_empty_service_refs_without_deterministic_citation_mode_stay_empty(
    tmp_path: Path,
) -> None:
    fixture = copy_fixture(tmp_path)
    source_root = fixture / "upstream_cache" / "collector-product" / "wiki"
    output_dir = tmp_path / "collector-empty-citation-output"
    strip_authored_source_refs(fixture)

    result = run_cli(
        fixture,
        output_dir,
        source_root=source_root,
        scratch_dir=tmp_path / "llmwiki-bench-empty-citation",
        variants=("native",),
        phases=("cold",),
        limit=3,
        extra_args=["--allow-fixture-tokenizer"],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = read_json(output_dir / "collection-report.json")
    runs = read_jsonl(output_dir / "runs.jsonl")
    assert report["citation_evidence"]["mode"] == "authored-service-source-refs"
    assert all(row["citation_ids"] == [] for row in runs)


def test_collect_verified_source_runs_cli_dry_run_writes_public_safe_report(
    tmp_path: Path,
) -> None:
    if not SCRIPT.exists():
        pytest.skip("scripts/collect_verified_source_runs.py has not been added yet")

    fixture = copy_fixture(tmp_path)
    source_root = fixture / "upstream_cache" / "collector-product" / "wiki"
    output_dir = tmp_path / "collector-dry-run-output"
    scratch_dir = tmp_path / "llmwiki-bench-dry-run"
    result = run_cli(
        fixture,
        output_dir,
        source_root=source_root,
        scratch_dir=scratch_dir,
        extra_args=["--dry-run"],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = read_json(output_dir / "collection-report.json")

    assert report["mode"] == "dry-run"
    case_metadata = expected_case_metadata(read_json(fixture / "case_manifest.json"))
    assert report["case"] == case_metadata
    assert report["source"] == {"mode": "explicit-source-root", **case_metadata}
    assert report["tokenizer"] is None
    assert report["citation_evidence"]["mode"] == "authored-service-source-refs"
    assert report["variants"] == []
    assert report["runs"] == []
    assert report["output_files"] == []
    assert report["source_mutation"] is None
    assert report["inputs"] == {"corpus_records": 5, "query_records": 4, "qrel_records": 4}
    assert not (output_dir / "runs.jsonl").exists()
    assert_public_safe(report, fixture, output_dir, scratch_dir)
    assert_private_operational_fields_are_absent(report)


def test_collect_verified_source_runs_cli_accepts_explicit_input_overrides(
    tmp_path: Path,
) -> None:
    if not SCRIPT.exists():
        pytest.skip("scripts/collect_verified_source_runs.py has not been added yet")

    fixture = copy_fixture(tmp_path)
    source_root = fixture / "upstream_cache" / "collector-product" / "wiki"
    output_dir = tmp_path / "collector-override-output"
    scratch_dir = tmp_path / "llmwiki-bench-override"
    result = run_cli(
        fixture,
        output_dir,
        source_root=source_root,
        scratch_dir=scratch_dir,
        include_input_overrides=True,
        extra_args=["--dry-run"],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = read_json(output_dir / "collection-report.json")
    case_metadata = expected_case_metadata(read_json(fixture / "case_manifest.json"))
    assert report["case"] == case_metadata
    assert report["source"] == {"mode": "explicit-source-root", **case_metadata}
    assert report["inputs"] == {"corpus_records": 5, "query_records": 4, "qrel_records": 4}


def test_collect_verified_source_runs_cli_accepts_verified_source_metadata_manifest_with_overrides(
    tmp_path: Path,
) -> None:
    if not SCRIPT.exists():
        pytest.skip("scripts/collect_verified_source_runs.py has not been added yet")

    fixture = copy_fixture(tmp_path)
    source_root = fixture / "upstream_cache" / "collector-product" / "wiki"
    output_dir = tmp_path / "collector-metadata-manifest-output"
    scratch_dir = tmp_path / "llmwiki-bench-metadata"
    metadata_manifest = fixture / "verified_source_manifest.json"
    metadata_manifest.write_text(
        json.dumps(
            {
                "schema": "llmwiki-serve-verified-source-case-manifest-v1",
                "case_id": "collector-metadata",
                "product": "Collector Metadata Fixture",
                "official_link": "https://example.invalid/llmwiki-serve/collector-metadata",
                "source_kind": "actual-pinned",
                "commit": "2222222222222222222222222222222222222222",
                "license_evidence": "synthetic-fixture",
                "evidence_type": "metadata-only-verified-source-manifest",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    result = run_cli(
        fixture,
        output_dir,
        source_root=source_root,
        scratch_dir=scratch_dir,
        case_manifest_path=metadata_manifest,
        include_input_overrides=True,
        extra_args=["--dry-run"],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = read_json(output_dir / "collection-report.json")
    assert report["case"] == {
        "case_id": "collector-metadata",
        "product": "Collector Metadata Fixture",
        "official_link": "https://example.invalid/llmwiki-serve/collector-metadata",
        "source_kind": "actual-pinned",
        "evidence_label": "metadata-only-verified-source-manifest",
        "pinned_commit": "2222222222222222222222222222222222222222",
        "license": "synthetic-fixture",
    }
    assert report["inputs"] == {"corpus_records": 5, "query_records": 4, "qrel_records": 4}


@pytest.mark.parametrize(
    ("case_id", "alias", "registry_source_path", "manifest_source_path"),
    (
        ("pratiyush-llm-wiki", "pratiyush", ".", "wiki/"),
        ("langchain-openwiki-self-docs", "openwiki", "openwiki", "openwiki/"),
    ),
)
def test_upstream_case_manifest_source_path_is_authoritative_for_nested_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case_id: str,
    alias: str,
    registry_source_path: str,
    manifest_source_path: str,
) -> None:
    collector = import_collector()
    checkout_cache, checkout_dir, commit = make_git_checkout(
        tmp_path,
        case_id=case_id,
        source_dirs=("wiki", "openwiki"),
    )
    monkeypatch.setattr(
        collector.upstream_smoke,
        "CASES",
        (
            upstream_case(
                collector,
                case_id=case_id,
                alias=alias,
                commit=commit,
                source_path=registry_source_path,
            ),
        ),
    )
    manifest_path = write_upstream_manifest(
        tmp_path,
        case_id=case_id,
        commit=commit,
        source_path=manifest_source_path,
    )
    args = upstream_resolve_args(collector, tmp_path, alias, checkout_cache, manifest_path)

    source = collector.resolve_source(args, collector.load_case_manifest(manifest_path))

    assert source.checkout_root == checkout_dir.resolve()
    assert source.root == (checkout_dir / manifest_source_path.rstrip("/")).resolve()
    assert source.case_metadata["case_id"] == case_id
    assert source.case_metadata["pinned_commit"] == commit


@pytest.mark.parametrize(
    ("updates", "error_match"),
    (
        ({"source_path": "../wiki"}, "source_path.*dot-dot"),
        ({"commit": "2222222222222222222222222222222222222222"}, "pinned_commit"),
        ({"case_id": "wrong-case"}, "case_id"),
    ),
)
def test_upstream_case_manifest_metadata_and_source_path_mismatches_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    updates: Mapping[str, Any],
    error_match: str,
) -> None:
    collector = import_collector()
    case_id = "pratiyush-llm-wiki"
    checkout_cache, _checkout_dir, commit = make_git_checkout(
        tmp_path,
        case_id=case_id,
        source_dirs=("wiki",),
    )
    monkeypatch.setattr(
        collector.upstream_smoke,
        "CASES",
        (
            upstream_case(
                collector,
                case_id=case_id,
                alias="pratiyush",
                commit=commit,
                source_path=".",
            ),
        ),
    )
    manifest_path = write_upstream_manifest(
        tmp_path,
        case_id=case_id,
        commit=commit,
        source_path="wiki/",
        updates=updates,
    )
    args = upstream_resolve_args(collector, tmp_path, "pratiyush", checkout_cache, manifest_path)

    with pytest.raises(collector.CollectorError, match=error_match):
        collector.resolve_source(args, collector.load_case_manifest(manifest_path))


def test_upstream_cached_checkout_head_mismatch_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    collector = import_collector()
    case_id = "pratiyush-llm-wiki"
    expected_commit = "1111111111111111111111111111111111111111"
    checkout_cache, _checkout_dir, actual_commit = make_git_checkout(
        tmp_path,
        case_id=case_id,
        source_dirs=("wiki",),
        checkout_commit=expected_commit,
    )
    assert actual_commit != expected_commit
    monkeypatch.setattr(
        collector.upstream_smoke,
        "CASES",
        (
            upstream_case(
                collector,
                case_id=case_id,
                alias="pratiyush",
                commit=expected_commit,
                source_path=".",
            ),
        ),
    )
    manifest_path = write_upstream_manifest(
        tmp_path,
        case_id=case_id,
        commit=expected_commit,
        source_path="wiki/",
    )
    args = upstream_resolve_args(collector, tmp_path, "pratiyush", checkout_cache, manifest_path)

    with pytest.raises(collector.CollectorError, match="checkout HEAD"):
        collector.resolve_source(args, collector.load_case_manifest(manifest_path))


def test_fixture_tokenizer_cannot_mask_verified_qwen_tokenizer_flag() -> None:
    collector = import_collector()
    args = collector.parse_args(
        [
            "--source-root",
            str(FIXTURE / "upstream_cache" / "collector-product" / "wiki"),
            "--case-manifest",
            str(FIXTURE / "case_manifest.json"),
            "--output-dir",
            "unused",
            "--tokenizer-id",
            TOKENIZER_ID,
            "--tokenizer-revision",
            TOKENIZER_REVISION,
            "--allow-fixture-tokenizer",
            "--verify-tokenizer",
        ]
    )

    with pytest.raises(collector.CollectorError):
        collector.load_counting_tokenizer(args)


def test_collect_verified_source_runs_cli_preserves_legacy_corpus_manifest_flow(
    tmp_path: Path,
) -> None:
    if not SCRIPT.exists():
        pytest.skip("scripts/collect_verified_source_runs.py has not been added yet")

    fixture = copy_fixture(tmp_path)
    source_root = fixture / "upstream_cache" / "collector-product" / "wiki"
    output_dir = tmp_path / "collector-legacy-output"
    scratch_dir = tmp_path / "llmwiki-bench-legacy"
    result = run_cli(
        fixture,
        output_dir,
        source_root=source_root,
        scratch_dir=scratch_dir,
        case_manifest_path=fixture / "corpus.jsonl",
        include_input_overrides=True,
        include_corpus_override=False,
        extra_args=["--dry-run"],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = read_json(output_dir / "collection-report.json")
    assert report["inputs"] == {"corpus_records": 5, "query_records": 4, "qrel_records": 4}


def test_collector_input_validation_uses_canonical_markdown_hash(tmp_path: Path) -> None:
    collector = import_collector()
    fixture = copy_fixture(tmp_path)
    source_root = fixture / "upstream_cache" / "collector-product" / "wiki"
    release_path = source_root / "release.md"
    lf_content = release_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    release_path.write_bytes(lf_content.replace("\n", "\r\n").encode("utf-8"))

    collector.load_inputs(
        fixture / "corpus.jsonl",
        fixture / "queries.jsonl",
        fixture / "qrels.jsonl",
        source_root,
    )

    release_path.write_text(lf_content + "\nChanged content.\n", encoding="utf-8")
    with pytest.raises(collector.benchmark.BenchmarkValidationError, match="canonical Markdown"):
        collector.load_inputs(
            fixture / "corpus.jsonl",
            fixture / "queries.jsonl",
            fixture / "qrels.jsonl",
            source_root,
        )


def import_collector() -> Any:
    if importlib.util.find_spec("scripts.collect_verified_source_runs") is None:
        pytest.skip("scripts/collect_verified_source_runs.py has not been added yet")
    from scripts import collect_verified_source_runs

    return collect_verified_source_runs


def copy_fixture(tmp_path: Path) -> Path:
    copied = tmp_path / "collector_tiny"
    shutil.copytree(FIXTURE, copied)
    return copied


def strip_authored_source_refs(fixture: Path, *, except_doc_ids: set[str] | None = None) -> None:
    except_doc_ids = except_doc_ids or set()
    source_root = fixture / "upstream_cache" / "collector-product" / "wiki"
    corpus = read_jsonl(fixture / "corpus.jsonl")
    for row in corpus:
        if row["doc_id"] in except_doc_ids:
            continue
        source_path = source_root / str(row["path"])
        lines = [
            line
            for line in source_path.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("source_refs:")
        ]
        source_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        row["sha256"] = bench.canonical_markdown_file_sha256(source_path)
    write_jsonl_file(fixture / "corpus.jsonl", corpus)


def add_corpus_source_ref(fixture: Path, doc_id: str, source_ref_id: str) -> None:
    corpus = read_jsonl(fixture / "corpus.jsonl")
    for row in corpus:
        if row["doc_id"] == doc_id:
            row["source_ref_ids"].append(source_ref_id)
            break
    else:  # pragma: no cover - guarded by test fixture constants.
        raise AssertionError(f"unknown fixture doc_id {doc_id!r}")
    write_jsonl_file(fixture / "corpus.jsonl", corpus)


def set_citation_mode(fixture: Path, citation_mode: str) -> None:
    manifest_path = fixture / "case_manifest.json"
    manifest = read_json(manifest_path)
    manifest["source_ref_behavior"] = {
        "projected_source_refs": "empty",
        "citation_mode": citation_mode,
        "reason": "fixture pages project deterministic public path IDs when refs are empty",
    }
    write_json_file(manifest_path, manifest)


def append_qrel(path: Path, qrel: Mapping[str, Any]) -> None:
    rows = read_jsonl(path)
    rows.append(dict(qrel))
    write_jsonl_file(path, rows)


def run_cli(
    fixture: Path,
    output_dir: Path,
    *,
    source_root: Path,
    scratch_dir: Path,
    extra_args: list[str],
    case_manifest_path: Path | None = None,
    include_input_overrides: bool = False,
    include_corpus_override: bool = True,
    variants: tuple[str, ...] = RUN_VARIANTS,
    phases: tuple[str, ...] = PHASES,
    limit: int = 3,
) -> subprocess.CompletedProcess[str]:
    input_override_args: list[str] = []
    if include_input_overrides:
        if include_corpus_override:
            input_override_args.extend(["--corpus", str(fixture / "corpus.jsonl")])
        input_override_args.extend(
            [
                "--queries",
                str(fixture / "queries.jsonl"),
                "--qrels",
                str(fixture / "qrels.jsonl"),
            ]
        )
    variant_args = [item for variant in variants for item in ("--variant", variant)]
    phase_args = [item for phase in phases for item in ("--phase", phase)]
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--source-root",
            str(source_root),
            "--case-manifest",
            str(case_manifest_path or fixture / "case_manifest.json"),
            *input_override_args,
            "--output-dir",
            str(output_dir),
            "--tokenizer-id",
            TOKENIZER_ID,
            "--tokenizer-revision",
            TOKENIZER_REVISION,
            "--scratch-dir",
            str(scratch_dir),
            *variant_args,
            *phase_args,
            "--limit",
            str(limit),
            *extra_args,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def make_git_checkout(
    tmp_path: Path,
    *,
    case_id: str,
    source_dirs: tuple[str, ...],
    checkout_commit: str | None = None,
) -> tuple[Path, Path, str]:
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    work_dir = tmp_path / f"checkout-work-{case_id}"
    work_dir.mkdir()
    run_git(work_dir, "init", "-q")
    run_git(work_dir, "config", "user.email", "collector@example.invalid")
    run_git(work_dir, "config", "user.name", "Collector Fixture")
    for source_dir in source_dirs:
        source_root = work_dir / source_dir
        source_root.mkdir(parents=True, exist_ok=True)
        (source_root / "release.md").write_text(
            "---\n"
            "id: release\n"
            "title: Release\n"
            "review_state: approved\n"
            "---\n"
            "# Release\n\n"
            "Nested upstream release fixture.\n",
            encoding="utf-8",
        )
    run_git(work_dir, "add", ".")
    run_git(work_dir, "commit", "-q", "-m", "collector fixture")
    actual_commit = run_git(work_dir, "rev-parse", "HEAD").stdout.strip()
    checkout_cache = tmp_path / "checkout-cache"
    checkout_dir = checkout_cache / case_id / (checkout_commit or actual_commit)
    checkout_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(work_dir), checkout_dir)
    return checkout_cache, checkout_dir, actual_commit


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )


def upstream_case(
    collector: Any,
    *,
    case_id: str,
    alias: str,
    commit: str,
    source_path: str,
) -> Any:
    return collector.upstream_smoke.UpstreamSmokeCase(
        id=case_id,
        aliases=(alias,),
        repo_url=f"https://github.com/example/{case_id}.git",
        ref=commit,
        source_path=source_path,
        expected_adapter="llmwiki-markdown",
        expected_implementation="llmwiki-markdown",
        query="release",
        min_pages=1,
        min_approved_pages=1,
        product=f"example/{case_id}",
        official_link=f"https://github.com/example/{case_id}",
        source_kind="actual-pinned",
        license_evidence="MIT",
        evidence_type="Actual pinned collector upstream fixture.",
    )


def write_upstream_manifest(
    tmp_path: Path,
    *,
    case_id: str,
    commit: str,
    source_path: str,
    updates: Mapping[str, Any] | None = None,
) -> Path:
    payload: dict[str, Any] = {
        "schema": "llmwiki-serve-verified-source-case-manifest-v1",
        "case_id": case_id,
        "product": f"example/{case_id}",
        "official_link": f"https://github.com/example/{case_id}",
        "official_url": f"https://github.com/example/{case_id}",
        "repo_url": f"https://github.com/example/{case_id}.git",
        "source_kind": "actual-pinned",
        "commit": commit,
        "full_commit": commit,
        "source_path": source_path,
        "license_evidence": "MIT",
        "license": "MIT",
        "evidence_type": "Actual pinned collector upstream manifest fixture.",
    }
    if updates:
        payload.update(updates)
    manifest_path = tmp_path / f"{case_id}-manifest.json"
    write_json_file(manifest_path, payload)
    return manifest_path


def upstream_resolve_args(
    collector: Any,
    tmp_path: Path,
    upstream_case_id: str,
    checkout_cache: Path,
    manifest_path: Path,
) -> Any:
    return collector.parse_args(
        [
            "--upstream-case",
            upstream_case_id,
            "--checkout-cache",
            str(checkout_cache),
            "--case-manifest",
            str(manifest_path),
            "--output-dir",
            str(tmp_path / "out"),
            "--tokenizer-id",
            TOKENIZER_ID,
            "--tokenizer-revision",
            TOKENIZER_REVISION,
            "--timeout",
            "30",
        ]
    )


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def write_json_file(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl_file(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def file_digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def tree_digest(root: Path) -> str:
    digest = sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root).as_posix()
        if any(part in {".git", "__pycache__"} for part in Path(relative).parts):
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def assert_public_safe(payload: object, *roots: Path) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    forbidden = {
        "127.0.0.1",
        "localhost",
        ".llmwiki-work",
        ".runtime-logs",
        "OPENAI_API_KEY",
        "sk-proj-",
    }
    for root in roots:
        resolved = root.resolve()
        forbidden.add(str(resolved))
        forbidden.add(resolved.as_posix())
    leaked = sorted(value for value in forbidden if value in serialized)
    assert leaked == []


def assert_no_byte_proxy_markers(payload: object) -> None:
    serialized = json.dumps(payload, sort_keys=True).lower()
    for marker in ("byte/4", "bytes/4", "byte_div_4", "heuristic", "approx"):
        assert marker not in serialized


def expected_case_metadata(case_manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {field: case_manifest[field] for field in CASE_METADATA_FIELDS}


def expected_public_case_manifest(case_manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": "llmwiki-serve-verified-source-collector-case-v1",
        **expected_case_metadata(case_manifest),
        "inputs": {
            "corpus": "corpus.jsonl",
            "queries": "queries.jsonl",
            "qrels": "qrels.jsonl",
        },
    }


def assert_private_operational_fields_are_absent(payload: object) -> None:
    serialized = json.dumps(payload, sort_keys=True)
    for marker in ("checkout_root", "source_root", "source_path", "license_evidence"):
        assert marker not in serialized


def assert_query_level_values_are_stable(
    runs: Iterable[Mapping[str, Any]],
    *,
    fields: tuple[str, ...],
) -> None:
    grouped: dict[tuple[str, str], dict[str, set[object]]] = defaultdict(
        lambda: {field: set() for field in fields}
    )
    for row in runs:
        values = grouped[(str(row["run_id"]), str(row["query_id"]))]
        for field in fields:
            values[field].add(row[field])

    for key, values in grouped.items():
        assert all(len(field_values) == 1 for field_values in values.values()), key


def benchmark_rows_from_dicts(
    collector: Any,
    rows: Iterable[Mapping[str, Any]],
) -> list[Any]:
    benchmark_rows: list[Any] = []
    for row in rows:
        source_bytes_scanned = row.get("source_bytes_scanned")
        benchmark_rows.append(
            collector.benchmark.RunRow(
                run_id=str(row["run_id"]),
                query_id=str(row["query_id"]),
                rank=int(row["rank"]),
                doc_id=str(row["doc_id"]),
                score=float(row["score"]),
                citation_ids=tuple(str(item) for item in row["citation_ids"]),
                context_tokens=int(row["context_tokens"]),
                payload_tokens=int(row["payload_tokens"]),
                payload_bytes=int(row["payload_bytes"]),
                latency_ms=float(row["latency_ms"]),
                surface=str(row["surface"]),
                tokenizer_id=str(row["tokenizer_id"]),
                tokenizer_revision=str(row["tokenizer_revision"]),
                source_bytes_scanned=(
                    int(source_bytes_scanned) if source_bytes_scanned is not None else None
                ),
            )
        )
    return benchmark_rows


def assert_run_summaries_cover_queries_and_phases(
    run_summaries: list[Mapping[str, Any]],
) -> None:
    assert {summary["run_id"] for summary in run_summaries} == EXPECTED_RUN_IDS
    assert {summary["phase"] for summary in run_summaries} == set(PHASES)
    assert {summary["surface"] for summary in run_summaries} == set(SURFACES)
    for summary in run_summaries:
        assert summary["query_count"] == 4
        assert len(summary["query_payloads"]) == 4
        for query_payload in summary["query_payloads"]:
            assert set(query_payload) == {
                "query_id",
                "payload_tokens",
                "payload_bytes",
                "latency_ms",
                "row_count",
                "orientation_count",
                "evidence_count",
            }
            assert query_payload["payload_tokens"] >= 0
            assert query_payload["payload_bytes"] >= 0
            assert query_payload["latency_ms"] >= 0
            assert query_payload["orientation_count"] >= 0
            assert query_payload["evidence_count"] >= 0
            if summary["surface"] == "service-context":
                assert query_payload["row_count"] == query_payload["evidence_count"]
            elif summary["surface"] == "service-context-orientation":
                assert query_payload["row_count"] == query_payload["orientation_count"]
            elif summary["surface"] == "service-context-bundle":
                assert (
                    query_payload["row_count"]
                    <= query_payload["orientation_count"] + query_payload["evidence_count"]
                )
                if query_payload["orientation_count"] or query_payload["evidence_count"]:
                    assert query_payload["row_count"] > 0
            elif summary["surface"] == "service-search-read":
                assert query_payload["orientation_count"] == 0
                assert query_payload["row_count"] == query_payload["evidence_count"]


def assert_negative_query_has_no_rows(
    run_summaries: list[Mapping[str, Any]],
    runs: list[Mapping[str, Any]],
) -> None:
    assert all(
        row["query_id"] != "q-negative-private-path"
        for row in runs
        if row["surface"] not in {"service-context-orientation", "service-context-bundle"}
    )
    for summary in run_summaries:
        negative = next(
            payload
            for payload in summary["query_payloads"]
            if payload["query_id"] == "q-negative-private-path"
        )
        if summary["surface"] in {"service-context-orientation", "service-context-bundle"}:
            assert negative["row_count"] == negative["orientation_count"]
        else:
            assert negative["row_count"] == 0
        assert negative["evidence_count"] == 0
