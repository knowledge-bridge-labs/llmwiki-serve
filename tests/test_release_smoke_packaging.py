from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from scripts import release_smoke

PROJECT_ROOT = Path(__file__).resolve().parents[1]

AGENT_GUIDED_LEXICAL_SDIST_FILES = frozenset(
    {
        "benchmarks/agent_guided_lexical/README.md",
        "benchmarks/agent_guided_lexical/fixture/agent-plan.jsonl",
        "benchmarks/agent_guided_lexical/fixture/authored/wiki/billing_refund_policy.md",
        "benchmarks/agent_guided_lexical/fixture/authored/wiki/code_identifier_reference.md",
        "benchmarks/agent_guided_lexical/fixture/authored/wiki/hot.md",
        "benchmarks/agent_guided_lexical/fixture/authored/wiki/index.md",
        "benchmarks/agent_guided_lexical/fixture/authored/wiki/irrelevant_distractor.md",
        "benchmarks/agent_guided_lexical/fixture/authored/wiki/korean_refund_policy.md",
        "benchmarks/agent_guided_lexical/fixture/authored/wiki/prompt_injection_safety.md",
        "benchmarks/agent_guided_lexical/fixture/projection/wiki/billing_refund_policy.md",
        "benchmarks/agent_guided_lexical/fixture/projection/wiki/code_identifier_reference.md",
        "benchmarks/agent_guided_lexical/fixture/projection/wiki/irrelevant_distractor.md",
        "benchmarks/agent_guided_lexical/fixture/projection/wiki/korean_refund_policy.md",
        "benchmarks/agent_guided_lexical/fixture/projection/wiki/prompt_injection_safety.md",
        "benchmarks/agent_guided_lexical/fixture/qrels.jsonl",
        "benchmarks/agent_guided_lexical/fixture/queries.jsonl",
        "benchmarks/agent_guided_lexical/gates.json",
        "benchmarks/agent_guided_lexical/report.schema.json",
    }
)


def test_hatch_sdist_policy_ships_agent_guided_fixture_not_generated_outputs() -> None:
    metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sdist_policy = metadata["tool"]["hatch"]["build"]["targets"]["sdist"]

    assert "/benchmarks/agent_guided_lexical" in set(sdist_policy["include"])
    assert {
        "/.runtime-logs",
        "/benchmarks/**/report-output",
        "/benchmarks/**/report-outputs",
        "/benchmarks/**/reports",
        "/**/.llmwiki-work",
        "/**/.runtime-logs",
    } <= set(sdist_policy["exclude"])


def test_release_smoke_expects_agent_guided_fixture_schema_and_gates() -> None:
    assert AGENT_GUIDED_LEXICAL_SDIST_FILES <= release_smoke.EXPECTED_SDIST_FILES
    assert not any(
        path.startswith("benchmarks/agent_guided_lexical/reports/")
        for path in release_smoke.EXPECTED_SDIST_FILES
    )
    assert not any(
        path.endswith(("-report.json", "_report.json"))
        for path in release_smoke.EXPECTED_SDIST_FILES
    )


@pytest.mark.parametrize("path", sorted(AGENT_GUIDED_LEXICAL_SDIST_FILES))
def test_release_smoke_allows_safe_agent_guided_harness_paths(path: str) -> None:
    assert release_smoke.forbidden_sdist_path_reason(path) is None


@pytest.mark.parametrize(
    "path, expected_reason",
    [
        (
            "benchmarks/agent_guided_lexical/reports/local-report.json",
            "cache, VCS, build, or generated artifact path",
        ),
        (
            "benchmarks/agent_guided_lexical/report-output/run.json",
            "cache, VCS, build, or generated artifact path",
        ),
        (
            "benchmarks/agent_guided_lexical/fixture/.llmwiki-work/index.json",
            "cache, VCS, build, or generated artifact path",
        ),
        (
            ".runtime-logs/agent-guided-lexical-smoke.json",
            "cache, VCS, build, or generated artifact path",
        ),
        (
            "benchmarks/agent_guided_lexical/fixture/cache/vectors.json",
            "cache, VCS, build, or generated artifact path",
        ),
        (
            "benchmarks/agent_guided_lexical/fixture/private-artifacts/notes.md",
            "cache, VCS, build, or generated artifact path",
        ),
        ("benchmarks/agent_guided_lexical/local-smoke.json", "generated report output"),
        ("benchmarks/agent_guided_lexical/run_report.json", "generated report output"),
    ],
)
def test_release_smoke_rejects_generated_runtime_and_private_sdist_paths(
    path: str,
    expected_reason: str,
) -> None:
    assert release_smoke.forbidden_sdist_path_reason(path) == expected_reason
