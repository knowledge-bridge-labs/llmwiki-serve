from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, NoReturn, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from llmwiki_serve.api import MCP_STREAM_PATH, create_app
from llmwiki_serve.errors import LlmWikiUserError
from llmwiki_serve.guided_retrieval import (
    AGENT_GUIDED_LEXICAL_CAPABILITY,
    build_retrieval_guidance,
)
from llmwiki_serve.models import (
    FolderCard,
    PageCard,
    RetrievalGuidance,
    SearchMode,
    WikiIndex,
    WikiPage,
)
from llmwiki_serve.service import LlmWikiService
from llmwiki_serve.vector import VectorConfig, VectorSearchError

FIXTURE = Path(__file__).parent / "fixtures" / "sample-wiki"
GUIDANCE_FIELDS = [
    "schema_version",
    "orientation_source",
    "content_trust",
    "max_query_variants",
    "character_budget",
    "folder_cards",
    "page_cards",
    "suggested_terms",
    "exact_identifiers",
    "fallback_modes",
]
FOLDER_CARD_FIELDS = ["path", "page_count", "terms"]
PAGE_CARD_FIELDS = [
    "page_id",
    "title",
    "path",
    "headings",
    "terms",
    "exact_identifiers",
    "excerpt",
]


class GuidedFakeEmbeddingProvider:
    provider_id = "fake"
    model_id = "fake-guidance-model"
    model_revision = "fake-revision"
    dimension = 3
    distance_metric: Literal["cosine"] = "cosine"

    def __init__(self) -> None:
        self.document_calls = 0
        self.query_calls = 0

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.document_calls += 1
        return [[1.0, 0.0, 0.0] for _text in texts]

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return [1.0, 0.0, 0.0]

    def safe_metadata(self) -> dict[str, str | int]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "dimension": self.dimension,
            "distance_metric": self.distance_metric,
        }


def test_capability_is_advertised_on_runtime_surfaces() -> None:
    service = LlmWikiService(FIXTURE)
    client = TestClient(create_app(FIXTURE))

    assert AGENT_GUIDED_LEXICAL_CAPABILITY in service.manifest().capabilities
    assert AGENT_GUIDED_LEXICAL_CAPABILITY in service.source_bundle().capabilities
    assert AGENT_GUIDED_LEXICAL_CAPABILITY in client.get("/health").json()["capabilities"]
    assert AGENT_GUIDED_LEXICAL_CAPABILITY in client.get("/manifest").json()["capabilities"]
    assert AGENT_GUIDED_LEXICAL_CAPABILITY in client.get("/source-bundle").json()["capabilities"]

    tools = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ).json()["result"]["tools"]
    descriptions = {tool["name"]: tool["description"] for tool in tools}

    assert "retrieval_guidance" in descriptions["llmwiki_context"]
    assert "query_variants" in descriptions["llmwiki_search"]


def test_context_emits_closed_authored_retrieval_guidance() -> None:
    context = LlmWikiService(FIXTURE).context("required copy release readiness", limit=4)
    guidance = context.retrieval_guidance
    assert guidance is not None

    payload = guidance.model_dump(mode="json")
    assert set(payload) == {
        "schema_version",
        "orientation_source",
        "content_trust",
        "max_query_variants",
        "character_budget",
        "folder_cards",
        "page_cards",
        "suggested_terms",
        "exact_identifiers",
        "fallback_modes",
    }
    assert payload["schema_version"] == "llmwiki.retrieval_guidance.v1"
    assert payload["orientation_source"] == "authored"
    assert payload["content_trust"] == "untrusted_source_evidence"
    assert payload["max_query_variants"] == 2
    assert payload["character_budget"] <= 6000
    assert payload["fallback_modes"] == ["literal"]
    assert payload["page_cards"]
    assert all("pageId" not in item for item in payload["page_cards"])
    assert "frontmatter" not in json.dumps(payload)
    assert "projection_digest" not in json.dumps(payload)

    with pytest.raises(ValidationError):
        RetrievalGuidance.model_validate({**payload, "diagnostics": {}})
    with pytest.raises(ValidationError):
        RetrievalGuidance.model_validate({**payload, "suggested_terms": None})


def test_retrieval_guidance_schema_requires_exact_fields_and_serializes_constants() -> None:
    guidance_schema = RetrievalGuidance.model_json_schema()
    folder_schema = FolderCard.model_json_schema()
    page_schema = PageCard.model_json_schema()

    assert guidance_schema["required"] == GUIDANCE_FIELDS
    assert set(guidance_schema["properties"]) == set(GUIDANCE_FIELDS)
    assert guidance_schema["properties"]["schema_version"]["const"] == (
        "llmwiki.retrieval_guidance.v1"
    )
    assert guidance_schema["properties"]["content_trust"]["const"] == ("untrusted_source_evidence")
    assert guidance_schema["properties"]["max_query_variants"]["const"] == 2
    assert folder_schema["required"] == FOLDER_CARD_FIELDS
    assert set(folder_schema["properties"]) == set(FOLDER_CARD_FIELDS)
    assert page_schema["required"] == PAGE_CARD_FIELDS
    assert set(page_schema["properties"]) == set(PAGE_CARD_FIELDS)

    guidance = RetrievalGuidance(
        schema_version="llmwiki.retrieval_guidance.v1",
        orientation_source="none",
        content_trust="untrusted_source_evidence",
        max_query_variants=2,
        character_budget=1,
        folder_cards=[],
        page_cards=[],
        suggested_terms=[],
        exact_identifiers=[],
        fallback_modes=[],
    )
    expected = {
        "schema_version": "llmwiki.retrieval_guidance.v1",
        "orientation_source": "none",
        "content_trust": "untrusted_source_evidence",
        "max_query_variants": 2,
        "character_budget": 1,
        "folder_cards": [],
        "page_cards": [],
        "suggested_terms": [],
        "exact_identifiers": [],
        "fallback_modes": [],
    }

    assert guidance.model_dump(mode="json") == expected
    assert guidance.model_dump(mode="json", exclude_unset=True) == expected

    projected_context = LlmWikiService(FIXTURE).context(
        "required copy",
        fields=["page_id"],
    )
    projected_payload = projected_context.model_dump(mode="json", exclude_unset=True)
    projected_guidance = cast(dict[str, Any], projected_payload["retrieval_guidance"])
    assert projected_guidance["schema_version"] == "llmwiki.retrieval_guidance.v1"
    assert projected_guidance["content_trust"] == "untrusted_source_evidence"
    assert projected_guidance["max_query_variants"] == 2


def test_generic_markdown_projection_extractive_guidance_is_zero_write_and_visibility_safe(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plain-notes"
    root.mkdir()
    write_markdown(
        root / "refund-policy.md",
        """
---
title: Refund Policy
review_state: approved
source_refs: ["SUPPORT-42"]
---
# Refund Policy

BillingRefundHandler checks refund.v1-beta.md before approving customer refunds.
""",
    )
    write_markdown(
        root / "draft-secret.md",
        """
---
title: Draft Secret
review_state: draft
---
# Draft Secret

zzdraftonly private endpoint http://127.0.0.1:9200 and password=secret.
""",
    )
    before = tree_snapshot(root)

    context = LlmWikiService(root).context("refund handler", limit=3)
    guidance = context.retrieval_guidance
    assert guidance is not None
    payload = guidance.model_dump(mode="json")
    encoded = json.dumps(payload)

    assert payload["orientation_source"] == "projection_extractive"
    assert payload["page_cards"][0]["page_id"] == "refund-policy"
    assert "BillingRefundHandler" in encoded
    assert "refund.v1-beta.md" in encoded
    assert "draft-secret" not in encoded
    assert "127.0.0.1" not in encoded
    assert str(root) not in encoded
    assert before == tree_snapshot(root)


def test_nested_quickstart_topic_does_not_count_as_authored_orientation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plain-notes"
    (root / "guide").mkdir(parents=True)
    write_markdown(
        root / "guide" / "quickstart.md",
        """
# Nested Quickstart

NestedQuickstartSymbol explains local onboarding.
""",
    )
    write_markdown(
        root / "topic.md",
        """
# Topic

Topic content.
""",
    )

    service = LlmWikiService(root)
    roles = {page.path: page.role for page in service.index().pages}
    context = service.context("NestedQuickstartSymbol")

    assert roles["guide/quickstart.md"] == "topic"
    assert context.retrieval_guidance is not None
    assert context.retrieval_guidance.orientation_source == "projection_extractive"


def test_guidance_omits_adversarial_private_values_and_overlong_paths(tmp_path: Path) -> None:
    overlong_path = f"unsafe/{'x' * 1030}.md"
    index = WikiIndex(
        root=tmp_path,
        title="Adversarial Privacy",
        adapter="generic-markdown",
        pages=[
            WikiPage(
                id="safe",
                title="Safe Release",
                path="safe/release.md",
                role="topic",
                text=(
                    "# Safe Release\n\n"
                    "Safe release evidence mentions BillingRefundHandler and release-v1."
                ),
                review_state="approved",
                source_refs=[
                    "SRC-SAFE",
                    "redis://:secret@10.0.0.5/0",
                    "Bearer abcdefghijklmnop",
                ],
                tags=["safe-tag", "api.internal.local", "secret"],
                headings=["Release Flow", "db.internal.corp password=bad"],
            ),
            WikiPage(
                id="overlong",
                title="Overlong Topic",
                path=overlong_path,
                role="topic",
                text="# Overlong Topic\n\nThis page should be omitted because its path is unsafe.",
                review_state="approved",
            ),
            WikiPage(
                id="unsafe-title",
                title="password=bad client_secret=also-bad",
                path="safe/unsafe-title.md",
                role="topic",
                text="# Unsafe Title\n\nBenign body.",
                review_state="approved",
                tags=["private"],
                headings=["Authorization: Bearer abcdefghijklmnop"],
            ),
        ],
        nodes=[],
        edges=[],
    )

    guidance = build_retrieval_guidance(
        index,
        query="safe release",
        include_drafts=False,
        fallback_modes=["literal"],
    )
    payload = guidance.model_dump(mode="json")
    encoded = json.dumps(payload, ensure_ascii=False)

    assert payload["orientation_source"] == "projection_extractive"
    assert "SRC-SAFE" in encoded
    assert "safe-tag" in encoded
    for forbidden in (
        "redis://",
        "secret@10.0.0.5",
        "Bearer abcdefghijklmnop",
        "api.internal.local",
        "db.internal.corp",
        "password=bad",
        "client_secret",
        "Overlong Topic",
        "unsafe-title",
        overlong_path[:120],
        str(tmp_path),
    ):
        assert forbidden not in encoded
    assert all(not card["path"].endswith("...") for card in payload["page_cards"])
    assert all(len(card["path"]) <= 1024 for card in payload["page_cards"])
    assert all(card["page_id"] != "overlong" for card in payload["page_cards"])


def test_context_guidance_for_configured_unprobed_vector_stays_literal_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import llmwiki_serve.service as service_module

    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(root / "index.md", "# Index\n\nBilling refund workflow.\n")

    def fail_create_embedding_provider(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError("context guidance must not instantiate vector providers")

    class FailingVectorIndexCache:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("context guidance must not create vector caches")

    monkeypatch.setattr(service_module, "create_embedding_provider", fail_create_embedding_provider)
    monkeypatch.setattr(service_module, "VectorIndexCache", FailingVectorIndexCache)

    service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
    )
    context = service.context("billing", limit=2)

    assert context.retrieval_guidance is not None
    assert context.retrieval_guidance.fallback_modes == ["literal"]
    assert service._vector_provider is None  # noqa: SLF001
    assert service._vector_cache is None  # noqa: SLF001


def test_context_guidance_uses_valid_injected_vector_fallback_without_cache_build(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(root / "index.md", "# Index\n\nBilling refund workflow.\n")
    provider = GuidedFakeEmbeddingProvider()

    service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True),
        vector_provider=provider,
    )
    context = service.context("billing", limit=2)
    capabilities = service.manifest().capabilities

    assert context.retrieval_guidance is not None
    assert context.retrieval_guidance.fallback_modes == ["literal", "hybrid", "vector"]
    assert "llmwiki_search_mode_vector" in capabilities
    assert "llmwiki_search_mode_hybrid" in capabilities
    assert provider.document_calls == 0
    assert provider.query_calls == 0
    assert service._vector_cache is None  # noqa: SLF001


def test_manifest_probe_enables_vector_guidance_after_lazy_provider_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import llmwiki_serve.service as service_module

    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(root / "index.md", "# Index\n\nBilling refund workflow.\n")
    provider = GuidedFakeEmbeddingProvider()
    create_calls = 0

    def fake_create_embedding_provider(_config: VectorConfig) -> GuidedFakeEmbeddingProvider:
        nonlocal create_calls
        create_calls += 1
        return provider

    monkeypatch.setattr(service_module, "create_embedding_provider", fake_create_embedding_provider)

    service = LlmWikiService(
        root,
        vector_config=VectorConfig(enabled=True, cache_dir=tmp_path / "cache"),
    )
    before_probe = service.context("billing", limit=2)
    assert service._vector_cache is None  # noqa: SLF001
    manifest = service.manifest()
    after_probe = service.context("billing", limit=2)

    assert before_probe.retrieval_guidance is not None
    assert before_probe.retrieval_guidance.fallback_modes == ["literal"]
    assert create_calls == 1
    assert "llmwiki_search_mode_vector" in manifest.capabilities
    assert "llmwiki_search_mode_hybrid" in manifest.capabilities
    assert after_probe.retrieval_guidance is not None
    assert after_probe.retrieval_guidance.fallback_modes == ["literal", "hybrid", "vector"]
    assert provider.document_calls == 0
    assert provider.query_calls == 0


def test_provider_failure_keeps_manifest_and_guidance_literal_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import llmwiki_serve.service as service_module

    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(root / "index.md", "# Index\n\nBilling refund workflow.\n")

    def fail_create_embedding_provider(_config: VectorConfig) -> NoReturn:
        raise VectorSearchError("forced provider failure")

    monkeypatch.setattr(service_module, "create_embedding_provider", fail_create_embedding_provider)

    service = LlmWikiService(root, vector_config=VectorConfig(enabled=True))
    manifest = service.manifest()
    context = service.context("billing", limit=2)
    guidance = context.retrieval_guidance

    assert "llmwiki_search_mode_vector" not in manifest.capabilities
    assert "llmwiki_search_mode_hybrid" not in manifest.capabilities
    assert guidance is not None
    assert guidance.fallback_modes == ["literal"]
    assert ("vector" in guidance.fallback_modes) is (
        "llmwiki_search_mode_vector" in manifest.capabilities
    )
    assert ("hybrid" in guidance.fallback_modes) is (
        "llmwiki_search_mode_hybrid" in manifest.capabilities
    )
    assert service._vector_provider is None  # noqa: SLF001


def test_query_variants_fuse_unicode_lexical_channels_and_keep_no_variant_compatibility(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(
        root / "billing-refund.md",
        """
# Billing Refund

Billing refund workflow and InvoiceRefundProcessor details.
""",
    )
    write_markdown(
        root / "korean-refund.md",
        """
# Korean Refund

환불 정책 및 청구 조정 절차를 설명한다.
""",
    )
    service = LlmWikiService(root)

    baseline = service.search("billing refund", limit=4)
    assert service.search("billing refund", limit=4, query_variants=[]) == baseline
    assert (
        service.search(
            "billing refund",
            limit=4,
            query_variants=["  BILLING REFUND  "],
        )
        == baseline
    )

    fused = service.search(
        "billing refund",
        limit=4,
        query_variants=["환불 정책", "InvoiceRefundProcessor"],
        fields=["page_id", "route"],
    )

    assert [item["page_id"] for item in fused[:2]] == ["billing-refund", "korean-refund"]
    assert all(item["route"] == "search" for item in fused)


def test_query_variants_do_not_override_primary_exact_identifier_guard(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(
        root / "release.v1-beta.md",
        """
# Exact Release

The exact release note owns the requested path identifier.
""",
    )
    write_markdown(
        root / "release-beta-summary.md",
        """
# Release Beta Summary

release beta summary repeated release beta summary.
""",
    )
    service = LlmWikiService(root, analyzer_profile="english")

    results = service.search(
        "release.v1-beta.md",
        limit=4,
        query_variants=["release beta summary"],
        fields=["page_id", "path"],
    )

    assert results == [{"page_id": "release.v1-beta", "path": "release.v1-beta.md"}]


@pytest.mark.parametrize("mode", ["literal", "vector", "hybrid"])
def test_query_variants_are_rejected_before_non_lexical_search(
    tmp_path: Path,
    mode: SearchMode,
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(root / "index.md", "# Index\n\nrefund workflow\n")
    service = LlmWikiService(root)

    with pytest.raises(LlmWikiUserError, match="mode=lexical"):
        service.search("refund", mode=mode, query_variants=["workflow"])


@pytest.mark.parametrize(
    ("query", "variants", "message"),
    [
        ("refund", ["a", "b", "c"], "at most 2"),
        ("refund", [""], "non-empty"),
        ("   ", ["refund"], "query is required"),
        ("refund", [42], "must be strings"),
    ],
)
def test_query_variants_reject_invalid_inputs(
    tmp_path: Path,
    query: str,
    variants: list[Any],
    message: str,
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(root / "index.md", "# Index\n\nrefund workflow\n")

    with pytest.raises(LlmWikiUserError, match=message):
        LlmWikiService(root).search(query, query_variants=variants)


def test_empty_query_overview_compatibility_across_surfaces(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(root / "index.md", "# Index\n\nRoot overview page.\n")
    write_markdown(root / "topic.md", "# Topic\n\nDetailed topic body.\n")
    service = LlmWikiService(root)

    baseline = service.search("", limit=2, fields=["page_id", "route"])
    assert baseline[0] == {"page_id": "index", "route": "overview"}
    assert service.search("", limit=2, query_variants=[], fields=["page_id", "route"]) == baseline
    assert service.context("", limit=2).evidence[0].page_id == "index"
    assert service.context("", limit=2, query_variants=[]).evidence[0].page_id == "index"

    client = TestClient(create_app(root))
    http_search_omitted = client.post(
        "/search",
        json={"limit": 2, "fields": ["page_id", "route"]},
    )
    http_query_omitted = client.post(
        "/query",
        json={"limit": 2, "fields": ["page_id", "route"]},
    )
    http_search = client.post(
        "/search",
        json={"query": "", "query_variants": [], "limit": 2, "fields": ["page_id", "route"]},
    )
    http_query = client.post(
        "/query",
        json={"query": "", "query_variants": [], "limit": 2, "fields": ["page_id", "route"]},
    )
    assert http_search_omitted.status_code == 200
    assert http_search_omitted.json()["results"] == baseline
    assert http_query_omitted.status_code == 200
    assert http_query_omitted.json()["evidence"][0] == {
        "page_id": "index",
        "route": "overview",
    }
    assert http_search.status_code == 200
    assert http_search.json()["results"] == baseline
    assert http_query.status_code == 200
    assert http_query.json()["evidence"][0] == {"page_id": "index", "route": "overview"}

    mcp_search_omitted = mcp_tool_call(
        client,
        "llmwiki_search",
        {"limit": 2, "fields": ["page_id", "route"]},
    )
    mcp_context_omitted = mcp_tool_call(
        client,
        "llmwiki_context",
        {"limit": 2, "fields": ["page_id", "route"]},
    )
    mcp_search = mcp_tool_call(
        client,
        "llmwiki_search",
        {"query": "", "query_variants": [], "limit": 2, "fields": ["page_id", "route"]},
    )
    mcp_context = mcp_tool_call(
        client,
        "llmwiki_context",
        {"query": "", "query_variants": [], "limit": 2, "fields": ["page_id", "route"]},
    )
    assert mcp_search_omitted["results"] == baseline
    assert mcp_context_omitted["evidence"][0] == {"page_id": "index", "route": "overview"}
    assert mcp_search["results"] == baseline
    assert mcp_context["evidence"][0] == {"page_id": "index", "route": "overview"}

    stream_context_omitted = mcp_stream_tool_call(
        root,
        "llmwiki_context",
        {"limit": 2, "fields": ["page_id", "route"]},
    )
    stream_context = mcp_stream_tool_call(
        root,
        "llmwiki_context",
        {"query": "", "query_variants": [], "limit": 2, "fields": ["page_id", "route"]},
    )
    assert stream_context_omitted["evidence"][0] == {"page_id": "index", "route": "overview"}
    assert stream_context["evidence"][0] == {"page_id": "index", "route": "overview"}


def test_fastmcp_query_variants_schema_and_runtime_cap(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(root / "index.md", "# Index\n\nBilling refund workflow.\n")
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }

    with TestClient(
        create_app(root),
        base_url="http://127.0.0.1:8000",
        follow_redirects=False,
    ) as stream_client:
        tools_response = stream_client.post(
            MCP_STREAM_PATH,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=headers,
        )

    assert tools_response.status_code == 200
    tools = {
        tool["name"]: tool
        for tool in tools_response.json()["result"]["tools"]
        if tool["name"] in {"llmwiki_context", "llmwiki_search"}
    }
    assert set(tools) == {"llmwiki_context", "llmwiki_search"}

    for tool in tools.values():
        query_variants_schema = tool["inputSchema"]["properties"]["query_variants"]
        assert query_variants_schema["type"] == "array"
        assert query_variants_schema["items"] == {"type": "string"}
        assert query_variants_schema["maxItems"] == 2

    for tool_name in ("llmwiki_context", "llmwiki_search"):
        response = mcp_stream_raw_tool_call(
            root,
            tool_name,
            {"query": "billing", "query_variants": ["one", "two", "three"]},
        )
        result = cast(dict[str, Any], response["result"])
        assert result["isError"] is True
        assert "query_variants" in result["content"][0]["text"]


def test_nonempty_query_variants_require_nonempty_primary_across_surfaces(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(root / "index.md", "# Index\n\nBilling refund workflow.\n")
    service = LlmWikiService(root)

    with pytest.raises(LlmWikiUserError, match="query is required"):
        service.search("  ", query_variants=["billing"])
    with pytest.raises(LlmWikiUserError, match="query is required"):
        service.context("  ", query_variants=["billing"])

    client = TestClient(create_app(root))
    http_response = client.post(
        "/search",
        json={"query": "  ", "query_variants": ["billing"]},
    )
    assert http_response.status_code == 400
    assert http_response.json()["detail"] == "query is required when query_variants are supplied"

    mcp_response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "llmwiki_search",
                "arguments": {"query": "  ", "query_variants": ["billing"]},
            },
        },
    ).json()
    assert mcp_response["error"] == {
        "code": -32602,
        "message": "query is required when query_variants are supplied",
    }

    stream_response = mcp_stream_raw_tool_call(
        root,
        "llmwiki_search",
        {"query": "  ", "query_variants": ["billing"]},
    )
    stream_result = cast(dict[str, Any], stream_response["result"])
    assert stream_result["isError"] is True
    assert (
        "query is required when query_variants are supplied"
        in (stream_result["content"][0]["text"])
    )


def test_http_and_mcp_query_variants_contract(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(root / "billing.md", "# Billing\n\nbilling refund workflow\n")
    write_markdown(root / "korean.md", "# Korean\n\n환불 정책\n")
    client = TestClient(create_app(root))

    http_search = client.post(
        "/search",
        json={
            "query": "billing refund",
            "query_variants": ["환불 정책"],
            "fields": ["page_id"],
        },
    )
    assert http_search.status_code == 200
    assert [item["page_id"] for item in http_search.json()["results"][:2]] == [
        "billing",
        "korean",
    ]

    invalid_http = client.post(
        "/search",
        json={"query": "billing", "mode": "literal", "query_variants": ["refund"]},
    )
    assert invalid_http.status_code == 400
    assert invalid_http.json()["detail"] == "query_variants is supported only with mode=lexical"

    null_http = client.post("/search", json={"query": "billing", "query_variants": None})
    assert null_http.status_code == 422

    mcp_search = mcp_tool_call(
        client,
        "llmwiki_search",
        {
            "query": "billing refund",
            "query_variants": ["환불 정책"],
            "fields": ["page_id"],
        },
    )
    assert [item["page_id"] for item in mcp_search["results"][:2]] == ["billing", "korean"]

    invalid_mcp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "llmwiki_search",
                "arguments": {
                    "query": "billing",
                    "mode": "literal",
                    "query_variants": ["refund"],
                },
            },
        },
    ).json()
    assert invalid_mcp["error"] == {
        "code": -32602,
        "message": "query_variants is supported only with mode=lexical",
    }

    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }
    with TestClient(
        create_app(root),
        base_url="http://127.0.0.1:8000",
        follow_redirects=False,
    ) as stream_client:
        stream_null = stream_client.post(
            MCP_STREAM_PATH,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "llmwiki_search",
                    "arguments": {"query": "billing", "query_variants": None},
                },
            },
            headers=headers,
        ).json()
    assert stream_null["result"]["isError"] is True
    assert "query_variants" in stream_null["result"]["content"][0]["text"]


def write_markdown(path: Path, content: str) -> None:
    path.write_text(content.strip() + "\n", encoding="utf-8")


def tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def mcp_tool_call(client: TestClient, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    payload = cast(
        dict[str, Any],
        client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        ).json(),
    )

    assert "error" not in payload
    return cast(dict[str, Any], payload["result"])


def mcp_stream_raw_tool_call(root: Path, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }
    with TestClient(
        create_app(root),
        base_url="http://127.0.0.1:8000",
        follow_redirects=False,
    ) as stream_client:
        return cast(
            dict[str, Any],
            stream_client.post(
                MCP_STREAM_PATH,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
                headers=headers,
            ).json(),
        )


def mcp_stream_tool_call(root: Path, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    payload = mcp_stream_raw_tool_call(root, name, arguments)
    result = cast(dict[str, Any], payload["result"])
    assert result["isError"] is False
    return cast(dict[str, Any], result["structuredContent"])
