from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from typer.testing import CliRunner

import llmwiki_serve.query_action_judgment as qaj
from llmwiki_serve.api import create_app
from llmwiki_serve.cli import app as cli_app
from llmwiki_serve.models import ContextPack
from llmwiki_serve.query_action_judgment import (
    QueryActionJudgeConfig,
    QueryActionJudgmentError,
)
from llmwiki_serve.service import LlmWikiService

FIXTURE = Path(__file__).parent / "fixtures" / "sample-wiki"
PATH_CANARY = "Z:" + "\\fixture\\private-root"
SECRET_CANARY = "api_" + "key=" + "visible_" + "secret_value"
URL_CANARY = "http://" + "192.0." + "2.10:8080/admin"
EMAIL_CANARY = "person@" + "example.invalid"


def test_query_action_guidance_default_off_keeps_context_contract(
    monkeypatch: Any,
) -> None:
    clear_query_action_judge_env(monkeypatch)
    monkeypatch.setattr(
        qaj,
        "call_system_one_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider called")),
    )
    expected_keys = {
        "query",
        "wiki_title",
        "description",
        "adapter",
        "implementation",
        "page_count",
        "approved_page_count",
        "answerable",
        "orientation",
        "evidence",
        "limitations",
        "graph",
        "retrieval_guidance",
    }

    context = LlmWikiService(FIXTURE).context("required copy release readiness", limit=4)
    client = TestClient(create_app(FIXTURE))
    http_context = client.post(
        "/query",
        json={"query": "required copy release readiness", "limit": 4},
    ).json()
    mcp_context = mcp_tool_call(
        client,
        "llmwiki_context",
        {"query": "required copy release readiness", "limit": 4},
    )
    cli_result = CliRunner().invoke(
        cli_app,
        ["query", str(FIXTURE), "required copy release readiness", "--limit", "4"],
    )

    assert set(context.model_dump()) == expected_keys
    assert "retrieval_action_guidance" not in http_context
    assert "retrieval_action_guidance" not in mcp_context
    assert cli_result.exit_code == 0, cli_result.output
    assert "retrieval_action_guidance" not in json.loads(cli_result.output)


def test_query_action_guidance_omits_only_new_none_field() -> None:
    context = ContextPack(
        query="manual",
        wiki_title="Manual",
        description="",
        adapter="generic-markdown",
        implementation="generic-markdown",
        page_count=0,
        approved_page_count=0,
        answerable=False,
    )

    payload = context.model_dump()
    json_payload = json.loads(context.model_dump_json())

    assert "retrieval_guidance" in payload
    assert payload["retrieval_guidance"] is None
    assert "retrieval_action_guidance" not in payload
    assert "retrieval_guidance" in json_payload
    assert json_payload["retrieval_guidance"] is None
    assert "retrieval_action_guidance" not in json_payload


def test_query_action_guidance_enabled_runs_after_evidence_and_returns_guidance(
    monkeypatch: Any,
) -> None:
    captured: list[dict[str, Any]] = []

    def fake_provider(
        payload: dict[str, Any],
        config: QueryActionJudgeConfig,
    ) -> dict[str, Any]:
        captured.append({"payload": payload, "config": config})
        return {
            "answers": {
                "next_action": {"type": "choice", "choice": "read", "confidence": 0.91},
                "evidence_sufficiency_score": {"type": "score", "score": 0.82},
            }
        }

    monkeypatch.setattr(qaj, "call_system_one_provider", fake_provider)

    service = LlmWikiService(
        FIXTURE,
        query_action_judge=QueryActionJudgeConfig(
            mode="system-one",
            endpoint="http://provider.test/systemone",
            api_key="test-provider-key",
        ),
    )
    context = service.context("required copy release readiness", limit=4)
    guidance = context.retrieval_action_guidance

    assert captured
    assert captured[0]["payload"]["model"] == "jev-latest"
    assert captured[0]["payload"]["state"]["result"]["evidence_count"] == len(context.evidence)
    assert captured[0]["payload"]["state"]["result"]["retrieval_guidance_present"] is True
    assert captured[0]["payload"]["questions"]["evidence_sufficiency_score"]["criteria"] == [
        "0 means the result is absent, weak, contradictory, or needs more retrieval.",
        "1 means the result is likely sufficient to stop retrieval for now.",
    ]
    assert guidance is not None
    assert guidance.schema_version == "llmwiki.retrieval_action_guidance.v1"
    assert guidance.mode == "system_one"
    assert guidance.status == "ok"
    assert guidance.recommended_action == "read"
    assert guidance.confidence == 0.91
    assert guidance.evidence_sufficiency_score == 0.82
    assert guidance.read_page_ids
    assert guidance.read_page_ids[0] == context.evidence[0].page_id
    assert guidance.diagnostics == []


def test_query_action_guidance_enabled_without_key_is_unconfigured(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        qaj,
        "call_system_one_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("provider called")),
    )

    context = LlmWikiService(
        FIXTURE,
        query_action_judge=QueryActionJudgeConfig(mode="system-one"),
    ).context("required copy release readiness", limit=4)
    guidance = context.retrieval_action_guidance

    assert guidance is not None
    assert guidance.status == "unconfigured"
    assert guidance.diagnostics == ["missing_api_key"]


def test_query_action_guidance_provider_payload_is_masked_and_minimized(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(
        root / "index.md",
        f"""
---
wiki_title: Masking Fixture
review_state: approved
source_refs: [SRC-SECRET]
---
# Masking Fixture

zzactionneedle approved page. {PATH_CANARY}
{SECRET_CANARY} {URL_CANARY} {EMAIL_CANARY}
""",
    )
    write_markdown(
        root / "draft.md",
        """
---
draft: true
source_refs: [SRC-DRAFT-SECRET]
---
# Draft

zzdraftsecret should never appear.
""",
    )
    captured: list[dict[str, Any]] = []

    def fake_provider(
        payload: dict[str, Any],
        _config: QueryActionJudgeConfig,
    ) -> dict[str, Any]:
        captured.append(payload)
        return {
            "answers": {
                "next_action": {"type": "choice", "choice": "graph", "confidence": 0.73},
                "evidence_sufficiency_score": {"type": "score", "score": 0.41},
            }
        }

    monkeypatch.setattr(qaj, "call_system_one_provider", fake_provider)

    context = LlmWikiService(
        root,
        query_action_judge=QueryActionJudgeConfig(mode="system-one", api_key="test-key"),
    ).context(
        f"zzactionneedle {PATH_CANARY} {SECRET_CANARY} {URL_CANARY} {EMAIL_CANARY}",
        limit=2,
    )
    payload = captured[0]
    serialized = json.dumps(payload, ensure_ascii=False)

    assert context.retrieval_action_guidance is not None
    assert payload["state"]["data_minimized"] is True
    assert set(payload["state"]["evidence"][0]) == {
        "ordinal",
        "role",
        "route",
        "score_bucket",
        "snippet_character_count",
        "source_ref_count",
        "path_depth",
        "query_overlap_ratio",
    }
    assert PATH_CANARY not in serialized
    assert SECRET_CANARY.split("=", 1)[1] not in serialized
    assert URL_CANARY not in serialized
    assert EMAIL_CANARY not in serialized
    assert "SRC-SECRET" not in serialized
    assert "SRC-DRAFT-SECRET" not in serialized
    assert "zzdraftsecret" not in serialized
    assert "index.md" not in serialized


def test_query_action_guidance_provider_failure_fails_open_with_sanitized_diagnostic(
    monkeypatch: Any,
) -> None:
    baseline = LlmWikiService(FIXTURE).context(
        "required copy release readiness",
        limit=4,
    )

    def failing_provider(
        _payload: dict[str, Any],
        _config: QueryActionJudgeConfig,
    ) -> dict[str, Any]:
        raise QueryActionJudgmentError(f"failed at {PATH_CANARY} with {SECRET_CANARY}")

    monkeypatch.setattr(qaj, "call_system_one_provider", failing_provider)

    context = LlmWikiService(
        FIXTURE,
        query_action_judge=QueryActionJudgeConfig(mode="system-one", api_key="test-key"),
    ).context("required copy release readiness", limit=4)
    guidance = context.retrieval_action_guidance
    serialized = context.model_dump_json()

    assert context.answerable == baseline.answerable
    assert [item.page_id for item in context.evidence] == [
        item.page_id for item in baseline.evidence
    ]
    assert [item.page_id for item in context.orientation] == [
        item.page_id for item in baseline.orientation
    ]
    assert context.limitations == baseline.limitations
    assert context.retrieval_guidance == baseline.retrieval_guidance
    assert guidance is not None
    assert guidance.status == "failed"
    assert guidance.diagnostics == ["provider_failed"]
    assert PATH_CANARY not in serialized
    assert SECRET_CANARY.split("=", 1)[1] not in serialized


def test_query_action_guidance_enabled_surfaces_through_http_and_mcp(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        qaj,
        "call_system_one_provider",
        lambda _payload, _config: {
            "answers": {
                "next_action": {"type": "choice", "choice": "search", "confidence": 0.64},
                "evidence_sufficiency_score": {"type": "score", "score": 0.37},
            }
        },
    )
    client = TestClient(
        create_app(
            FIXTURE,
            query_action_judge=QueryActionJudgeConfig(
                mode="system-one",
                api_key="test-provider-key",
            ),
        )
    )

    http_context = client.post(
        "/query",
        json={"query": "required copy release readiness", "limit": 4},
    ).json()
    mcp_context = mcp_tool_call(
        client,
        "llmwiki_context",
        {"query": "required copy release readiness", "limit": 4},
    )

    assert http_context["retrieval_action_guidance"]["recommended_action"] == "search"
    assert http_context["retrieval_action_guidance"]["search_queries"] == [
        "required copy release readiness"
    ]
    assert mcp_context["retrieval_action_guidance"]["recommended_action"] == "search"


def clear_query_action_judge_env(monkeypatch: Any) -> None:
    for name in (
        "LLMWIKI_QUERY_ACTION_JUDGE",
        "LLMWIKI_QUERY_ACTION_JUDGE_API_KEY",
        "LLMWIKI_QUERY_ACTION_JUDGE_ENDPOINT",
        "LLMWIKI_QUERY_ACTION_JUDGE_BASE_URL",
        "LLMWIKI_QUERY_ACTION_JUDGE_MODEL",
        "LLMWIKI_QUERY_ACTION_JUDGE_TIMEOUT_MS",
        "TYPESAFE_API_KEY",
        "JEV_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def write_markdown(path: Path, content: str) -> None:
    path.write_text(content.strip() + "\n", encoding="utf-8")


def mcp_tool_call(client: TestClient, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    payload = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    ).json()

    assert "error" not in payload
    return payload["result"]
