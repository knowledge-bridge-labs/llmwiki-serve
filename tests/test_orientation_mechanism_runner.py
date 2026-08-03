from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from scripts.benchmark_adapters import orientation_mechanism_runner as runner


class FakeOrientationProvider:
    provider_id = "fake"
    model_id = "fake-multilingual-orientation"
    model_revision = "fake-revision-1"
    dimension = 5
    distance_metric: Literal["cosine"] = "cosine"

    def __init__(self) -> None:
        self.document_calls = 0
        self.query_calls = 0
        self.pairs = [
            ("customer overcharge reversal", [1.0, 0.0, 0.0, 0.0, 0.0]),
            ("billing_refund_policy", [1.0, 0.0, 0.0, 0.0, 0.0]),
            ("reimbursement approval", [1.0, 0.0, 0.0, 0.0, 0.0]),
            ("overcharge glossary", [0.95, 0.0, 0.0, 0.0, 0.0]),
            ("팀 에이전트가 같은 위키", [0.0, 1.0, 0.0, 0.0, 0.0]),
            ("korean_shared_wiki", [0.0, 1.0, 0.0, 0.0, 0.0]),
            ("여러 원격 위키", [0.0, 0.0, 1.0, 0.0, 0.0]),
            ("remote_multi_source_ko", [0.0, 0.0, 1.0, 0.0, 0.0]),
            ("release.v1-beta.md", [0.0, 0.0, 0.0, 1.0, 0.0]),
            ("release v1 beta", [0.0, 0.0, 0.0, 1.0, 0.0]),
            ("retention guardrails", [0.0, 0.0, 0.0, 0.0, 1.0]),
            ("retention_policy", [0.0, 0.0, 0.0, 0.0, 1.0]),
            ("generic hub cap", [0.0, 0.0, 0.2, 0.0, 0.0]),
            ("boilerplate_safety", [0.0, 0.0, 0.2, 0.0, 0.0]),
            ("malicious_relation_target", [0.2, 0.2, 0.2, 0.0, 0.0]),
        ]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.document_calls += 1
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
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
        for keyword, vector in self.pairs:
            if keyword.casefold() in lowered:
                return vector
        return [0.1, 0.1, 0.1, 0.1, 0.1]


def test_orientation_mechanism_runner_fake_provider_contract(tmp_path: Path) -> None:
    provider = FakeOrientationProvider()
    report_path = tmp_path / "orientation-report.json"

    report = runner.run_orientation_mechanism_benchmark(
        fixture_dir=Path("benchmarks/orientation_mechanism/fixture"),
        output_report=report_path,
        vector_cache_root=tmp_path / "vector-cache",
        vector_provider=provider,
        implementation_revision="git:" + "1" * 40,
    )

    assert report_path.is_file()
    written = json.loads(report_path.read_text(encoding="utf-8"))
    assert written == report
    runner.validate_orientation_report(report)
    assert report["schema_id"] == runner.REPORT_SCHEMA_ID
    assert report["benchmark_class"] == runner.BENCHMARK_CLASS
    assert report["query_count"] == 19
    assert report["languages_evaluated"] == ["en", "ko"]
    assert list(report["metrics"]) == list(runner.SEARCH_MODES)
    assert report["tested_size_envelope"]["synthetic_results_authority"] == "non-authoritative"
    assert report["tested_size_envelope"]["adversarial_query_count"] == 9
    assert (
        report["retrieval_configuration"]["exact_search_backend"]["name"]
        == "builtin-lexical-exact-compound-and-metadata"
    )
    assert provider.document_calls >= 1
    assert provider.query_calls >= len(runner.SEARCH_MODES)

    assertions = report["case_assertions"]
    assert "ori-en-001" in assertions["orientation_link_lift_query_ids"]
    assert "ori-ko-001" in assertions["orientation_link_lift_query_ids"]
    assert assertions["exact_identifier_rank1_query_ids"] == ["ori-en-004"]
    assert assertions["approved_mode_draft_leak_query_ids"] == []
    assert assertions["missing_orientation_fallback"]["exact_order_and_score_match"] is True

    diagnostics = report["orientation_diagnostics"]
    assert diagnostics["orientation_seeded_queries"] >= 1
    assert diagnostics["total_related_page_count"] >= 1
    assert len(diagnostics["per_query"]) == report["query_count"]
    assert {
        "query_id",
        "case",
        "mode",
        "orientation_seed_count",
        "related_page_count",
        "fallback_reason",
        "candidate_depth",
    } <= set(diagnostics["per_query"][0])

    robustness = report["robustness_gates"]
    assert robustness["gate"] == "orientation-adversarial-public-safe-v1"
    assert (
        len(
            robustness["passed_query_ids"]
            + robustness["failed_query_ids"]
            + robustness["residual_risk_query_ids"]
        )
        == 9
    )
    adversarial = {item["case"]: item for item in assertions["adversarial_orientation_results"]}
    assert set(adversarial) == runner.ADVERSARIAL_CASES
    assert adversarial["high_degree_generic_hub"]["status"] == "pass"
    assert adversarial["stale_deleted_link_target"]["status"] == "pass"
    exact_poisoned = adversarial["exact_identifier_poisoned_hints"]
    assert exact_poisoned["status"] == "pass"
    assert exact_poisoned["observed"]["target_rank_hybrid"] == 1
    assert "exact_identifier_preserved" in exact_poisoned["invariants"]
    assert "orientation_result_suppressed" in exact_poisoned["invariants"]
    assert exact_poisoned["failures"] == []
    assert exact_poisoned["observed"]["orientation_page_ids_returned"] == []
    assert robustness["production_code_blocker_query_ids"] == []
    assert adversarial["draft_private_target"]["status"] == "pass"
    assert adversarial["duplicate_alias_links"]["status"] == "pass"
    assert adversarial["explicit_malicious_distractor_link"]["status"] in {
        "pass",
        "residual_risk",
    }
    assert adversarial["malicious_tag_source_ref"]["status"] in {"pass", "residual_risk"}
    if adversarial["prompt_injection_like_prose"]["status"] == "fail":
        assert "ori-adv-en-003" in robustness["production_code_blocker_query_ids"]
    if adversarial["korean_nfc_nfd_label"]["status"] == "fail":
        assert "ori-adv-ko-001" in robustness["production_code_blocker_query_ids"]

    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert "How should we reverse a customer overcharge?" not in encoded
    assert "Reimbursement approval" not in encoded
    assert str(tmp_path) not in encoded


def test_orientation_report_validator_rejects_authoritative_or_pathy_reports(
    tmp_path: Path,
) -> None:
    provider = FakeOrientationProvider()
    report = runner.run_orientation_mechanism_benchmark(
        fixture_dir=Path("benchmarks/orientation_mechanism/fixture"),
        output_report=tmp_path / "valid.json",
        vector_cache_root=tmp_path / "vector-cache",
        vector_provider=provider,
        implementation_revision="git:" + "2" * 40,
    )

    authoritative = dict(report)
    authoritative["notice"] = "External retrieval quality headline."
    try:
        runner.validate_orientation_report(authoritative)
    except runner.OrientationBenchmarkError as exc:
        assert "non-authoritative" in str(exc)
    else:
        raise AssertionError("authoritative-looking report should be rejected")

    pathy = dict(report)
    pathy["local_path"] = r"C:\Users\example\secret"
    try:
        runner.validate_orientation_report(pathy)
    except runner.OrientationBenchmarkError as exc:
        assert "private-looking" in str(exc)
    else:
        raise AssertionError("private-looking report should be rejected")
