from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from scripts.benchmark_adapters import agent_guided_lexical_runner as runner

FIXTURE_DIR = Path("benchmarks/agent_guided_lexical/fixture")


class FakeSearchService:
    def __init__(self, orientation_source: str) -> None:
        self.orientation_source = orientation_source
        self.calls: list[dict[str, object]] = []
        self.context_calls: list[str] = []

    def context(
        self,
        query: str,
        *,
        limit: int = runner.RETRIEVAL_LIMIT,
        mode: runner.BenchmarkSearchMode = "lexical",
    ) -> object:
        self.context_calls.append(query)
        return SimpleNamespace(
            retrieval_guidance=SimpleNamespace(orientation_source=self.orientation_source)
        )

    def search(
        self,
        query: str,
        *,
        limit: int,
        mode: runner.BenchmarkSearchMode = "lexical",
        query_variants: Sequence[Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "query": query,
                "mode": mode,
                "query_variants": list(query_variants or ()),
            }
        )
        text = runner.normalize_channel_key(" ".join((query, *(query_variants or ()))))
        if "quantum" in text or "catering" in text:
            return []
        if "overcharge" in text or "refund approval window" in text or "credit reversal" in text:
            return [result("billing_refund_policy", 1.0), result("irrelevant_distractor", 0.2)][
                :limit
            ]
        if "환불" in text or "과금 취소" in text:
            return [result("korean_refund_policy", 1.0), result("irrelevant_distractor", 0.2)][
                :limit
            ]
        if (
            "aglparser.resolve_symbol" in text
            or "e_bridge_timeout" in text
            or "bridge timeout" in text
            or "resolver timeout" in text
        ):
            return [result("code_identifier_reference", 1.0), result("irrelevant_distractor", 0.2)][
                :limit
            ]
        if (
            "prompt injection" in text
            or "untrusted source evidence" in text
            or "instruction-like prose" in text
            or "source prose evidence" in text
        ):
            return [result("prompt_injection_safety", 1.0), result("irrelevant_distractor", 0.2)][
                :limit
            ]
        return [result("irrelevant_distractor", 0.1)][:limit]


class LegacyFakeSearchService:
    def __init__(self, orientation_source: str) -> None:
        self.orientation_source = orientation_source
        self.calls: list[str] = []
        self.context_calls: list[str] = []

    def context(
        self,
        query: str,
        *,
        limit: int = runner.RETRIEVAL_LIMIT,
        mode: runner.BenchmarkSearchMode = "lexical",
    ) -> object:
        self.context_calls.append(query)
        return SimpleNamespace(
            retrieval_guidance=SimpleNamespace(orientation_source=self.orientation_source)
        )

    def search(
        self,
        query: str,
        *,
        limit: int,
        mode: runner.BenchmarkSearchMode = "lexical",
    ) -> list[dict[str, Any]]:
        self.calls.append(query)
        service = FakeSearchService(self.orientation_source)
        return service.search(query, limit=limit, mode=mode)


class RecordingFactory:
    def __init__(self, *, legacy: bool = False) -> None:
        self.legacy = legacy
        self.services: list[FakeSearchService | LegacyFakeSearchService] = []

    def __call__(self, wiki_dir: Path) -> runner.SearchService:
        orientation_source = (
            "authored"
            if "authored" in {part.casefold() for part in wiki_dir.parts}
            else "projection_extractive"
        )
        service: FakeSearchService | LegacyFakeSearchService
        if self.legacy:
            service = LegacyFakeSearchService(orientation_source)
        else:
            service = FakeSearchService(orientation_source)
        self.services.append(service)
        return cast(runner.SearchService, service)


class SingletonFactory:
    def __init__(self) -> None:
        self.service = FakeSearchService("authored")

    def __call__(self, _wiki_dir: Path) -> runner.SearchService:
        return cast(runner.SearchService, self.service)


def result(page_id: str, score: float) -> dict[str, Any]:
    return {
        "page_id": page_id,
        "title": page_id.replace("_", " ").title(),
        "path": f"{page_id}.md",
        "score": score,
        "snippet": "",
        "role": "topic",
        "source_refs": [],
        "route": "",
    }


def test_runner_executes_all_deterministic_arms_with_external_plan() -> None:
    report = runner.run_agent_guided_lexical_benchmark(
        fixture_dir=FIXTURE_DIR,
        service_factory=RecordingFactory(),
        implementation_revision="git:" + ("1" * 40),
    )

    assert report["schema_id"] == runner.REPORT_SCHEMA_ID
    assert report["constraints"] == {
        "runner_calls_llm": False,
        "llm_call_count": 0,
        "variants_generated_from_qrels": False,
        "max_query_variants": 2,
        "source_qrels_separated": True,
        "original_page_citations_only": True,
        "public_superiority_claim": False,
        "raw_hybrid_requires_explicit_provider": True,
        "semantic_leakage_mechanically_proven": False,
        "release_evidence_requires_independent_source_only_plans_before_qrels": True,
        "fixture_is_release_evidence": False,
        "cold_cache_mechanically_verified": False,
        "service_instance_isolation_verified": True,
        "service_instance_isolation_method": runner.SERVICE_INSTANCE_ISOLATION_METHOD,
    }
    fixture = cast(dict[str, Any], report["fixture"])
    assert fixture["query_counts"] == {"total": 5, "evaluated": 4, "negative": 1}
    assert fixture["qrel_counts"] == {"total": 4, "positive": 4}

    arms = cast(dict[str, dict[str, object]], report["arms"])
    assert set(arms) == set(runner.ALL_ARMS)
    assert arms["authored_raw_lexical"]["status"] == "available"
    assert arms["authored_agent_guided_lexical"]["status"] == "available"
    assert arms["projection_raw_lexical"]["status"] == "available"
    assert arms["projection_sketch_agent_guided_lexical"]["status"] == "available"
    assert arms["authored_raw_hybrid"]["status"] == "skipped"
    assert arms["projection_raw_hybrid"]["status"] == "skipped"
    assert arms["authored_raw_hybrid"]["metrics"] is None
    assert arms["authored_raw_hybrid"]["latency_ms"] is None
    assert arms["authored_raw_hybrid"]["payload_bytes"] is None
    assert (
        arms["authored_agent_guided_lexical"]["retrieval_guidance_orientation_source"] == "authored"
    )
    assert (
        arms["projection_sketch_agent_guided_lexical"]["retrieval_guidance_orientation_source"]
        == "projection_extractive"
    )

    authored_usage = cast(dict[str, object], arms["authored_agent_guided_lexical"]["usage"])
    assert authored_usage["variant_count"] == 10
    assert authored_usage["public_search_requests"] == 5
    assert authored_usage["internal_lexical_channel_evaluations"] == 15
    assert authored_usage["adapter_search_calls"] == 5
    assert authored_usage["public_context_requests"] == 5
    assert authored_usage["cold_usage_cache"] is None
    assert authored_usage["cold_usage_cache_evidence"] == "unknown"
    assert authored_usage["cache_isolation"] == "unknown"
    assert authored_usage["service_instance_isolation_verified"] is True
    assert (
        authored_usage["service_instance_isolation_method"]
        == runner.SERVICE_INSTANCE_ISOLATION_METHOD
    )
    assert authored_usage["llm_calls"] == 0
    assert authored_usage["accounting_source"] == "manual-fixture-token-estimate"
    assert (
        cast(dict[str, float], arms["authored_agent_guided_lexical"]["metrics"])["Recall@5"] == 1.0
    )

    raw_usage = cast(dict[str, object], arms["authored_raw_lexical"]["usage"])
    assert raw_usage["public_search_requests"] == 5
    assert raw_usage["internal_lexical_channel_evaluations"] == 5
    assert raw_usage["variant_count"] == 0
    assert (
        cast(dict[str, float], arms["authored_raw_lexical"]["metrics"])[
            "negative_false_positive_rate@5"
        ]
        == 0.0
    )

    provenance = cast(dict[str, Any], report["provenance"])
    assert provenance["implementation"]["revision"] == "git:" + ("1" * 40)
    assert provenance["implementation"]["dirty"] in {True, False, None}
    assert provenance["plan"]["generator_kinds"] == ["human_fixture"]
    assert provenance["plan"]["stable_fixture_marker"] is True
    assert len(provenance["plan"]["source_context_digests"]) == 2

    per_query = cast(dict[str, list[dict[str, object]]], report["per_query"])
    assert set(per_query) == set(runner.ALL_ARMS)
    assert {row["query_id"] for row in per_query["authored_raw_lexical"]} == {
        "agl-en-001",
        "agl-ko-001",
        "agl-code-001",
        "agl-neg-001",
        "agl-adv-001",
    }
    assert per_query["authored_raw_hybrid"] == []
    assert per_query["projection_raw_hybrid"] == []

    runner.validate_runner_report(report)
    runner.validate_report_public_safety(report)


def test_runner_rejects_singleton_or_reused_service_factory() -> None:
    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="reused service instance"):
        runner.run_agent_guided_lexical_benchmark(
            fixture_dir=FIXTURE_DIR,
            service_factory=SingletonFactory(),
            implementation_revision="git:" + ("6" * 40),
        )


def test_cold_cache_status_is_unknown_and_skipped_usage_is_not_applicable() -> None:
    report = runner.run_agent_guided_lexical_benchmark(
        fixture_dir=FIXTURE_DIR,
        service_factory=RecordingFactory(),
        implementation_revision="git:" + ("7" * 40),
    )
    arms = cast(dict[str, dict[str, object]], report["arms"])
    available_usage = cast(dict[str, object], arms["authored_raw_lexical"]["usage"])
    skipped_usage = cast(dict[str, object], arms["authored_raw_hybrid"]["usage"])

    assert available_usage["cold_usage_cache"] is None
    assert available_usage["cold_usage_cache_evidence"] == "unknown"
    assert available_usage["cache_isolation"] == "unknown"
    assert available_usage["service_instance_isolation_verified"] is True
    assert skipped_usage == {
        "cold_usage_cache": None,
        "cold_usage_cache_evidence": "not-applicable",
        "cache_isolation": "not-applicable",
        "service_instance_isolation_verified": False,
        "service_instance_isolation_method": "not-applicable",
        "public_context_requests": 0,
        "public_search_requests": 0,
        "internal_lexical_channel_evaluations": 0,
        "adapter_search_calls": 0,
        "read_calls": 0,
        "variant_count": 0,
        "query_character_count": 0,
        "character_counting_method": "not-applicable",
        "token_count_source": "not-applicable",
        "input_tokens": 0,
        "output_tokens": 0,
        "accounting_source": "not-applicable",
        "llm_calls": 0,
    }


def test_runner_falls_back_to_channel_rrf_when_service_lacks_query_variants() -> None:
    factory = RecordingFactory(legacy=True)
    report = runner.run_agent_guided_lexical_benchmark(
        fixture_dir=FIXTURE_DIR,
        service_factory=factory,
        implementation_revision="git:" + ("2" * 40),
    )

    arms = cast(dict[str, dict[str, object]], report["arms"])
    authored_usage = cast(dict[str, object], arms["authored_agent_guided_lexical"]["usage"])
    assert authored_usage["public_search_requests"] == 5
    assert authored_usage["internal_lexical_channel_evaluations"] == 15
    assert authored_usage["adapter_search_calls"] == 15
    assert sum(len(service.calls) for service in factory.services) >= 30


def test_plan_validation_rejects_more_than_two_variants() -> None:
    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="maxItems is 2"):
        runner.validate_variant_list(
            ["one", "two", "three"],
            path=Path("agent-plan.jsonl"),
            line_number=1,
        )


def test_plan_validation_rejects_qrel_leak_fields(tmp_path: Path) -> None:
    plan = tmp_path / "agent-plan.jsonl"
    plan.write_text(
        json.dumps(
            {
                "schema_id": runner.PLAN_SCHEMA_ID,
                "query_id": "q",
                "arm": "authored_agent_guided_lexical",
                "primary_query": "query",
                "query_variants": ["variant"],
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "provenance": {"citation": "gold_page"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="must not contain qrel"):
        runner.load_agent_plan(plan)


def test_plan_validation_rejects_positive_qrel_identifier_matches() -> None:
    paths = runner.resolve_fixture_paths(FIXTURE_DIR)
    queries = runner.load_query_cases(paths.queries)
    qrels = runner.load_qrels(paths.qrels)
    plan_rows = runner.load_agent_plan(paths.agent_plan)
    corpus_pages: dict[runner.CorpusId, dict[str, runner.PageIdentity]] = {
        "authored": runner.page_identities_from_wiki(paths.authored_wiki),
        "projection": runner.page_identities_from_wiki(paths.projection_wiki),
    }
    bad_plan_rows = dict(plan_rows)
    key: tuple[str, runner.BenchmarkArm] = ("agl-en-001", "authored_agent_guided_lexical")
    bad_plan_rows[key] = replace(
        plan_rows[key],
        query_variants=("BILLING_REFUND_POLICY.md",),
    )

    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="positive qrel identifier"):
        runner.validate_no_positive_qrel_identifier_leakage(
            queries,
            plan_rows=bad_plan_rows,
            qrels=qrels,
            corpus_pages=corpus_pages,
        )


def test_qrel_validation_rejects_missing_unknown_and_unanswerable_qrels() -> None:
    pages: dict[runner.CorpusId, frozenset[str]] = {
        "authored": frozenset({"doc"}),
        "projection": frozenset({"doc"}),
    }
    answerable = (
        runner.QueryCase(
            query_id="q1",
            language="en",
            case="case",
            query="question",
            answerability="answerable",
        ),
    )
    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="positive qrel"):
        runner.validate_qrel_query_consistency(answerable, {}, pages)
    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="unknown query"):
        runner.validate_qrel_query_consistency(answerable, {"missing": {"doc": 1.0}}, pages)

    unknown = (
        runner.QueryCase(
            query_id="q1",
            language="en",
            case="case",
            query="question",
            answerability="unknown",
        ),
    )
    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="not evaluable"):
        runner.validate_qrel_query_consistency(unknown, {}, pages)

    unanswerable = (
        runner.QueryCase(
            query_id="q2",
            language="en",
            case="case",
            query="question",
            answerability="unanswerable",
        ),
    )
    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="must not have qrels"):
        runner.validate_qrel_query_consistency(unanswerable, {"q2": {"doc": 1.0}}, pages)


def test_qrel_loader_rejects_duplicate_rows_and_extra_keys(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate-qrels.jsonl"
    duplicate.write_text(
        "\n".join(
            [
                json.dumps({"query_id": "q", "page_id": "doc", "relevance": 1}),
                json.dumps({"query_id": "q", "page_id": "doc", "relevance": 0}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="duplicate qrel"):
        runner.load_qrels(duplicate)

    extra = tmp_path / "extra-qrels.jsonl"
    extra.write_text(
        json.dumps({"query_id": "q", "page_id": "doc", "relevance": 1, "note": "bad"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="extra"):
        runner.load_qrels(extra)


def test_page_identities_use_canonical_service_ids_and_reject_collisions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    (root / "nested").mkdir(parents=True)
    (root / "nested" / "topic.md").write_text(
        """---
id: canonical-topic
title: Canonical Topic
---
# Ignored Heading
""",
        encoding="utf-8",
    )

    identities = runner.page_identities_from_wiki(root)
    assert set(identities) == {"canonical-topic"}
    assert identities["canonical-topic"].path == "nested/topic.md"
    assert identities["canonical-topic"].title == "Canonical Topic"

    (root / "other.md").write_text(
        """---
id: canonical-topic
---
# Other
""",
        encoding="utf-8",
    )
    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="duplicate canonical"):
        runner.page_identities_from_wiki(root)


def test_fixture_artifacts_must_stay_outside_served_wikis(tmp_path: Path) -> None:
    authored = tmp_path / "authored" / "wiki"
    projection = tmp_path / "projection" / "wiki"
    authored.mkdir(parents=True)
    projection.mkdir(parents=True)
    (authored / "index.md").write_text("# Index\n", encoding="utf-8")
    (projection / "topic.md").write_text("# Topic\n", encoding="utf-8")
    (authored / "qrels.jsonl").write_text("", encoding="utf-8")
    paths = runner.FixturePaths(
        fixture_dir=tmp_path,
        queries=tmp_path / "queries.jsonl",
        qrels=authored / "qrels.jsonl",
        agent_plan=tmp_path / "agent-plan.jsonl",
        authored_wiki=authored,
        projection_wiki=projection,
    )

    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="outside served source"):
        runner.validate_fixture_separation(paths)


def test_projection_corpus_must_not_have_hot_index_or_overview(tmp_path: Path) -> None:
    authored = tmp_path / "authored" / "wiki"
    projection = tmp_path / "projection" / "wiki"
    authored.mkdir(parents=True)
    projection.mkdir(parents=True)
    (authored / "index.md").write_text("# Index\n", encoding="utf-8")
    (projection / "overview.md").write_text("# Overview\n", encoding="utf-8")
    paths = runner.FixturePaths(
        fixture_dir=tmp_path,
        queries=tmp_path / "queries.jsonl",
        qrels=tmp_path / "qrels.jsonl",
        agent_plan=tmp_path / "agent-plan.jsonl",
        authored_wiki=authored,
        projection_wiki=projection,
    )

    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="must not include"):
        runner.validate_corpus_layout(paths)


def test_unicode_and_code_identifier_channels_are_preserved() -> None:
    channels = runner.effective_channels(
        "AGLParser.resolve_symbol E_BRIDGE_TIMEOUT 고객",
        ["aglparser.resolve_symbol e_bridge_timeout 고객", "경로/wiki-index.md"],
    )

    assert channels == (
        "AGLParser.resolve_symbol E_BRIDGE_TIMEOUT 고객",
        "경로/wiki-index.md",
    )


def test_public_safety_rejects_private_paths_and_superiority_claims() -> None:
    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="superiority"):
        runner.validate_report_public_safety({"claim": "This outperforms BM25."})

    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="private"):
        runner.validate_report_public_safety({"path": r"C:\Users\example-user\secret.txt"})


def test_prompt_injection_fixture_text_is_not_published_in_report() -> None:
    source_text = (FIXTURE_DIR / "authored" / "wiki" / "prompt_injection_safety.md").read_text(
        encoding="utf-8"
    )
    assert "ignore previous instructions" in source_text

    report = runner.run_agent_guided_lexical_benchmark(
        fixture_dir=FIXTURE_DIR,
        service_factory=RecordingFactory(),
        implementation_revision="git:" + ("3" * 40),
    )
    serialized = json.dumps(report, ensure_ascii=False)
    assert "ignore previous instructions" not in serialized
    assert "prompt_injection_safety" in serialized


def test_original_page_id_validator_rejects_synthetic_citations() -> None:
    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="non-source page ids"):
        runner.validate_original_page_ids(["orientation:index"], frozenset({"index"}))


def test_report_runtime_validation_rejects_extra_properties() -> None:
    report = runner.run_agent_guided_lexical_benchmark(
        fixture_dir=FIXTURE_DIR,
        service_factory=RecordingFactory(),
        implementation_revision="git:" + ("4" * 40),
    )
    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="extra"):
        runner.validate_runner_report({**report, "extra": True})

    bad_report = dict(report)
    arms = cast(dict[str, Any], bad_report["arms"]).copy()
    arm_payload = dict(arms["authored_raw_lexical"])
    usage = dict(arm_payload["usage"])
    usage["search_calls"] = 5
    arm_payload["usage"] = usage
    arms["authored_raw_lexical"] = arm_payload
    bad_report["arms"] = arms
    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="extra"):
        runner.validate_runner_report(bad_report)


def test_report_runtime_validation_rejects_mixed_skipped_and_available_shapes() -> None:
    report = runner.run_agent_guided_lexical_benchmark(
        fixture_dir=FIXTURE_DIR,
        service_factory=RecordingFactory(),
        implementation_revision="git:" + ("8" * 40),
    )

    skipped_with_metrics = dict(report)
    arms = cast(dict[str, Any], skipped_with_metrics["arms"]).copy()
    skipped_payload = dict(arms["authored_raw_hybrid"])
    skipped_payload["metrics"] = runner.empty_metrics()
    arms["authored_raw_hybrid"] = skipped_payload
    skipped_with_metrics["arms"] = arms
    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="metrics must be null"):
        runner.validate_runner_report(skipped_with_metrics)

    skipped_with_rows = dict(report)
    per_query = cast(dict[str, Any], skipped_with_rows["per_query"]).copy()
    per_query["authored_raw_hybrid"] = [
        {
            "query_id": "agl-en-001",
            "answerability": "answerable",
            "ranked_page_ids": [],
            "public_search_requests": 0,
            "internal_lexical_channel_evaluations": 0,
            "adapter_search_calls": 0,
            "query_variant_count": 0,
        }
    ]
    skipped_with_rows["per_query"] = per_query
    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="empty when skipped"):
        runner.validate_runner_report(skipped_with_rows)

    available_without_metrics = dict(report)
    arms = cast(dict[str, Any], available_without_metrics["arms"]).copy()
    available_payload = dict(arms["authored_raw_lexical"])
    available_payload["metrics"] = None
    arms["authored_raw_lexical"] = available_payload
    available_without_metrics["arms"] = arms
    with pytest.raises(runner.AgentGuidedLexicalRunnerError, match="metrics must be an object"):
        runner.validate_runner_report(available_without_metrics)


def test_bundled_json_schema_validates_report() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(Path("benchmarks/agent_guided_lexical/report.schema.json").read_text())
    report = runner.run_agent_guided_lexical_benchmark(
        fixture_dir=FIXTURE_DIR,
        service_factory=RecordingFactory(),
        implementation_revision="git:" + ("5" * 40),
    )

    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(report, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**report, "extra": True}, schema)

    skipped_with_metrics = dict(report)
    arms = cast(dict[str, Any], skipped_with_metrics["arms"]).copy()
    skipped_payload = dict(arms["authored_raw_hybrid"])
    skipped_payload["metrics"] = runner.empty_metrics()
    arms["authored_raw_hybrid"] = skipped_payload
    skipped_with_metrics["arms"] = arms
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(skipped_with_metrics, schema)

    skipped_with_rows = dict(report)
    per_query = cast(dict[str, Any], skipped_with_rows["per_query"]).copy()
    per_query["authored_raw_hybrid"] = [
        {
            "query_id": "agl-en-001",
            "answerability": "answerable",
            "ranked_page_ids": [],
            "public_search_requests": 0,
            "internal_lexical_channel_evaluations": 0,
            "adapter_search_calls": 0,
            "query_variant_count": 0,
        }
    ]
    skipped_with_rows["per_query"] = per_query
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(skipped_with_rows, schema)
