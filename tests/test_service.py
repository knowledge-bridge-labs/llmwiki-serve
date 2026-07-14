from __future__ import annotations

import builtins
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from llmwiki_serve.adapters import (
    WIKI_ROOT_MISSING_CODE,
    WIKI_ROOT_MISSING_SAFE_MESSAGE,
    WIKI_ROOT_UNSUPPORTED_CODE,
    WIKI_ROOT_UNSUPPORTED_SAFE_MESSAGE,
)
from llmwiki_serve.api import (
    MCP_INTERNAL_FAILURE_MESSAGE,
    MCP_STREAM_PATH,
    MCP_UNKNOWN_TOOL_MESSAGE,
    MCP_UNSUPPORTED_METHOD_MESSAGE,
    NETWORK_MANIFEST_ROOT,
    create_app,
)
from llmwiki_serve.cli import app as cli_app
from llmwiki_serve.projection_store import (
    REDIS_EXTRA_MESSAGE,
    InMemoryProjectionStore,
    ProjectionKey,
    ProjectionRecord,
    RedisProjectionStore,
    projection_record_from_payload,
    record_to_payload,
    redis_latest_key,
    redis_projection_key,
)
from llmwiki_serve.search import search as raw_search
from llmwiki_serve.service import LlmWikiService, source_signature

FIXTURE = Path(__file__).parent / "fixtures" / "sample-wiki"


def test_manifest_and_graph() -> None:
    service = LlmWikiService(FIXTURE)
    manifest = service.manifest()
    assert manifest.title == "Sample Packaging LLMWiki"
    assert manifest.source_id == "sample-packaging-llmwiki"
    assert manifest.bundle_id.startswith("sample-packaging-llmwiki:sha256:")
    assert manifest.public_uri == "llmwiki://sample-packaging-llmwiki"
    assert manifest.page_count == 5
    assert manifest.approved_page_count == 4
    assert manifest.projection.signature.startswith("sha256:")
    assert manifest.projection.page_count == 5
    assert manifest.projection.approved_page_count == 4
    assert manifest.projection.graph_node_count > 0
    assert manifest.raw_origins.enabled is False
    assert manifest.raw_origins.metadata_only is True
    assert "llmwiki_source_bundle" in manifest.capabilities
    assert "llmwiki_source_refs" in manifest.capabilities
    assert "mcp-streamable-http" in manifest.capabilities
    assert "a2a-message" not in manifest.capabilities
    assert "a2a-message" in service.manifest(enable_a2a_compat=True).capabilities
    graph = service.graph()
    assert any(node["kind"] == "source_ref" for node in graph["nodes"])
    assert any(edge["relation"] == "links_to" for edge in graph["edges"])


def test_source_refs_are_typed_opaque_and_draft_filtered() -> None:
    service = LlmWikiService(FIXTURE)

    refs = service.source_refs()
    labels = {item.label for item in refs.source_refs}

    assert refs.source_id == "sample-packaging-llmwiki"
    assert refs.bundle_id.startswith("sample-packaging-llmwiki:sha256:")
    assert "SRC-DRAFT" not in labels
    assert "SRC-HOT" in labels
    hot = next(item for item in refs.source_refs if item.label == "SRC-HOT")
    assert hot.id == "src-hot"
    assert hot.uri == "llmwiki://sample-packaging-llmwiki/source-refs/src-hot"
    assert hot.linked_pages == ["hot.md"]
    assert hot.linked_page_ids == ["hot"]
    assert hot.locator == {}

    draft_refs = service.source_refs(include_drafts=True)
    assert "SRC-DRAFT" in {item.label for item in draft_refs.source_refs}


def test_source_ref_ids_do_not_merge_hash_suffix_collisions(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    second_label = "A-B"
    second_suffix = hashlib.sha256(second_label.encode("utf-8")).hexdigest()[:8]
    third_label = f"A-B-{second_suffix}"
    write_markdown(
        root / "index.md",
        f"""
---
review_state: approved
source_refs: ["A B", "{second_label}", "{third_label}"]
---
# Index

Collision source refs.
""",
    )

    refs = LlmWikiService(root).source_refs().source_refs
    ids_by_label = {item.label: item.id for item in refs}

    assert set(ids_by_label) == {"A B", second_label, third_label}
    assert len(set(ids_by_label.values())) == 3
    assert ids_by_label["A B"] == "a-b"
    assert ids_by_label[second_label] == f"a-b-{second_suffix}"
    assert ids_by_label[third_label].startswith(f"a-b-{second_suffix}-")


def test_bundle_id_uses_portable_projection_digest(tmp_path: Path) -> None:
    copied = tmp_path / "sample-wiki-copy"
    shutil.copytree(FIXTURE, copied)

    original_manifest = LlmWikiService(FIXTURE).manifest()
    copied_manifest = LlmWikiService(copied).manifest()

    assert copied_manifest.source_id == original_manifest.source_id
    assert copied_manifest.projection.signature == original_manifest.projection.signature
    assert copied_manifest.bundle_id == original_manifest.bundle_id


def test_context_uses_hot_index_and_withholds_drafts() -> None:
    context = LlmWikiService(FIXTURE).context("required copy release readiness", limit=4)
    assert context.answerable
    assert context.description == "Synthetic packaging operations knowledge base."
    assert context.adapter == "llmwiki-markdown"
    assert context.implementation == "llmwiki-markdown"
    assert context.page_count == 5
    assert context.approved_page_count == 4
    assert [item.role for item in context.orientation[:2]] == ["hot", "index"]
    assert all("draft" not in item.path for item in context.evidence)
    assert context.limitations == ["1 draft or unapproved page(s) were withheld."]
    assert all("draft" not in node["id"] for node in context.graph["nodes"])


def test_search_role_boost_does_not_create_unmatched_evidence(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(
        root / "hot.md",
        """
---
review_state: approved
---
# Hot

zzcommonneedle appears in hot guidance.
""",
    )
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Search Match Fixture
review_state: approved
---
# Search Match Fixture

Public orientation page with no private needle.
""",
    )
    write_markdown(
        root / "overview.md",
        """
---
review_state: approved
---
# Overview

Public overview page with no private needle.
""",
    )
    write_markdown(
        root / "topic.md",
        """
---
title: Topic
review_state: approved
---
# Topic

zzcommonneedle appears in a regular topic too.
""",
    )
    write_markdown(
        root / "draft-note.md",
        """
---
title: Draft Note
review_state: draft
---
# Draft Note

zzdraftonlyneedle exists only in this draft page.
""",
    )
    service = LlmWikiService(root)

    no_match = service.context("zzmissingneedle")
    assert no_match.answerable is False
    assert no_match.evidence == []
    assert service.search("zzmissingneedle") == []
    assert [item.role for item in no_match.orientation[:3]] == ["hot", "index", "overview"]
    assert no_match.limitations == [
        "No matching approved LLMWiki page was found.",
        "1 draft or unapproved page(s) were withheld.",
    ]

    matched_results = service.search("zzcommonneedle")
    assert matched_results[0]["page_id"] == "hot"
    assert all(item["page_id"] not in {"index", "overview"} for item in matched_results)

    draft_only = service.context("zzdraftonlyneedle")
    assert draft_only.answerable is False
    assert draft_only.evidence == []
    assert draft_only.limitations == [
        "No matching approved LLMWiki page was found.",
        "1 draft or unapproved page(s) were withheld.",
    ]
    assert service.search("zzdraftonlyneedle") == []
    assert service.search("zzdraftonlyneedle", include_drafts=True)[0]["page_id"] == "draft-note"


def test_global_and_local_questions_use_same_context_contract() -> None:
    client = TestClient(create_app(FIXTURE))

    global_context = client.post(
        "/query", json={"query": "what is in this wiki?", "limit": 4}
    ).json()
    local_context = client.post(
        "/query", json={"query": "required copy release readiness", "limit": 4}
    ).json()
    mcp_context = mcp_tool_call(
        client,
        "llmwiki_context",
        {"query": "what is in this wiki?", "limit": 4},
    )

    for context in (global_context, local_context, mcp_context):
        assert context["wiki_title"] == "Sample Packaging LLMWiki"
        assert context["description"] == "Synthetic packaging operations knowledge base."
        assert context["adapter"] == "llmwiki-markdown"
        assert context["implementation"] == "llmwiki-markdown"
        assert context["page_count"] == 5
        assert context["approved_page_count"] == 4
        assert [item["role"] for item in context["orientation"][:2]] == ["hot", "index"]
        assert all(item["route"] == "orientation" for item in context["orientation"])
        assert all(item["route"] == "search" for item in context["evidence"])
        assert context["graph"]["nodes"]

    assert {item["page_id"] for item in global_context["evidence"]} >= {"index", "hot"}
    assert local_context["evidence"][0]["page_id"] == "hot"


def test_graph_filters_drafts_by_default() -> None:
    service = LlmWikiService(FIXTURE)
    graph = service.graph()
    assert all("draft" not in node["id"] for node in graph["nodes"])
    draft_graph = service.graph(include_drafts=True)
    assert any("draft" in node["id"] for node in draft_graph["nodes"])


def test_graph_keeps_approved_unresolved_links_shared_with_drafts(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(
        root / "aaa-draft.md",
        """
---
draft: true
tags: [shared-node]
source_refs: [SHARED-SOURCE]
---
# Draft

Draft links to [[Missing Shared]] before the approved page does. #shared-inline
""",
    )
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
tags: [shared-node]
source_refs: [SHARED-SOURCE]
---
# Index

Approved page links to [[Missing Shared]] too. #shared-inline
""",
    )

    service = LlmWikiService(root)
    graph = service.graph()
    context_graph = service.context("Missing Shared").graph
    node_ids = {node["id"] for node in graph["nodes"]}
    nodes = {node["id"]: node for node in graph["nodes"]}
    edge_keys = {(edge["source"], edge["target"], edge["relation"]) for edge in graph["edges"]}

    assert "page:aaa-draft" not in node_ids
    assert "placeholder:Missing-Shared" in node_ids
    assert ("page:index", "placeholder:Missing-Shared", "links_to") in edge_keys
    for shared_node_id in (
        "placeholder:Missing-Shared",
        "tag:shared-node",
        "tag:shared-inline",
        "source:SHARED-SOURCE",
    ):
        assert nodes[shared_node_id]["path"] == "index.md"
    assert "aaa-draft.md" not in json.dumps(graph)
    assert "aaa-draft.md" not in json.dumps(context_graph)


def test_graph_limit_does_not_return_edges_to_omitted_nodes() -> None:
    client = TestClient(create_app(FIXTURE))

    for graph in [
        client.get("/graph?limit=1").json(),
        client.get("/graph?limit=1&include_drafts=true").json(),
    ]:
        node_ids = {node["id"] for node in graph["nodes"]}
        assert len(graph["nodes"]) == 1
        assert all(
            edge["source"] in node_ids and edge["target"] in node_ids for edge in graph["edges"]
        )


def test_read_draft_is_blocked_by_default() -> None:
    service = LlmWikiService(FIXTURE)
    assert service.read("draft-note") == {"found": False, "reason": "not approved for serving"}
    assert service.read("draft-note", include_drafts=True)["title"] == "Draft Note"
    assert service.read("../draft-note") == {"found": False}


def test_read_returns_json_safe_frontmatter(tmp_path: Path) -> None:
    write_markdown(
        tmp_path / "hot.md",
        """---
title: Hot Cache
created: 2026-07-10
updated: 2026-07-11
review_state: approved
---

# Hot Cache

Current focus.
""",
    )
    service = LlmWikiService(tmp_path)

    page = service.read("hot")

    assert page["frontmatter"]["created"] == "2026-07-10"
    assert page["frontmatter"]["updated"] == "2026-07-11"


def test_http_include_drafts_requires_app_opt_in() -> None:
    default_client = TestClient(create_app(FIXTURE))

    default_query = default_client.post(
        "/query", json={"query": "withheld unless", "include_drafts": True}
    ).json()
    default_search = default_client.post(
        "/search", json={"query": "withheld unless", "include_drafts": True}
    ).json()
    default_read = default_client.get("/read/draft-note?include_drafts=true").json()
    default_graph = default_client.get("/graph?include_drafts=true").json()

    assert all(item["page_id"] != "draft-note" for item in default_query["evidence"])
    assert default_query["answerable"] is False
    assert default_query["evidence"] == []
    assert default_query["limitations"] == [
        "No matching approved LLMWiki page was found.",
        "1 draft or unapproved page(s) were withheld.",
    ]
    assert default_search["results"] == []
    assert default_read == {"found": False, "reason": "not approved for serving"}
    assert all(node["id"] != "page:draft-note" for node in default_graph["nodes"])

    allowed_client = TestClient(create_app(FIXTURE, allow_drafts=True))

    allowed_query = allowed_client.post(
        "/query", json={"query": "withheld unless", "include_drafts": True}
    ).json()
    allowed_search = allowed_client.post(
        "/search", json={"query": "withheld unless", "include_drafts": True}
    ).json()
    allowed_read = allowed_client.get("/read/draft-note?include_drafts=true").json()
    allowed_graph = allowed_client.get("/graph?include_drafts=true").json()

    assert any(item["page_id"] == "draft-note" for item in allowed_query["evidence"])
    assert any(item["page_id"] == "draft-note" for item in allowed_search["results"])
    assert allowed_read["title"] == "Draft Note"
    assert any(node["id"] == "page:draft-note" for node in allowed_graph["nodes"])


def test_mcp_include_drafts_requires_app_opt_in() -> None:
    default_client = TestClient(create_app(FIXTURE))

    default_context = mcp_tool_call(
        default_client,
        "llmwiki_context",
        {"query": "withheld unless", "include_drafts": True},
    )
    default_search = mcp_tool_call(
        default_client,
        "llmwiki_search",
        {"query": "withheld unless", "include_drafts": True},
    )
    default_read = mcp_tool_call(
        default_client,
        "llmwiki_read",
        {"page_id": "draft-note", "include_drafts": True},
    )
    default_graph = mcp_tool_call(
        default_client,
        "llmwiki_graph",
        {"include_drafts": True},
    )

    assert all(item["page_id"] != "draft-note" for item in default_context["evidence"])
    assert default_context["answerable"] is False
    assert default_context["evidence"] == []
    assert default_context["limitations"] == [
        "No matching approved LLMWiki page was found.",
        "1 draft or unapproved page(s) were withheld.",
    ]
    assert default_search["results"] == []
    assert default_read == {"found": False, "reason": "not approved for serving"}
    assert all(node["id"] != "page:draft-note" for node in default_graph["nodes"])

    allowed_client = TestClient(create_app(FIXTURE, allow_drafts=True))

    allowed_context = mcp_tool_call(
        allowed_client,
        "llmwiki_context",
        {"query": "withheld unless", "include_drafts": True},
    )
    allowed_search = mcp_tool_call(
        allowed_client,
        "llmwiki_search",
        {"query": "withheld unless", "include_drafts": True},
    )
    allowed_read = mcp_tool_call(
        allowed_client,
        "llmwiki_read",
        {"page_id": "draft-note", "include_drafts": True},
    )
    allowed_graph = mcp_tool_call(
        allowed_client,
        "llmwiki_graph",
        {"include_drafts": True},
    )

    assert any(item["page_id"] == "draft-note" for item in allowed_context["evidence"])
    assert any(item["page_id"] == "draft-note" for item in allowed_search["results"])
    assert allowed_read["title"] == "Draft Note"
    assert any(node["id"] == "page:draft-note" for node in allowed_graph["nodes"])


def test_network_manifest_redacts_root_and_cli_manifest_keeps_it() -> None:
    client = TestClient(create_app(FIXTURE))
    response = client.get("/manifest")
    manifest = response.json()

    assert manifest["root"] == NETWORK_MANIFEST_ROOT
    assert manifest["source_id"] == "sample-packaging-llmwiki"
    assert manifest["bundle_id"].startswith("sample-packaging-llmwiki:sha256:")
    assert manifest["public_uri"] == "llmwiki://sample-packaging-llmwiki"
    assert manifest["projection"]["signature"].startswith("sha256:")
    assert manifest["raw_origins"]["metadata_only"] is True
    assert "llmwiki_source_refs" in manifest["capabilities"]
    assert "mcp-streamable-http" in manifest["capabilities"]
    assert "a2a-message" not in manifest["capabilities"]
    assert str(FIXTURE.resolve()) not in response.text

    opt_in_manifest = (
        TestClient(create_app(FIXTURE, enable_a2a_compat=True)).get("/manifest").json()
    )
    assert "a2a-message" in opt_in_manifest["capabilities"]

    result = CliRunner().invoke(cli_app, ["manifest", str(FIXTURE)])

    assert result.exit_code == 0, result.output
    cli_manifest = json.loads(result.output)
    assert cli_manifest["root"] == str(FIXTURE.resolve())
    assert "mcp-streamable-http" in cli_manifest["capabilities"]
    assert "a2a-message" not in cli_manifest["capabilities"]


def test_source_refs_http_mcp_and_cli_contract() -> None:
    client = TestClient(create_app(FIXTURE))

    http_refs = client.get("/source-refs").json()
    mcp_refs = mcp_tool_call(client, "llmwiki_source_refs", {})
    cli_result = CliRunner().invoke(cli_app, ["source-refs", str(FIXTURE)])

    assert cli_result.exit_code == 0, cli_result.output
    cli_refs = json.loads(cli_result.output)
    assert http_refs == mcp_refs == cli_refs
    assert http_refs["source_id"] == "sample-packaging-llmwiki"
    assert all(
        "draft" not in page for item in http_refs["source_refs"] for page in item["linked_pages"]
    )
    assert any(
        item["uri"].startswith("llmwiki://sample-packaging-llmwiki/source-refs/")
        for item in http_refs["source_refs"]
    )

    draft_blocked = client.get("/source-refs?include_drafts=true").json()
    assert "SRC-DRAFT" not in {item["label"] for item in draft_blocked["source_refs"]}

    draft_allowed = (
        TestClient(create_app(FIXTURE, allow_drafts=True))
        .get("/source-refs?include_drafts=true")
        .json()
    )
    assert "SRC-DRAFT" in {item["label"] for item in draft_allowed["source_refs"]}


def test_source_bundle_http_mcp_and_cli_contract() -> None:
    client = TestClient(create_app(FIXTURE))

    http_bundle = client.get("/source-bundle").json()
    mcp_bundle = mcp_tool_call(client, "llmwiki_source_bundle", {})
    cli_result = CliRunner().invoke(cli_app, ["source-bundle", str(FIXTURE)])

    assert cli_result.exit_code == 0, cli_result.output
    cli_bundle = json.loads(cli_result.output)
    assert http_bundle == mcp_bundle == cli_bundle
    assert http_bundle["source_id"] == "sample-packaging-llmwiki"
    assert http_bundle["public_uri"] == "llmwiki://sample-packaging-llmwiki"
    assert http_bundle["projection"]["page_count"] == 5
    assert http_bundle["projection"]["approved_page_count"] == 4
    assert http_bundle["raw_origins"]["metadata_only"] is True
    assert "llmwiki_source_bundle" in http_bundle["capabilities"]
    assert "llmwiki_source_refs" in http_bundle["capabilities"]
    assert "SRC-DRAFT" not in {item["label"] for item in http_bundle["source_refs"]}

    a2a_client = TestClient(create_app(FIXTURE, enable_a2a_compat=True))
    a2a_http_bundle = a2a_client.get("/source-bundle").json()
    a2a_mcp_bundle = mcp_tool_call(a2a_client, "llmwiki_source_bundle", {})
    assert "a2a-message" in a2a_http_bundle["capabilities"]
    assert "a2a-message" in a2a_mcp_bundle["capabilities"]


def test_cli_reports_unsupported_wiki_root_without_traceback(tmp_path: Path) -> None:
    empty_root = tmp_path / "empty-wiki"
    empty_root.mkdir()

    for command in (
        ["manifest", str(empty_root)],
        ["query", str(empty_root), "release readiness"],
        ["serve", str(empty_root)],
    ):
        result = CliRunner().invoke(cli_app, command)

        assert result.exit_code == 1, result.output
        assert "No supported wiki files were found" in result.output
        assert "Traceback" not in result.output


def test_cli_reports_marker_only_roots_as_unsupported(tmp_path: Path) -> None:
    for adapter_name in ("foam", "dendron", "quartz", "logseq"):
        root = tmp_path / f"{adapter_name}-marker-only"
        create_marker_only_root(root, adapter_name)

        result = CliRunner().invoke(cli_app, ["manifest", str(root)])

        assert result.exit_code == 1, result.output
        assert "No supported wiki files were found" in result.output
        assert "Traceback" not in result.output


def test_http_routes_return_redacted_json_for_missing_and_unsupported_roots(
    tmp_path: Path,
) -> None:
    missing_root = tmp_path / "private" / "missing-wiki"
    missing = TestClient(create_app(missing_root)).get("/manifest")

    assert_redacted_root_error(
        missing,
        status_code=404,
        code=WIKI_ROOT_MISSING_CODE,
        message=WIKI_ROOT_MISSING_SAFE_MESSAGE,
        root=missing_root,
    )

    unsupported_root = tmp_path / "foam-marker-only"
    create_marker_only_root(unsupported_root, "foam")
    unsupported = TestClient(create_app(unsupported_root)).post(
        "/query",
        json={"query": "release readiness"},
    )

    assert_redacted_root_error(
        unsupported,
        status_code=422,
        code=WIKI_ROOT_UNSUPPORTED_CODE,
        message=WIKI_ROOT_UNSUPPORTED_SAFE_MESSAGE,
        root=unsupported_root,
    )


def test_http_routes_return_redacted_json_when_root_is_removed_after_start(
    tmp_path: Path,
) -> None:
    root = tmp_path / "removable-wiki"
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Removable Wiki
review_state: approved
---
# Removable Wiki

initial content before deletion.
""",
    )
    client = TestClient(create_app(root))
    assert client.get("/manifest").status_code == 200

    shutil.rmtree(root)
    response = client.get("/graph")

    assert_redacted_root_error(
        response,
        status_code=404,
        code=WIKI_ROOT_MISSING_CODE,
        message=WIKI_ROOT_MISSING_SAFE_MESSAGE,
        root=root,
    )


def test_cli_rejects_out_of_range_runtime_options_without_traceback() -> None:
    for command in (
        ["query", str(FIXTURE), "required copy", "--limit", "0"],
        ["query", str(FIXTURE), "required copy", "--limit", "31"],
        ["serve", str(FIXTURE), "--port", "0"],
        ["serve", str(FIXTURE), "--refresh-interval-seconds", "-1"],
    ):
        result = CliRunner().invoke(cli_app, command)

        assert result.exit_code != 0, result.output
        assert "Invalid value" in result.output
        assert "Traceback" not in result.output


def test_cli_rejects_invalid_projection_store_env_without_traceback() -> None:
    result = CliRunner().invoke(
        cli_app,
        ["serve", str(FIXTURE)],
        env={"LLMWIKI_PROJECTION_STORE": "redsi"},
    )

    assert result.exit_code == 1, result.output
    assert "LLMWIKI_PROJECTION_STORE must be 'memory' or 'redis'" in result.output
    assert "Traceback" not in result.output


def test_publication_frontmatter_filters_default_surfaces(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Visibility Fixture
review_state: approved
---
# Visibility Fixture

Public orientation page for publication filtering.
""",
    )
    write_markdown(
        root / "draft-string.md",
        """
---
title: Draft String
review_state: approved
draft: "true"
---
# Draft String

blocked visibility phrase from draft string.
zzblockedvisibility appears only in blocked pages.
""",
    )
    write_markdown(
        root / "published-false.md",
        """
---
title: Published False
review_state: approved
published: false
---
# Published False

blocked visibility phrase from published false.
zzblockedvisibility appears only in blocked pages.
""",
    )
    write_markdown(
        root / "publish-string-false.md",
        """
---
title: Publish String False
review_state: approved
publish: "false"
---
# Publish String False

blocked visibility phrase from publish string false.
zzblockedvisibility appears only in blocked pages.
""",
    )
    write_markdown(
        root / "visible-draft-false.md",
        """
---
title: Visible Draft False
review_state: approved
draft: false
---
# Visible Draft False

visible control phrase from explicit draft false.
""",
    )

    service = LlmWikiService(root)
    blocked_ids = {"draft-string", "published-false", "publish-string-false"}

    assert service.manifest().approved_page_count == 2
    assert service.search("zzblockedvisibility") == []
    assert {
        item["page_id"] for item in service.search("zzblockedvisibility", include_drafts=True)
    } >= blocked_ids
    assert service.search("visible control phrase")[0]["page_id"] == "visible-draft-false"

    context = service.context("zzblockedvisibility")
    assert context.answerable is False
    assert context.evidence == []
    assert all(item.page_id not in blocked_ids for item in context.evidence)
    assert all(item.page_id not in blocked_ids for item in context.orientation)

    graph = service.graph()
    default_node_ids = {node["id"] for node in graph["nodes"]}
    assert all(f"page:{page_id}" not in default_node_ids for page_id in blocked_ids)
    draft_node_ids = {
        node["id"]
        for node in service.graph(include_drafts=True)["nodes"]
        if node["id"].startswith("page:")
    }
    assert {f"page:{page_id}" for page_id in blocked_ids} <= draft_node_ids

    for page_id in blocked_ids:
        assert service.read(page_id) == {"found": False, "reason": "not approved for serving"}
        assert service.read(page_id, include_drafts=True)["id"] == page_id


def test_http_service_refreshes_when_compile_output_appears_after_start(tmp_path: Path) -> None:
    root = tmp_path / "compile-workspace"
    root.mkdir()
    client = TestClient(create_app(root))

    unsupported = client.get("/manifest")
    assert unsupported.status_code == 422
    assert unsupported.json() == {
        "error": {
            "code": WIKI_ROOT_UNSUPPORTED_CODE,
            "message": WIKI_ROOT_UNSUPPORTED_SAFE_MESSAGE,
        }
    }

    source_root = root / "wiki"
    (source_root / "sources").mkdir(parents=True)
    write_markdown(
        source_root / "index.md",
        """
---
wiki_title: Runtime Compile Fixture
review_state: approved
---
# Runtime Compile Fixture

Compiled wiki index created after service start.
""",
    )
    write_markdown(
        source_root / "sources" / "runtime-ingest.md",
        """
---
title: Runtime Ingest
review_state: approved
---
# Runtime Ingest

zzruntimecompileappeared confirms compile output created after service start.
""",
    )

    manifest = client.get("/manifest").json()
    query = client.post("/query", json={"query": "zzruntimecompileappeared", "limit": 4}).json()

    assert manifest["adapter"] == "llmwiki-markdown"
    assert manifest["page_count"] == 2
    assert query["answerable"] is True
    assert query["evidence"][0]["page_id"] == "sources/runtime-ingest"


def test_service_refreshes_when_nested_compile_output_is_replaced(tmp_path: Path) -> None:
    root = tmp_path / "compile-replace-workspace"
    source_root = root / "wiki"
    (source_root / "sources").mkdir(parents=True)
    write_markdown(
        source_root / "index.md",
        """
---
wiki_title: Compile Replace Fixture
review_state: approved
---
# Compile Replace Fixture

Initial compiled wiki index.
""",
    )
    write_markdown(
        source_root / "sources" / "first.md",
        """
---
title: First Compile
review_state: approved
---
# First Compile

zzcompilefirstneedle is present before replacement.
""",
    )
    service = LlmWikiService(root)

    assert service.manifest().adapter == "llmwiki-markdown"
    assert service.search("zzcompilefirstneedle")[0]["page_id"] == "sources/first"

    replacement_root = root / "wiki.next"
    (replacement_root / "sources").mkdir(parents=True)
    write_markdown(
        replacement_root / "index.md",
        """
---
wiki_title: Compile Replace Fixture V2
review_state: approved
---
# Compile Replace Fixture V2

Replacement compiled wiki index.
""",
    )
    write_markdown(
        replacement_root / "sources" / "second.md",
        """
---
title: Second Compile
review_state: approved
---
# Second Compile

zzcompilesecondneedle is present after replacement.
""",
    )
    previous_root = root / "wiki.previous"
    source_root.rename(previous_root)
    replacement_root.rename(source_root)
    shutil.rmtree(previous_root)

    assert service.manifest().title == "Compile Replace Fixture V2"
    assert service.search("zzcompilefirstneedle") == []
    assert service.read("sources/first") == {"found": False}
    assert service.search("zzcompilesecondneedle")[0]["page_id"] == "sources/second"
    assert service.read("sources/second")["path"] == "sources/second.md"


def test_obsidian_raw_ingest_notes_refresh_add_update_delete(tmp_path: Path) -> None:
    root = tmp_path / "obsidian-runtime-vault"
    notes = root / ".raw" / "personal-agent-notes"
    notes.mkdir(parents=True)
    (root / ".obsidian").mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Obsidian Runtime Fixture
review_state: approved
---
# Obsidian Runtime Fixture

Vault index before runtime ingest.
""",
    )
    service = LlmWikiService(root)
    raw_note = notes / "runtime-ingest.md"

    assert service.manifest().adapter == "obsidian"
    assert service.search("zzobsidianrawadd") == []

    write_markdown(
        raw_note,
        """
# Runtime Raw Ingest

zzobsidianrawadd confirms raw note add after service start.
""",
    )
    assert service.search("zzobsidianrawadd")[0]["page_id"] == (
        ".raw/personal-agent-notes/runtime-ingest"
    )
    assert service.read(".raw/personal-agent-notes/runtime-ingest")["path"] == (
        ".raw/personal-agent-notes/runtime-ingest.md"
    )

    write_markdown(
        raw_note,
        """
# Runtime Raw Ingest

zzobsidianrawupdate confirms raw note update after service start.
""",
    )
    assert service.search("zzobsidianrawadd") == []
    assert service.search("zzobsidianrawupdate")[0]["page_id"] == (
        ".raw/personal-agent-notes/runtime-ingest"
    )

    raw_note.unlink()
    assert service.search("zzobsidianrawupdate") == []
    assert service.read(".raw/personal-agent-notes/runtime-ingest") == {"found": False}
    assert "page:.raw/personal-agent-notes/runtime-ingest" not in {
        node["id"] for node in service.graph(include_drafts=True)["nodes"]
    }


def test_obsidian_nested_wiki_notes_refresh_add_update_delete(tmp_path: Path) -> None:
    root = tmp_path / "obsidian-runtime-wiki-vault"
    concepts = root / "wiki" / "concepts"
    concepts.mkdir(parents=True)
    (root / ".obsidian").mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Obsidian Runtime Wiki Fixture
review_state: approved
---
# Obsidian Runtime Wiki Fixture

Vault index before runtime wiki note changes.
""",
    )
    service = LlmWikiService(root)
    runtime_note = concepts / "runtime.md"

    assert service.manifest().adapter == "obsidian"
    assert service.index().metadata["source_root"] == "."
    assert service.search("zzobsidianwikiadd") == []

    write_markdown(
        runtime_note,
        """
---
title: Runtime Wiki Note
review_state: approved
---
# Runtime Wiki Note

zzobsidianwikiadd confirms nested wiki note add after service start.
""",
    )
    assert service.manifest().adapter == "obsidian"
    assert service.index().metadata["source_root"] == "."
    assert service.search("zzobsidianwikiadd")[0]["page_id"] == "wiki/concepts/runtime"
    assert service.read("wiki/concepts/runtime")["path"] == "wiki/concepts/runtime.md"

    write_markdown(
        runtime_note,
        """
---
title: Runtime Wiki Note
review_state: approved
---
# Runtime Wiki Note

zzobsidianwikiupdate confirms nested wiki note update after service start.
""",
    )
    assert service.search("zzobsidianwikiadd") == []
    assert service.search("zzobsidianwikiupdate")[0]["page_id"] == "wiki/concepts/runtime"

    runtime_note.unlink()
    assert service.search("zzobsidianwikiupdate") == []
    assert service.read("wiki/concepts/runtime") == {"found": False}
    assert "page:wiki/concepts/runtime" not in {
        node["id"] for node in service.graph(include_drafts=True)["nodes"]
    }


def test_obsidian_nested_hubs_fill_manifest_without_replacing_root_hubs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "obsidian-hub-vault"
    (root / ".obsidian").mkdir(parents=True)
    (root / "wiki").mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Root Vault Title
review_state: approved
---
# Root Vault Title

The root index remains the primary index hub.
""",
    )
    write_markdown(
        root / "wiki" / "index.md",
        """
---
wiki_title: Nested Wiki Title
review_state: approved
---
# Nested Wiki Index

The nested index is still recognized but should not replace the root index.
""",
    )
    write_markdown(
        root / "wiki" / "hot.md",
        """
---
review_state: approved
---
# Nested Hot

Nested hot guidance should populate the manifest when no root hot exists.
""",
    )
    write_markdown(
        root / "wiki" / "overview.md",
        """
---
review_state: approved
---
# Nested Overview

Nested overview should stay in the three-item orientation set.
""",
    )

    service = LlmWikiService(root)
    manifest = service.manifest()
    context = service.context("")

    assert manifest.title == "Root Vault Title"
    assert manifest.hot_page == "wiki/hot.md"
    assert manifest.index_page == "index.md"
    assert manifest.overview_page == "wiki/overview.md"
    assert [item.path for item in context.orientation] == [
        "wiki/hot.md",
        "index.md",
        "wiki/overview.md",
    ]
    assert [item.role for item in context.orientation] == ["hot", "index", "overview"]


def test_status_lifecycle_values_are_visible_and_blocking_values_are_withheld(
    tmp_path: Path,
) -> None:
    root = tmp_path / "status-policy-wiki"
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Status Policy Fixture
review_state: approved
---
# Status Policy Fixture

Index for status policy tests.
""",
    )

    visible_statuses = ("evergreen", "current", "mature", "developing")
    blocking_statuses = ("blocked", "private", "unpublished", "draft")
    for status in visible_statuses:
        write_markdown(
            root / f"visible-{status}.md",
            f"""
---
title: Visible {status.title()}
status: {status}
---
# Visible {status.title()}

zzvisible{status} is served for lifecycle status {status}.
""",
        )
    for status in blocking_statuses:
        write_markdown(
            root / f"blocked-{status}.md",
            f"""
---
title: Blocked {status.title()}
status: {status}
---
# Blocked {status.title()}

zzblocked{status} is served only when drafts are included.
""",
        )

    service = LlmWikiService(root)
    default_nodes = {node["id"] for node in service.graph()["nodes"]}
    draft_nodes = {node["id"] for node in service.graph(include_drafts=True)["nodes"]}

    assert service.manifest().approved_page_count == 1 + len(visible_statuses)
    for status in visible_statuses:
        page_id = f"visible-{status}"
        assert service.search(f"zzvisible{status}")[0]["page_id"] == page_id
        assert service.read(page_id)["status"] == status
        assert f"page:{page_id}" in default_nodes

    for status in blocking_statuses:
        page_id = f"blocked-{status}"
        assert service.search(f"zzblocked{status}") == []
        assert service.search(f"zzblocked{status}", include_drafts=True)[0]["page_id"] == page_id
        assert service.read(page_id) == {
            "found": False,
            "reason": "not approved for serving",
        }
        assert service.read(page_id, include_drafts=True)["status"] == status
        assert f"page:{page_id}" not in default_nodes
        assert f"page:{page_id}" in draft_nodes


def test_status_visibility_refreshes_across_search_context_read_and_graph(
    tmp_path: Path,
) -> None:
    root = tmp_path / "status-runtime-wiki"
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Status Runtime Fixture
review_state: approved
---
# Status Runtime Fixture

Index for status runtime tests.
""",
    )
    topic = root / "topic.md"
    write_markdown(
        topic,
        """
---
title: Status Topic
status: approved
---
# Status Topic

zzstatusapproved is visible while status is approved.
""",
    )
    service = LlmWikiService(root)

    assert service.manifest().approved_page_count == 2
    assert service.search("zzstatusapproved")[0]["page_id"] == "topic"
    assert service.context("zzstatusapproved").answerable is True
    assert service.read("topic")["path"] == "topic.md"
    assert "page:topic" in {node["id"] for node in service.graph()["nodes"]}

    write_markdown(
        topic,
        """
---
title: Status Topic
status: blocked
---
# Status Topic

zzstatusblocked is visible only when drafts are included.
""",
    )
    stat = topic.stat()
    os.utime(topic, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    blocked_context = service.context("zzstatusblocked")
    assert service.manifest().approved_page_count == 1
    assert service.search("zzstatusblocked") == []
    assert blocked_context.answerable is False
    assert blocked_context.evidence == []
    assert service.search("zzstatusblocked", include_drafts=True)[0]["page_id"] == "topic"
    assert service.read("topic") == {"found": False, "reason": "not approved for serving"}
    assert "page:topic" not in {node["id"] for node in service.graph()["nodes"]}
    assert "page:topic" in {node["id"] for node in service.graph(include_drafts=True)["nodes"]}

    write_markdown(
        topic,
        """
---
title: Status Topic
status: published
---
# Status Topic

zzstatuspublished is visible after status is published.
""",
    )
    stat = topic.stat()
    os.utime(topic, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))

    assert service.manifest().approved_page_count == 2
    assert service.search("zzstatusblocked") == []
    assert service.search("zzstatuspublished")[0]["page_id"] == "topic"
    assert service.context("zzstatuspublished").answerable is True
    assert service.read("topic")["status"] == "published"
    assert "page:topic" in {node["id"] for node in service.graph()["nodes"]}


def test_service_auto_refreshes_markdown_and_sidecar_changes(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Refresh Fixture
review_state: approved
---
# Refresh Fixture

Start with [[topic]].
""",
    )
    write_markdown(
        root / "topic.md",
        """
---
title: Topic
review_state: approved
---
# Topic

Initial body.
""",
    )
    service = LlmWikiService(root)

    assert service.search("zznewlyindexedphrase") == []
    write_markdown(
        root / "topic.md",
        """
---
title: Topic
review_state: approved
---
# Topic

Initial body plus zznewlyindexedphrase after service start.
""",
    )
    assert any(item["page_id"] == "topic" for item in service.search("zznewlyindexedphrase"))

    assert all(
        edge["relation"] != "supports" for edge in service.graph(include_drafts=True)["edges"]
    )
    graph_dir = root / "graph"
    graph_dir.mkdir()
    graph_json = graph_dir / "graph.json"
    graph_json.write_text(
        """
{
  "edges": [
    {"from": "index", "to": "topic", "type": "supports", "confidence": 0.64}
  ]
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    refreshed_edges = service.graph(include_drafts=True)["edges"]
    assert any(
        edge["source"] == "page:index"
        and edge["target"] == "page:topic"
        and edge["relation"] == "supports"
        and edge["metadata"].get("confidence") == 0.64
        for edge in refreshed_edges
    )

    graph_json.write_text(
        """
{
  "edges": [
    {"from": "index", "to": "topic", "type": "blocks", "confidence": 0.5}
  ]
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    stat = graph_json.stat()
    os.utime(graph_json, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    changed_edges = service.graph(include_drafts=True)["edges"]
    assert any(edge["relation"] == "blocks" for edge in changed_edges)
    assert all(edge["relation"] != "supports" for edge in changed_edges)

    graph_json.unlink()

    deleted_edges = service.graph(include_drafts=True)["edges"]
    assert all(edge["relation"] not in {"blocks", "supports"} for edge in deleted_edges)


def test_service_reuses_source_signature_scan_between_manifest_and_query(monkeypatch) -> None:
    real_walk = os.walk
    walk_calls = 0

    def counting_walk(*args: Any, **kwargs: Any) -> Any:
        nonlocal walk_calls
        walk_calls += 1
        return real_walk(*args, **kwargs)

    monkeypatch.setattr("llmwiki_serve.service.os.walk", counting_walk)

    service = LlmWikiService(FIXTURE)

    assert service.manifest().page_count == 5
    assert service.manifest().approved_page_count == 4
    assert service.context("required copy").answerable
    assert service.context("requester return").answerable
    assert walk_calls == 1


def test_service_reuses_search_corpus_until_projection_changes(monkeypatch) -> None:
    import llmwiki_serve.service as service_module

    real_build_search_corpus = service_module.build_search_corpus
    build_calls = 0

    def counting_build_search_corpus(*args: Any, **kwargs: Any) -> Any:
        nonlocal build_calls
        build_calls += 1
        return real_build_search_corpus(*args, **kwargs)

    monkeypatch.setattr(service_module, "build_search_corpus", counting_build_search_corpus)

    service = LlmWikiService(FIXTURE)
    index = service.index()
    expected = [item.model_dump() for item in raw_search(index, "required copy", limit=4)]

    assert service.search("required copy", limit=4) == expected
    assert build_calls == 1
    assert service.search("requester return", limit=4)
    assert build_calls == 1
    assert service.context("required copy", limit=4).answerable
    assert build_calls == 1
    assert service.search("draft", limit=4, include_drafts=True)
    assert build_calls == 2

    service.index(refresh=True)
    assert service.search("required copy", limit=4) == expected
    assert build_calls == 3


def test_graph_neighbors_does_not_build_search_corpus(monkeypatch) -> None:
    import llmwiki_serve.service as service_module

    build_calls = 0
    real_build_search_corpus = service_module.build_search_corpus

    def wrapped_build_search_corpus(*args: Any, **kwargs: Any) -> Any:
        nonlocal build_calls
        build_calls += 1
        return real_build_search_corpus(*args, **kwargs)

    monkeypatch.setattr(service_module, "build_search_corpus", wrapped_build_search_corpus)

    service = LlmWikiService(FIXTURE)

    assert service.graph_neighbors(seeds=["hot"], depth=1).nodes
    assert service.graph(limit=20)["nodes"]
    assert build_calls == 0
    assert service.search("required copy", limit=4)
    assert build_calls == 1


def test_memory_projection_store_matches_default_http_payloads() -> None:
    default_client = TestClient(create_app(FIXTURE))
    store_client = TestClient(create_app(FIXTURE, projection_store=InMemoryProjectionStore()))

    assert store_client.get("/manifest").json() == default_client.get("/manifest").json()
    assert store_client.get("/source-bundle").json() == default_client.get("/source-bundle").json()
    assert store_client.post("/query", json={"query": "required copy"}).json() == (
        default_client.post("/query", json={"query": "required copy"}).json()
    )
    assert store_client.post("/search", json={"query": "required copy"}).json() == (
        default_client.post("/search", json={"query": "required copy"}).json()
    )
    assert store_client.get("/read/hot").json() == default_client.get("/read/hot").json()
    assert store_client.get("/graph").json() == default_client.get("/graph").json()


def test_projection_store_hit_skips_wiki_builder(monkeypatch) -> None:
    import llmwiki_serve.service as service_module

    seeded_index = LlmWikiService(FIXTURE).index()
    get_calls: list[ProjectionKey] = []
    put_calls: list[ProjectionRecord] = []

    class HitStore:
        def get(self, key: ProjectionKey, *, root: Path) -> ProjectionRecord | None:
            get_calls.append(key)
            return ProjectionRecord(key=key, index=seeded_index)

        def put(self, record: ProjectionRecord) -> None:
            put_calls.append(record)

        def invalidate_source(self, *, namespace: str, source_id: str) -> None:
            raise AssertionError("invalidate_source should not be called")

    def forbidden_builder(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("wiki builder should not run on projection-store hit")

    monkeypatch.setattr(service_module, "load_wiki", forbidden_builder)
    monkeypatch.setattr(service_module, "project_wiki", forbidden_builder)

    service = LlmWikiService(
        FIXTURE,
        projection_store=HitStore(),
        cache_namespace="pytest",
        source_id="sample-packaging-llmwiki",
    )

    assert service.manifest().page_count == 5
    assert service.search("required copy")
    assert [key.source_id for key in get_calls] == ["sample-packaging-llmwiki"]
    assert put_calls == []


def test_projection_store_miss_writes_and_fresh_service_can_hydrate_hit(monkeypatch) -> None:
    import llmwiki_serve.service as service_module

    real_project_wiki = service_module.project_wiki
    project_calls = 0

    def counting_project_wiki(*args: Any, **kwargs: Any) -> Any:
        nonlocal project_calls
        project_calls += 1
        return real_project_wiki(*args, **kwargs)

    monkeypatch.setattr(service_module, "project_wiki", counting_project_wiki)
    store = InMemoryProjectionStore()
    first = LlmWikiService(
        FIXTURE,
        projection_store=store,
        cache_namespace="pytest",
        source_id="sample-packaging-llmwiki",
    )

    assert first.manifest().page_count == 5
    assert project_calls == 1

    def forbidden_builder(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("fresh service should hydrate projection-store hit")

    monkeypatch.setattr(service_module, "load_wiki", forbidden_builder)
    monkeypatch.setattr(service_module, "project_wiki", forbidden_builder)
    second = LlmWikiService(
        FIXTURE,
        projection_store=store,
        cache_namespace="pytest",
        source_id="sample-packaging-llmwiki",
    )

    assert second.manifest().page_count == 5
    assert second.search("required copy")


def test_projection_record_payload_excludes_absolute_root_and_hydrates_local_root(
    tmp_path: Path,
) -> None:
    index = LlmWikiService(FIXTURE).index()
    key = ProjectionKey(
        namespace="pytest",
        source_id="sample-packaging-llmwiki",
        projection_signature="sha256:test",
    )
    payload = record_to_payload(ProjectionRecord(key=key, index=index))

    encoded = json.dumps(payload)
    assert str(FIXTURE) not in encoded
    assert "root" not in payload["index"]

    hydrated = projection_record_from_payload(key, payload, root=tmp_path)
    assert hydrated.index.root == tmp_path
    assert hydrated.index.title == index.title
    assert hydrated.index.pages == index.pages


def test_redis_projection_store_uses_namespaced_keys_and_round_trips_without_paths() -> None:
    client = FakeRedisClient()
    store = RedisProjectionStore(url="redis://example.invalid/0", client=client)
    index = LlmWikiService(FIXTURE).index()
    key = ProjectionKey(
        namespace="pytest",
        source_id="sample-packaging-llmwiki",
        projection_signature="sha256:test",
    )
    record = ProjectionRecord(key=key, index=index)

    store.put(record)
    redis_key = redis_projection_key(key)

    assert redis_key == (
        "llmwiki:pytest:projections:projection-store-v1:sample-packaging-llmwiki:sha256_test"
    )
    assert redis_key in client.values
    assert str(FIXTURE) not in client.values[redis_key]
    assert client.values["llmwiki:pytest:sources:sample-packaging-llmwiki:latest"] == (
        "sha256:test"
    )

    hydrated = store.get(key, root=Path("/served/wiki"))
    assert hydrated is not None
    assert hydrated.index.root == Path("/served/wiki")
    assert hydrated.index.title == index.title


def test_redis_projection_store_treats_corrupt_payload_as_cache_miss() -> None:
    client = FakeRedisClient()
    store = RedisProjectionStore(url="redis://example.invalid/0", client=client)
    key = ProjectionKey(
        namespace="pytest",
        source_id="sample-packaging-llmwiki",
        projection_signature="sha256:test",
    )
    client.values[redis_projection_key(key)] = "{not valid json"

    assert store.get(key, root=FIXTURE) is None
    assert "JSONDecodeError" in store.last_error
    assert store.available is True


def test_redis_projection_store_invalidates_source_projection_keys() -> None:
    client = FakeRedisClient()
    store = RedisProjectionStore(url="redis://example.invalid/0", client=client)
    index = LlmWikiService(FIXTURE).index()
    keys = [
        ProjectionKey(
            namespace="pytest",
            source_id="sample-packaging-llmwiki",
            projection_signature="sha256:one",
        ),
        ProjectionKey(
            namespace="pytest",
            source_id="sample-packaging-llmwiki",
            projection_signature="sha256:two",
        ),
        ProjectionKey(
            namespace="pytest",
            source_id="other-source",
            projection_signature="sha256:one",
        ),
    ]
    for key in keys:
        store.put(ProjectionRecord(key=key, index=index))

    store.invalidate_source(namespace="pytest", source_id="sample-packaging-llmwiki")

    assert redis_projection_key(keys[0]) not in client.values
    assert redis_projection_key(keys[1]) not in client.values
    assert redis_latest_key("pytest", "sample-packaging-llmwiki") not in client.values
    assert redis_projection_key(keys[2]) in client.values
    assert redis_latest_key("pytest", "other-source") in client.values


def test_redis_projection_store_falls_back_to_local_memory_after_client_failure() -> None:
    client = FakeRedisClient(fail=True)
    store = RedisProjectionStore(url="redis://example.invalid/0", client=client)
    index = LlmWikiService(FIXTURE).index()
    key = ProjectionKey(
        namespace="pytest",
        source_id="sample-packaging-llmwiki",
        projection_signature="sha256:test",
    )
    record = ProjectionRecord(key=key, index=index)

    store.put(record)

    assert store.available is False
    assert store.get(key, root=FIXTURE) == record


def test_redis_projection_store_missing_extra_error_is_actionable(monkeypatch) -> None:
    real_import = builtins.__import__

    def blocked_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "redis":
            raise ModuleNotFoundError("No module named 'redis'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)

    with pytest.raises(RuntimeError, match=r"llmwiki-serve\[redis\]"):
        RedisProjectionStore(url="redis://127.0.0.1:6379/0")

    assert 'pip install "llmwiki-serve[redis]"' in REDIS_EXTRA_MESSAGE


def test_projection_store_diagnostics_redacts_redis_url_and_local_root() -> None:
    client = TestClient(
        create_app(
            FIXTURE,
            projection_store=RedisProjectionStore(
                url="redis://:secret@example.invalid/0",
                client=FakeRedisClient(fail=True),
            ),
            cache_namespace="pytest",
            source_id="sample-packaging-llmwiki",
        )
    )

    client.get("/manifest")
    diagnostics = client.get("/diagnostics/projection-store").json()
    encoded = json.dumps(diagnostics)

    assert diagnostics["backend"] == "RedisProjectionStore"
    assert diagnostics["namespace"] == "pytest"
    assert diagnostics["cache_source_id"] == "sample-packaging-llmwiki"
    assert diagnostics["available"] is False
    assert "secret" not in encoded
    assert "example.invalid" not in encoded
    assert str(FIXTURE) not in encoded


def test_service_signature_cache_refreshes_when_source_file_changes(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Signature Cache Fixture
review_state: approved
---
# Signature Cache Fixture

Start with [[topic]].
""",
    )
    topic = root / "topic.md"
    write_markdown(
        topic,
        """
---
title: Topic
review_state: approved
---
# Topic

Initial body.
""",
    )
    real_walk = os.walk
    walk_calls = 0

    def counting_walk(*args: Any, **kwargs: Any) -> Any:
        nonlocal walk_calls
        walk_calls += 1
        return real_walk(*args, **kwargs)

    monkeypatch.setattr("llmwiki_serve.service.os.walk", counting_walk)

    service = LlmWikiService(root)

    assert service.search("zzcacheinvalidationphrase") == []
    initial_signature = service._signature
    assert initial_signature is not None
    assert service.manifest().page_count == 2
    assert walk_calls == 1

    write_markdown(
        topic,
        """
---
title: Topic
review_state: approved
---
# Topic

Initial body plus zzcacheinvalidationphrase after service start.
""",
    )
    stat = topic.stat()
    os.utime(topic, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    assert any(item["page_id"] == "topic" for item in service.search("zzcacheinvalidationphrase"))
    assert service._signature != initial_signature
    assert service.manifest().page_count == 2
    assert walk_calls == 2

    write_markdown(
        topic,
        """
---
title: Topic
draft: true
---
# Topic

Initial body plus cache invalidation phrase after service start.
""",
    )
    stat = topic.stat()
    os.utime(topic, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    assert all(item["page_id"] != "topic" for item in service.search("cache invalidation phrase"))
    assert any(
        item["page_id"] == "topic"
        for item in service.search("cache invalidation phrase", include_drafts=True)
    )
    assert service.manifest().approved_page_count == 1
    assert walk_calls == 3


def test_service_signature_cache_refreshes_when_source_file_inode_changes_only(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Signature Cache Inode Fixture
review_state: approved
---
# Signature Cache Inode Fixture

Start with [[topic]].
""",
    )
    topic = root / "topic.md"

    def topic_content(body: str) -> str:
        return f"---\ntitle: Topic\nreview_state: approved\n---\n# Topic\n\n{body}\n"

    initial_content = topic_content("Initial stale body AAAA")
    replacement_content = topic_content("Updated fresh body BBBB")
    assert len(initial_content.encode("utf-8")) == len(replacement_content.encode("utf-8"))

    topic.write_text(initial_content, encoding="utf-8")
    requested_mtime_ns = 1_700_000_000_123_456_789
    os.utime(topic, ns=(requested_mtime_ns, requested_mtime_ns))

    service = LlmWikiService(root)
    assert "Initial stale body AAAA" in service.read("topic")["text"]

    before_signature = source_signature(root)
    before_stat = topic.stat()

    replacement = root / ".topic.md.tmp"
    replacement.write_text(replacement_content, encoding="utf-8")
    os.utime(replacement, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))
    os.replace(replacement, topic)

    after_stat = topic.stat()
    assert (after_stat.st_dev, after_stat.st_ino) != (before_stat.st_dev, before_stat.st_ino)
    assert after_stat.st_size == before_stat.st_size
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert source_signature(root) == before_signature

    refreshed = service.read("topic")
    assert "Updated fresh body BBBB" in refreshed["text"]
    assert "Initial stale body AAAA" not in refreshed["text"]


def test_service_signature_cache_refreshes_when_file_stat_is_preserved(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Signature Cache Digest Fixture
review_state: approved
---
# Signature Cache Digest Fixture

Start with [[topic]].
""",
    )
    topic = root / "topic.md"

    def topic_content(body: str) -> str:
        return f"---\ntitle: Topic\nreview_state: approved\n---\n# Topic\n\n{body}\n"

    initial_content = topic_content("Initial stat body AAAA")
    replacement_content = topic_content("Updated stat body BBBB")
    assert len(initial_content.encode("utf-8")) == len(replacement_content.encode("utf-8"))

    topic.write_text(initial_content, encoding="utf-8")
    requested_mtime_ns = 1_710_000_000_123_456_789
    os.utime(topic, ns=(requested_mtime_ns, requested_mtime_ns))

    service = LlmWikiService(root)
    assert "Initial stat body AAAA" in service.read("topic")["text"]

    before_signature = source_signature(root)
    before_stat = topic.stat()
    topic.write_text(replacement_content, encoding="utf-8")
    os.utime(topic, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))

    after_stat = topic.stat()
    assert after_stat.st_ino == before_stat.st_ino
    assert after_stat.st_size == before_stat.st_size
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert source_signature(root) == before_signature

    refreshed = service.read("topic")
    assert "Updated stat body BBBB" in refreshed["text"]
    assert "Initial stat body AAAA" not in refreshed["text"]


def test_service_default_strict_refresh_detects_immediate_same_stat_rewrite(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Strict Refresh Fixture
review_state: approved
---
# Strict Refresh Fixture

Start with [[topic]].
""",
    )
    topic = root / "topic.md"

    def topic_content(body: str) -> str:
        return f"---\ntitle: Topic\nreview_state: approved\n---\n# Topic\n\n{body}\n"

    initial_content = topic_content("Strict one body AAAA")
    replacement_content = topic_content("Strict two body BBBB")
    assert len(initial_content.encode("utf-8")) == len(replacement_content.encode("utf-8"))

    topic.write_text(initial_content, encoding="utf-8")
    requested_mtime_ns = 1_730_000_000_123_456_789
    os.utime(topic, ns=(requested_mtime_ns, requested_mtime_ns))

    service = LlmWikiService(root)
    assert "Strict one body AAAA" in service.read("topic")["text"]

    before_stat = topic.stat()
    topic.write_text(replacement_content, encoding="utf-8")
    os.utime(topic, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))

    after_stat = topic.stat()
    assert after_stat.st_size == before_stat.st_size
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns

    refreshed = service.read("topic")
    assert "Strict two body BBBB" in refreshed["text"]
    assert "Strict one body AAAA" not in refreshed["text"]


def test_service_refresh_interval_reuses_then_refreshes_and_allows_explicit_refresh(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Refresh Interval Fixture
review_state: approved
---
# Refresh Interval Fixture

Start with [[topic]].
""",
    )
    topic = root / "topic.md"

    def topic_content(body: str) -> str:
        return f"---\ntitle: Topic\nreview_state: approved\n---\n# Topic\n\n{body}\n"

    initial_content = topic_content("Version one body AAAA")
    interval_content = topic_content("Version two body BBBB")
    explicit_content = topic_content("Version tri body CCCC")
    assert len(initial_content.encode("utf-8")) == len(interval_content.encode("utf-8"))
    assert len(interval_content.encode("utf-8")) == len(explicit_content.encode("utf-8"))

    topic.write_text(initial_content, encoding="utf-8")
    requested_mtime_ns = 1_740_000_000_123_456_789
    os.utime(topic, ns=(requested_mtime_ns, requested_mtime_ns))

    now = 0.0

    def clock() -> float:
        return now

    real_walk = os.walk
    walk_calls = 0

    def counting_walk(*args: Any, **kwargs: Any) -> Any:
        nonlocal walk_calls
        walk_calls += 1
        return real_walk(*args, **kwargs)

    monkeypatch.setattr("llmwiki_serve.service.os.walk", counting_walk)

    service = LlmWikiService(root, refresh_interval_seconds=10.0, clock=clock)
    assert "Version one body AAAA" in service.read("topic")["text"]
    assert walk_calls == 1

    before_stat = topic.stat()
    topic.write_text(interval_content, encoding="utf-8")
    os.utime(topic, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))

    now = 9.0
    cached = service.read("topic")
    assert "Version one body AAAA" in cached["text"]
    assert "Version two body BBBB" not in cached["text"]
    assert walk_calls == 1

    now = 10.0
    interval_refreshed = service.read("topic")
    assert "Version two body BBBB" in interval_refreshed["text"]
    assert "Version one body AAAA" not in interval_refreshed["text"]
    assert walk_calls == 2

    before_stat = topic.stat()
    topic.write_text(explicit_content, encoding="utf-8")
    os.utime(topic, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))

    now = 11.0
    still_cached = service.read("topic")
    assert "Version two body BBBB" in still_cached["text"]
    assert "Version tri body CCCC" not in still_cached["text"]
    assert walk_calls == 2

    service.index(refresh=True)
    explicitly_refreshed = service.read("topic")
    assert "Version tri body CCCC" in explicitly_refreshed["text"]
    assert "Version two body BBBB" not in explicitly_refreshed["text"]
    assert walk_calls == 3


def test_producer_manifest_reuses_until_marker_changes(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    manifest = root / ".llmwiki-producer-manifest.json"
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

zzfirstproducerneedle.
""",
    )
    manifest.write_text('{"build":1}\n', encoding="utf-8")

    service = LlmWikiService(root, producer_manifest_path=manifest.name)

    assert "zzfirstproducerneedle" in service.read("index")["text"]

    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

zzsecondproducerneedle.
""",
    )

    assert "zzsecondproducerneedle" not in service.read("index")["text"]
    assert "zzfirstproducerneedle" in service.read("index")["text"]

    manifest.write_text('{"build":2}\n', encoding="utf-8")

    assert "zzsecondproducerneedle" in service.read("index")["text"]
    assert "zzfirstproducerneedle" not in service.read("index")["text"]


def test_producer_manifest_public_identity_is_content_derived(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    manifest = root / ".llmwiki-producer-manifest.json"
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Producer Identity Wiki
review_state: approved
source_refs: [PRODUCER-SRC-001]
---
# Producer Identity Wiki

zzproduceridentityfirst.
""",
    )
    manifest.write_text('{"build":1}\n', encoding="utf-8")

    service = LlmWikiService(root, producer_manifest_path=manifest.name)

    initial = service.manifest()
    initial_bundle = service.source_bundle()
    strict_initial = LlmWikiService(root).manifest()
    assert initial.projection.signature == strict_initial.projection.signature
    assert initial.bundle_id == strict_initial.bundle_id
    assert initial_bundle.projection.signature == initial.projection.signature
    assert initial_bundle.bundle_id == initial.bundle_id

    manifest.write_text('{"build":2}\n', encoding="utf-8")

    marker_only = service.manifest()
    marker_only_bundle = service.source_bundle()
    assert marker_only.projection.signature == initial.projection.signature
    assert marker_only.bundle_id == initial.bundle_id
    assert marker_only_bundle.projection.signature == initial.projection.signature
    assert marker_only_bundle.bundle_id == initial.bundle_id

    write_markdown(
        root / "index.md",
        """
---
wiki_title: Producer Identity Wiki
review_state: approved
source_refs: [PRODUCER-SRC-001]
---
# Producer Identity Wiki

zzproduceridentitysecond.
""",
    )

    stale = service.manifest()
    assert stale.projection.signature == initial.projection.signature
    assert stale.bundle_id == initial.bundle_id
    assert "zzproduceridentityfirst" in service.read("index")["text"]
    assert "zzproduceridentitysecond" not in service.read("index")["text"]

    manifest.write_text('{"build":3}\n', encoding="utf-8")

    refreshed = service.manifest()
    refreshed_bundle = service.source_bundle()
    strict_refreshed = LlmWikiService(root).manifest()
    assert "zzproduceridentitysecond" in service.read("index")["text"]
    assert "zzproduceridentityfirst" not in service.read("index")["text"]
    assert refreshed.projection.signature != initial.projection.signature
    assert refreshed.projection.signature == strict_refreshed.projection.signature
    assert refreshed.bundle_id == strict_refreshed.bundle_id
    assert refreshed_bundle.projection.signature == refreshed.projection.signature
    assert refreshed_bundle.bundle_id == refreshed.bundle_id


def test_producer_manifest_reuses_content_signature_until_marker_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    manifest = root / ".llmwiki-producer-manifest.json"
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Producer Scan Wiki
review_state: approved
---
# Producer Scan Wiki

zzproducerscanfirst.
""",
    )
    manifest.write_text('{"build":1}\n', encoding="utf-8")

    real_walk = os.walk
    walk_calls = 0

    def counting_walk(*args: Any, **kwargs: Any) -> Any:
        nonlocal walk_calls
        walk_calls += 1
        return real_walk(*args, **kwargs)

    monkeypatch.setattr("llmwiki_serve.service.os.walk", counting_walk)

    service = LlmWikiService(root, producer_manifest_path=manifest.name)
    assert service.manifest().page_count == 1
    assert walk_calls == 1

    assert service.manifest().approved_page_count == 1
    assert service.source_bundle().title == "Producer Scan Wiki"
    assert "zzproducerscanfirst" in service.read("index")["text"]
    assert walk_calls == 1

    write_markdown(
        root / "index.md",
        """
---
wiki_title: Producer Scan Wiki
review_state: approved
---
# Producer Scan Wiki

zzproducerscansecond.
""",
    )

    assert "zzproducerscanfirst" in service.read("index")["text"]
    assert "zzproducerscansecond" not in service.read("index")["text"]
    assert walk_calls == 1

    manifest.write_text('{"build":2}\n', encoding="utf-8")

    assert "zzproducerscansecond" in service.read("index")["text"]
    assert walk_calls == 2
    assert service.manifest().page_count == 1
    assert walk_calls == 2


def test_missing_producer_manifest_falls_back_to_strict_source_scan(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

zzfirstfallbackneedle.
""",
    )

    service = LlmWikiService(root, producer_manifest_path=".missing-producer-manifest.json")

    assert "zzfirstfallbackneedle" in service.read("index")["text"]

    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

zzsecondfallbackneedle.
""",
    )

    assert "zzsecondfallbackneedle" in service.read("index")["text"]
    assert "zzfirstfallbackneedle" not in service.read("index")["text"]


def test_outside_root_producer_manifest_falls_back_to_strict_source_scan(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    outside = tmp_path / ".llmwiki-producer-manifest.json"
    outside.write_text('{"build":1}\n', encoding="utf-8")
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

zzfirstoutsideneedle.
""",
    )

    service = LlmWikiService(root, producer_manifest_path=outside)

    assert "zzfirstoutsideneedle" in service.read("index")["text"]

    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

zzsecondoutsideneedle.
""",
    )

    assert "zzsecondoutsideneedle" in service.read("index")["text"]
    assert "zzfirstoutsideneedle" not in service.read("index")["text"]


def test_symlinked_producer_manifest_falls_back_to_strict_source_scan(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    manifest_target = root / ".llmwiki-producer-manifest-target.json"
    manifest_link = root / ".llmwiki-producer-manifest.json"
    manifest_target.write_text('{"build":1}\n', encoding="utf-8")
    try:
        manifest_link.symlink_to(manifest_target)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

zzfirstsymlinkneedle.
""",
    )

    service = LlmWikiService(root, producer_manifest_path=manifest_link.name)

    assert "zzfirstsymlinkneedle" in service.read("index")["text"]

    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

zzsecondsymlinkneedle.
""",
    )

    assert "zzsecondsymlinkneedle" in service.read("index")["text"]
    assert "zzfirstsymlinkneedle" not in service.read("index")["text"]


def test_service_signature_cache_refreshes_when_sidecar_file_stat_is_preserved(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    graph = root / "graph"
    graph.mkdir(parents=True)
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Signature Cache Sidecar Digest Fixture
review_state: approved
---
# Signature Cache Sidecar Digest Fixture

Start with [[topic]].
""",
    )
    write_markdown(
        root / "topic.md",
        """
---
title: Topic
review_state: approved
---
# Topic

Target page.
""",
    )
    graph_json = graph / "graph.json"

    def sidecar_content(relation: str) -> str:
        return (
            json.dumps(
                {
                    "edges": [
                        {
                            "from": "index",
                            "to": "topic",
                            "type": relation,
                            "confidence": 0.64,
                        }
                    ]
                },
                separators=(",", ":"),
            )
            + "\n"
        )

    initial_content = sidecar_content("supports")
    replacement_content = sidecar_content("requires")
    assert len(initial_content.encode("utf-8")) == len(replacement_content.encode("utf-8"))

    graph_json.write_text(initial_content, encoding="utf-8")
    requested_mtime_ns = 1_720_000_000_123_456_789
    os.utime(graph_json, ns=(requested_mtime_ns, requested_mtime_ns))

    service = LlmWikiService(root)
    assert any(edge["relation"] == "supports" for edge in service.graph()["edges"])

    before_signature = source_signature(root)
    before_stat = graph_json.stat()
    graph_json.write_text(replacement_content, encoding="utf-8")
    os.utime(graph_json, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))

    after_stat = graph_json.stat()
    assert after_stat.st_ino == before_stat.st_ino
    assert after_stat.st_size == before_stat.st_size
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert source_signature(root) == before_signature

    refreshed_edges = service.graph()["edges"]
    assert any(edge["relation"] == "requires" for edge in refreshed_edges)
    assert all(edge["relation"] != "supports" for edge in refreshed_edges)


def test_service_signature_cache_refreshes_when_new_source_file_directory_stat_is_preserved(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Signature Cache Directory Digest Fixture
review_state: approved
---
# Signature Cache Directory Digest Fixture

Start without the later topic.
""",
    )
    service = LlmWikiService(root)

    assert service.search("zzpreservedaddneedle") == []

    before_stat = root.stat()
    write_markdown(
        root / "added.md",
        """
---
title: Added
review_state: approved
---
# Added

zzpreservedaddneedle appears after service start.
""",
    )
    os.utime(root, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))

    after_stat = root.stat()
    assert after_stat.st_size == before_stat.st_size
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert service.search("zzpreservedaddneedle")[0]["page_id"] == "added"


def test_service_signature_cache_refreshes_when_new_sidecar_directory_stat_is_preserved(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    graph = root / "graph"
    graph.mkdir(parents=True)
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Signature Cache Sidecar Directory Digest Fixture
review_state: approved
---
# Signature Cache Sidecar Directory Digest Fixture

Start with [[topic]].
""",
    )
    write_markdown(
        root / "topic.md",
        """
---
title: Topic
review_state: approved
---
# Topic

Target page.
""",
    )
    service = LlmWikiService(root)

    assert all(edge["relation"] != "adds_sidecar" for edge in service.graph()["edges"])

    before_stat = graph.stat()
    (graph / "graph.json").write_text(
        """
{
  "edges": [
    {"from": "index", "to": "topic", "type": "adds_sidecar", "confidence": 0.64}
  ]
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    os.utime(graph, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))

    after_stat = graph.stat()
    assert after_stat.st_size == before_stat.st_size
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert any(edge["relation"] == "adds_sidecar" for edge in service.graph()["edges"])


def test_service_signature_cache_refreshes_when_source_files_are_added_and_deleted(
    tmp_path: Path, monkeypatch
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Signature Cache Add Delete Fixture
review_state: approved
---
# Signature Cache Add Delete Fixture

Start empty.
""",
    )
    real_walk = os.walk
    walk_calls = 0

    def counting_walk(*args: Any, **kwargs: Any) -> Any:
        nonlocal walk_calls
        walk_calls += 1
        return real_walk(*args, **kwargs)

    monkeypatch.setattr("llmwiki_serve.service.os.walk", counting_walk)

    service = LlmWikiService(root)

    assert service.search("zzfreshadditionphrase") == []
    assert walk_calls == 1

    added = root / "added.md"
    write_markdown(
        added,
        """
---
title: Added
review_state: approved
---
# Added

zzfreshadditionphrase appears after service start.
""",
    )
    root_stat = root.stat()
    os.utime(root, ns=(root_stat.st_atime_ns, root_stat.st_mtime_ns + 1_000_000_000))

    assert any(item["page_id"] == "added" for item in service.search("zzfreshadditionphrase"))
    assert walk_calls == 2

    added.unlink()
    root_stat = root.stat()
    os.utime(root, ns=(root_stat.st_atime_ns, root_stat.st_mtime_ns + 1_000_000_000))

    assert service.search("zzfreshadditionphrase") == []
    assert walk_calls == 3


def test_service_signature_cache_refreshes_when_missing_root_is_created(tmp_path: Path) -> None:
    root = tmp_path / "late-wiki"
    service = LlmWikiService(root)

    try:
        service.manifest()
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("missing roots should fail until the wiki is created")

    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Late Wiki
review_state: approved
---
# Late Wiki

late root creation phrase.
""",
    )

    assert service.manifest().title == "Late Wiki"
    assert service.search("late root creation phrase")[0]["page_id"] == "index"


def test_source_signature_ignores_runtime_dirs_relative_to_root_only(tmp_path: Path) -> None:
    build_parent = tmp_path / "build" / "wiki"
    build_parent.mkdir(parents=True)
    write_markdown(
        build_parent / "index.md",
        """
---
review_state: approved
---
# Index

Content under a parent directory named build still participates in refresh.
""",
    )
    ignored_dir = build_parent / "node_modules"
    ignored_dir.mkdir()
    (ignored_dir / "ignored.md").write_text("# Ignored\n", encoding="utf-8")
    runtime_logs = build_parent / ".runtime-logs"
    runtime_logs.mkdir()
    (runtime_logs / "llmwiki-serve-io.jsonl").write_text(
        '{"event":"serve_io","body":"ignored runtime log"}\n',
        encoding="utf-8",
    )

    signature = source_signature(build_parent)

    assert any(relative == "index.md" for relative, _mtime, _size in signature)
    assert all(not relative.startswith("node_modules/") for relative, _mtime, _size in signature)
    assert all(not relative.startswith(".runtime-logs/") for relative, _mtime, _size in signature)


def test_source_signature_ignores_vscode_markdown_but_keeps_extension_marker(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    vscode = root / ".vscode"
    vscode.mkdir(parents=True)
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

Served page.
""",
    )
    write_markdown(
        vscode / "internal.md",
        """
# Internal

Ignored workspace Markdown.
""",
    )
    (vscode / "extensions.json").write_text(
        '{"recommendations":["foam.foam-vscode"]}\n',
        encoding="utf-8",
    )

    signature = source_signature(root)
    relatives = {relative for relative, _mtime, _size in signature}

    assert "index.md" in relatives
    assert ".vscode/extensions.json" in relatives
    assert ".vscode/internal.md" not in relatives


def test_service_auto_refreshes_adapter_marker_and_config_changes(tmp_path: Path) -> None:
    cases: tuple[tuple[str, Path, str | None, str], ...] = (
        ("obsidian", Path(".obsidian"), None, "obsidian"),
        ("foam-marker", Path(".foam"), None, "foam"),
        ("foam-marker-file", Path(".foam"), "", "foam"),
        (
            "foam-vscode",
            Path(".vscode") / "extensions.json",
            '{"recommendations":["foam.foam-vscode"]}\n',
            "foam",
        ),
        ("logseq-config", Path("logseq") / "config.edn", "{:meta/version 1}\n", "logseq"),
    )

    for case_name, marker_path, marker_content, expected_adapter in cases:
        root = tmp_path / case_name
        root.mkdir()
        write_markdown(
            root / "index.md",
            """
---
wiki_title: Marker Refresh Fixture
review_state: approved
---
# Marker Refresh Fixture

Generic Markdown page before marker creation.
""",
        )
        service = LlmWikiService(root)
        assert service.manifest().adapter == "generic-markdown"
        before_signature = source_signature(root)

        marker = root / marker_path
        if marker_content is None:
            marker.mkdir(parents=True)
        else:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(marker_content, encoding="utf-8")
        if expected_adapter == "logseq":
            pages = root / "pages"
            pages.mkdir()
            write_markdown(
                pages / "Marker Refresh.md",
                """
---
review_state: approved
---
# Marker Refresh

Logseq page added with the config marker.
""",
            )

        assert source_signature(root) != before_signature
        assert service.manifest().adapter == expected_adapter

        if marker_content is None:
            after_create_signature = source_signature(root)
            stat = marker.stat()
            os.utime(marker, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
            assert source_signature(root) != after_create_signature


def test_invalid_yaml_frontmatter_does_not_break_load_manifest_or_health(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Invalid YAML Fixture
review_state: approved
---
# Invalid YAML Fixture

Valid page.
""",
    )
    write_markdown(
        root / "bad.md",
        """
---
title: [bad
---
# Bad YAML Page

This malformed frontmatter page should still load.
""",
    )

    service = LlmWikiService(root)
    assert service.manifest().page_count == 2
    bad_page = service.read("bad")
    assert bad_page["title"] == "Bad YAML Page"
    assert bad_page["frontmatter"] == {}

    client = TestClient(create_app(root))
    assert client.get("/health").json()["status"] == "ok"


def test_default_cors_scopes_to_local_dev_origins() -> None:
    client = TestClient(create_app(FIXTURE))
    preflight_headers = {"access-control-request-method": "POST"}

    local = client.options(
        "/query",
        headers={"origin": "http://localhost:5173", **preflight_headers},
    )
    loopback = client.options(
        "/query",
        headers={"origin": "http://127.0.0.1:3000", **preflight_headers},
    )
    ipv6 = client.options(
        "/query",
        headers={"origin": "http://[::1]:5173", **preflight_headers},
    )
    foreign = client.options(
        "/query",
        headers={"origin": "https://example.com", **preflight_headers},
    )

    assert local.status_code == 200
    assert loopback.status_code == 200
    assert ipv6.status_code == 200
    assert local.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert loopback.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"
    assert ipv6.headers["access-control-allow-origin"] == "http://[::1]:5173"
    assert local.headers["access-control-allow-origin"] != "*"
    assert foreign.status_code == 403
    assert foreign.headers.get("access-control-allow-origin") is None


def test_app_factory_accepts_explicit_cors_origins() -> None:
    client = TestClient(create_app(FIXTURE, cors_origins=["https://viewer.example"]))

    response = client.options(
        "/health",
        headers={
            "origin": "https://viewer.example",
            "access-control-request-method": "GET",
        },
    )
    local = client.options(
        "/health",
        headers={
            "origin": "http://localhost:5173",
            "access-control-request-method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://viewer.example"
    assert local.status_code == 403
    assert local.headers.get("access-control-allow-origin") is None


def test_http_health_and_query() -> None:
    client = TestClient(create_app(FIXTURE))
    assert client.get("/health").json()["status"] == "ok"
    assert client.post("/query", json={"query": "required copy"}).json()["answerable"] is True


def test_malformed_network_inputs_use_client_errors_or_safe_defaults() -> None:
    client = TestClient(create_app(FIXTURE))
    a2a_client = TestClient(create_app(FIXTURE, enable_a2a_compat=True))

    invalid_query = client.post("/query", json={"query": "required copy", "limit": "many"})
    invalid_mcp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []},
    )
    default_a2a = client.post("/message:send", json={"message": {"parts": "not-a-list"}})
    loose_a2a = a2a_client.post("/message:send", json={"message": {"parts": "not-a-list"}})

    assert invalid_query.status_code == 422
    assert invalid_mcp.status_code == 422
    assert default_a2a.status_code == 404
    assert loose_a2a.status_code == 200
    loose_a2a_payload = loose_a2a.json()
    context_artifact_data = next(
        part["data"]
        for artifact in loose_a2a_payload["artifacts"]
        if artifact["name"] == "llmwiki_context"
        for part in artifact["parts"]
        if part["kind"] == "data"
    )
    assert context_artifact_data["answerable"] is True
    assert all("draft" not in item["path"] for item in context_artifact_data["evidence"])
    encoded = json.dumps(
        {
            "query": invalid_query.json(),
            "mcp": invalid_mcp.json(),
            "a2a": loose_a2a.json(),
        }
    )
    assert str(FIXTURE.resolve()) not in encoded


def test_quickstart_http_request_body_smoke() -> None:
    client = TestClient(create_app(FIXTURE))

    manifest = client.get("/manifest").json()
    query = client.post(
        "/query",
        json={"query": "required copy release readiness", "limit": 4},
    ).json()
    search = client.post(
        "/search",
        json={"query": "requester return", "limit": 5},
    ).json()
    read = client.get("/read/requester-return").json()
    graph = client.get("/graph?limit=120").json()

    assert manifest["title"] == "Sample Packaging LLMWiki"
    assert manifest["root"] == NETWORK_MANIFEST_ROOT
    assert "mcp-streamable-http" in manifest["capabilities"]
    assert "a2a-message" not in manifest["capabilities"]
    assert query["answerable"] is True
    assert query["evidence"]
    assert search["results"]
    assert read["title"] == "Requester Return"
    assert graph["nodes"]
    assert graph["edges"]


def test_mcp_tools_list_contains_context_and_graph() -> None:
    client = TestClient(create_app(FIXTURE))
    tools = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()

    tool_names = {tool["name"] for tool in tools["result"]["tools"]}
    descriptions = {tool["name"]: tool["description"] for tool in tools["result"]["tools"]}
    assert {"llmwiki_context", "llmwiki_graph", "llmwiki_graph_neighbors"} <= tool_names
    assert (
        "hot/index/overview or OpenWiki quickstart orientation first"
        in descriptions["llmwiki_context"]
    )
    assert "query-ranked citation evidence" in descriptions["llmwiki_context"]


def test_mcp_context_matches_http_query_shape() -> None:
    client = TestClient(create_app(FIXTURE))
    http_context = client.post("/query", json={"query": "requester return", "limit": 8}).json()
    mcp_context = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "llmwiki_context", "arguments": {"query": "requester return"}},
        },
    ).json()

    assert mcp_context["result"] == http_context


def test_mcp_graph_matches_http_graph_and_returns_page_nodes() -> None:
    client = TestClient(create_app(FIXTURE))
    http_graph = client.get("/graph?limit=500").json()
    mcp_graph = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "llmwiki_graph", "arguments": {"limit": 500}},
        },
    ).json()

    assert mcp_graph["result"] == http_graph
    assert any(node["id"].startswith("page:") for node in mcp_graph["result"]["nodes"])


def test_graph_neighbors_http_mcp_and_service_contract() -> None:
    native = Path(__file__).parent / "fixtures" / "native-wiki-root"
    service = LlmWikiService(native)
    client = TestClient(create_app(native))

    service_neighbors = service.graph_neighbors(
        seeds=["overview"],
        depth=1,
        direction="out",
        relations=["SUPPORTS"],
        limit=10,
    ).model_dump()
    http_neighbors = client.get(
        "/graph/neighborhood",
        params={
            "seed": "overview",
            "depth": "1",
            "direction": "out",
            "relation": "SUPPORTS",
            "limit": "10",
        },
    ).json()
    mcp_neighbors = mcp_tool_call(
        client,
        "llmwiki_graph_neighbors",
        {
            "seed": "overview",
            "depth": 1,
            "direction": "out",
            "relation": "SUPPORTS",
            "limit": 10,
        },
    )

    assert service_neighbors == http_neighbors == mcp_neighbors
    assert http_neighbors["seeds"] == ["page:overview"]
    assert http_neighbors["relations"] == ["supports"]
    assert [node["id"] for node in http_neighbors["nodes"]] == [
        "page:overview",
        "page:concepts/release",
    ]
    assert http_neighbors["edges"] == [
        {
            "source": "page:overview",
            "target": "page:concepts/release",
            "relation": "supports",
            "metadata": {
                "source": "graph.json",
                "path": "graph/graph.json",
                "confidence": 0.88,
            },
        }
    ]

    incoming = client.get(
        "/graph/neighborhood",
        params={
            "seed": "GH-42",
            "depth": "1",
            "direction": "in",
            "relation": "tracks",
        },
    ).json()
    assert incoming["seeds"] == ["external:GH-42"]
    assert {node["id"] for node in incoming["nodes"]} == {
        "external:GH-42",
        "page:concepts/release",
    }
    assert incoming["edges"][0]["relation"] == "tracks"


def test_graph_neighbors_unknown_seed_returns_unmatched() -> None:
    client = TestClient(create_app(FIXTURE))

    neighbors = client.get(
        "/graph/neighborhood",
        params={"seed": "missing-dependency-chain", "depth": "2"},
    ).json()

    assert neighbors["seeds"] == []
    assert neighbors["unmatched"] == ["missing-dependency-chain"]
    assert neighbors["nodes"] == []
    assert neighbors["edges"] == []


def test_graph_neighbors_respects_draft_filtering(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

Approved page.
""",
    )
    write_markdown(
        root / "draft.md",
        """
---
review_state: draft
---
# Draft

Draft-only page.
""",
    )
    graph = root / "graph"
    graph.mkdir()
    (graph / "graph.json").write_text(
        json.dumps(
            {
                "edges": [
                    {
                        "from": "index",
                        "to": "draft",
                        "type": "requires",
                        "confidence": 0.9,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    default_client = TestClient(create_app(root))
    allowed_client = TestClient(create_app(root, allow_drafts=True))

    default_neighbors = default_client.get(
        "/graph/neighborhood",
        params={"seed": "index", "depth": "1", "relation": "requires"},
    ).json()
    allowed_neighbors = allowed_client.get(
        "/graph/neighborhood",
        params={
            "seed": "index",
            "depth": "1",
            "relation": "requires",
            "include_drafts": "true",
        },
    ).json()

    assert [node["id"] for node in default_neighbors["nodes"]] == ["page:index"]
    assert default_neighbors["edges"] == []
    assert "draft" not in json.dumps(default_neighbors)
    assert "page:draft" in {node["id"] for node in allowed_neighbors["nodes"]}
    assert allowed_neighbors["edges"][0]["relation"] == "requires"


def test_mcp_contract_errors_use_safe_messages() -> None:
    client = TestClient(create_app(FIXTURE))

    unsupported = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 4, "method": f"/tmp/private/{FIXTURE.resolve()}"},
    ).json()
    unknown_tool = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": f"/tmp/private/{FIXTURE.resolve()}", "arguments": {}},
        },
    ).json()

    assert unsupported["error"] == {"code": -32601, "message": MCP_UNSUPPORTED_METHOD_MESSAGE}
    assert unknown_tool["error"] == {"code": -32602, "message": MCP_UNKNOWN_TOOL_MESSAGE}
    encoded = json.dumps({"unsupported": unsupported, "unknown_tool": unknown_tool})
    assert "/tmp/private" not in encoded
    assert str(FIXTURE.resolve()) not in encoded


def test_mcp_internal_errors_hide_local_paths(tmp_path: Path) -> None:
    missing_root = tmp_path / "private" / "missing-wiki"
    client = TestClient(create_app(missing_root))

    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "llmwiki_context", "arguments": {"query": "release"}},
        },
    ).json()

    assert response["error"] == {"code": -32000, "message": MCP_INTERNAL_FAILURE_MESSAGE}
    encoded = json.dumps(response)
    assert str(missing_root) not in encoded
    assert str(tmp_path) not in encoded
    assert "LLMWiki root" not in encoded


def test_mcp_internal_exception_message_is_sanitized(monkeypatch) -> None:
    client = TestClient(create_app(FIXTURE))

    def fail_with_path(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError(f"failed to read {FIXTURE.resolve()}")

    monkeypatch.setattr("llmwiki_serve.api.handle_mcp", fail_with_path)
    internal = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 7, "method": "tools/list"},
    ).json()

    assert internal["error"] == {"code": -32000, "message": MCP_INTERNAL_FAILURE_MESSAGE}
    assert str(FIXTURE.resolve()) not in json.dumps(internal)


def test_quickstart_mcp_request_body_smoke() -> None:
    client = TestClient(create_app(FIXTURE))
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "llmwiki_context",
                "arguments": {"query": "required copy release readiness", "limit": 4},
            },
        },
    ).json()

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 1
    assert response["result"]["answerable"] is True
    assert response["result"]["evidence"]


def test_mcp_streamable_http_tools_list_and_call_smoke() -> None:
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }

    with TestClient(
        create_app(FIXTURE),
        base_url="http://127.0.0.1:8000",
        follow_redirects=False,
    ) as client:
        tools_response = client.post(
            MCP_STREAM_PATH,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=headers,
        )
        call_response = client.post(
            MCP_STREAM_PATH,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "llmwiki_context",
                    "arguments": {"query": "required copy release readiness", "limit": 4},
                },
            },
            headers=headers,
        )

    assert tools_response.status_code == 200
    tool_names = {tool["name"] for tool in tools_response.json()["result"]["tools"]}
    assert {
        "llmwiki_context",
        "llmwiki_search",
        "llmwiki_read",
        "llmwiki_graph",
        "llmwiki_graph_neighbors",
    } <= tool_names

    assert call_response.status_code == 200
    call_result = call_response.json()["result"]
    assert call_result["isError"] is False
    assert call_result["structuredContent"]["answerable"] is True
    assert call_result["structuredContent"]["evidence"]

    with TestClient(
        create_app(Path(__file__).parent / "fixtures" / "native-wiki-root"),
        base_url="http://127.0.0.1:8000",
        follow_redirects=False,
    ) as client:
        neighbors_response = client.post(
            MCP_STREAM_PATH,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "llmwiki_graph_neighbors",
                    "arguments": {
                        "seed": "overview",
                        "depth": 1,
                        "direction": "out",
                        "relation": "supports",
                    },
                },
            },
            headers=headers,
        )

    assert neighbors_response.status_code == 200
    neighbors_result = neighbors_response.json()["result"]
    assert neighbors_result["isError"] is False
    assert neighbors_result["structuredContent"]["seeds"] == ["page:overview"]
    assert neighbors_result["structuredContent"]["edges"][0]["relation"] == "supports"


def test_origin_header_is_enforced_for_browser_requests() -> None:
    client = TestClient(create_app(FIXTURE))

    blocked = client.get("/manifest", headers={"origin": "https://attacker.example"})
    allowed = client.get("/manifest", headers={"origin": "http://localhost:5173"})

    assert blocked.status_code == 403
    assert blocked.json() == {"detail": "origin not allowed"}
    assert allowed.status_code == 200


def test_explicit_cors_origin_replaces_local_origin_allowlist() -> None:
    client = TestClient(create_app(FIXTURE, cors_origins=["https://chat.example"]))

    allowed = client.get("/manifest", headers={"origin": "https://chat.example"})
    blocked_local = client.get("/manifest", headers={"origin": "http://localhost:5173"})

    assert allowed.status_code == 200
    assert blocked_local.status_code == 403


def test_a2a_compat_routes_are_disabled_by_default() -> None:
    client = TestClient(create_app(FIXTURE))

    assert client.get("/.well-known/agent-card.json").status_code == 404
    assert (
        client.post("/message:send", json={"data": {"query": "required copy"}}).status_code == 404
    )


def test_a2a_agent_card_message_url_is_relative() -> None:
    client = TestClient(create_app(FIXTURE, enable_a2a_compat=True))
    card = client.get("/.well-known/agent-card.json").json()

    assert card["url"] == "/message:send"
    assert "://" not in card["url"]


def test_a2a_message_send_returns_context_artifact_data() -> None:
    client = TestClient(create_app(FIXTURE, enable_a2a_compat=True))
    a2a = client.post("/message:send", json={"data": {"query": "required copy"}}).json()

    assert a2a["status"] == "completed"
    context_artifact = next(
        artifact for artifact in a2a["artifacts"] if artifact["name"] == "llmwiki_context"
    )
    context_data = next(
        part["data"] for part in context_artifact["parts"] if part["kind"] == "data"
    )

    assert context_data["evidence"]
    assert context_data["graph"]["nodes"]
    assert context_data["graph"]["edges"]
    assert context_data["orientation"]
    assert context_data["limitations"] == ["1 draft or unapproved page(s) were withheld."]


def test_quickstart_a2a_request_body_smoke() -> None:
    client = TestClient(create_app(FIXTURE, enable_a2a_compat=True))
    a2a = client.post(
        "/message:send",
        json={
            "message": {
                "role": "user",
                "parts": [
                    {"kind": "text", "text": "required copy release readiness"},
                ],
            }
        },
    ).json()

    assert a2a["status"] == "completed"
    assert a2a["message"]["role"] == "agent"
    artifact = next(item for item in a2a["artifacts"] if item["name"] == "llmwiki_context")
    context_data = next(part["data"] for part in artifact["parts"] if part["kind"] == "data")
    assert context_data["answerable"] is True
    assert context_data["evidence"]


def write_markdown(path: Path, content: str) -> None:
    path.write_text(content.strip() + "\n", encoding="utf-8")


class FakeRedisClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        if self.fail:
            raise RuntimeError("redis unavailable")
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        if self.fail:
            raise RuntimeError("redis unavailable")
        self.values[key] = value

    def delete(self, *keys: str) -> None:
        if self.fail:
            raise RuntimeError("redis unavailable")
        for key in keys:
            self.values.pop(key, None)

    def scan_iter(self, *, match: str) -> list[str]:
        if self.fail:
            raise RuntimeError("redis unavailable")
        prefix = match.removesuffix("*")
        return [key for key in sorted(self.values) if key.startswith(prefix)]


def create_marker_only_root(root: Path, adapter_name: str) -> None:
    root.mkdir()
    if adapter_name == "foam":
        (root / ".foam").mkdir()
        return
    if adapter_name == "dendron":
        (root / "dendron.yml").write_text(
            "version: 5\nvaults:\n  - fsPath: vault\n",
            encoding="utf-8",
        )
        return
    if adapter_name == "quartz":
        (root / "quartz.config.ts").write_text("export default {}\n", encoding="utf-8")
        return
    if adapter_name == "logseq":
        logseq = root / "logseq"
        logseq.mkdir()
        (logseq / "config.edn").write_text("{:meta/version 1}\n", encoding="utf-8")
        return
    raise AssertionError(f"unknown adapter fixture: {adapter_name}")


def assert_redacted_root_error(
    response: Any,
    *,
    status_code: int,
    code: str,
    message: str,
    root: Path,
) -> None:
    assert response.status_code == status_code, response.text
    assert response.json() == {"error": {"code": code, "message": message}}
    encoded = response.text
    assert str(root) not in encoded
    assert str(root.parent) not in encoded
    assert "LLMWiki root" not in encoded


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
