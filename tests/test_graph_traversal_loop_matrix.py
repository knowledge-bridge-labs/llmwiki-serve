from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from llmwiki_serve.api import MCP_STREAM_PATH, create_app
from llmwiki_serve.service import LlmWikiService

NATIVE_FIXTURE = Path(__file__).parent / "fixtures" / "native-wiki-root"
TRAVERSAL_QUERY = "release readiness graph references"
STREAM_HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
}


def test_gt010_context_returns_orientation_and_evidence_seed_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GT-010: orientation-first query/context seed discovery."""
    forbid_full_graph_payloads(monkeypatch)

    contexts = context_across_surfaces(NATIVE_FIXTURE, TRAVERSAL_QUERY, limit=4)

    assert_surface_parity(contexts)
    context = contexts["service"]
    orientation_ids = [item["page_id"] for item in context["orientation"]]
    evidence_ids = [item["page_id"] for item in context["evidence"]]

    assert orientation_ids[0] == "overview"
    assert all(item["route"] == "orientation" for item in context["orientation"])
    assert all(item["route"] == "search" for item in context["evidence"])
    assert evidence_ids[:2] == ["concepts/release", "overview"]
    assert {"overview", "concepts/release"} <= set(orientation_ids + evidence_ids)


def test_gt020_graph_neighbors_surface_matrix_returns_sidecar_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GT-020: bounded traversal from an overview seed returns typed sidecar edges."""
    forbid_full_graph_payloads(monkeypatch)

    payloads = graph_neighbors_across_surfaces(
        NATIVE_FIXTURE,
        seed="overview",
        depth=1,
        direction="out",
        relation="supports",
        limit=10,
    )

    assert_surface_parity(payloads)
    neighbors = payloads["service"]
    assert neighbors["seeds"] == ["page:overview"]
    assert neighbors["unmatched"] == []
    assert neighbors["relations"] == ["supports"]
    assert [node["id"] for node in neighbors["nodes"]] == [
        "page:overview",
        "page:concepts/release",
    ]
    assert neighbors["edges"] == [
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


def test_gt030_relation_filtering_narrows_neighborhood_surface_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GT-030: relation filters narrow traversal results deterministically."""
    forbid_full_graph_payloads(monkeypatch)

    wide_payloads = graph_neighbors_across_surfaces(
        NATIVE_FIXTURE,
        seed="overview",
        depth=1,
        direction="out",
        limit=20,
    )
    filtered_payloads = graph_neighbors_across_surfaces(
        NATIVE_FIXTURE,
        seed="overview",
        depth=1,
        direction="out",
        relation="supports",
        limit=20,
    )

    assert_surface_parity(wide_payloads)
    assert_surface_parity(filtered_payloads)
    wide = wide_payloads["service"]
    filtered = filtered_payloads["service"]
    wide_relations = {edge["relation"] for edge in wide["edges"]}

    assert {"contains", "cites", "tagged", "links_to", "supports"} <= wide_relations
    assert {edge["relation"] for edge in filtered["edges"]} == {"supports"}
    assert len(filtered["nodes"]) < len(wide["nodes"])
    assert len(filtered["edges"]) < len(wide["edges"])


def test_gt040_unknown_seed_surface_matrix_returns_unmatched_without_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GT-040: unknown seeds produce unmatched metadata, not errors."""
    forbid_full_graph_payloads(monkeypatch)

    payloads = graph_neighbors_across_surfaces(
        NATIVE_FIXTURE,
        seed="missing-dependency-chain",
        depth=2,
        direction="both",
        limit=10,
    )

    assert_surface_parity(payloads)
    neighbors = payloads["service"]
    assert neighbors["seeds"] == []
    assert neighbors["unmatched"] == ["missing-dependency-chain"]
    assert neighbors["nodes"] == []
    assert neighbors["edges"] == []


def test_gt050_draft_neighbor_visibility_surface_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GT-050: draft nodes stay hidden unless draft traversal is explicitly enabled."""
    forbid_full_graph_payloads(monkeypatch)
    root = tmp_path / "wiki"
    write_draft_loop_wiki(root)

    default_payloads = graph_neighbors_across_surfaces(
        root,
        seed="index",
        depth=1,
        direction="out",
        relation="requires",
        limit=10,
    )
    allowed_payloads = graph_neighbors_across_surfaces(
        root,
        seed="index",
        depth=1,
        direction="out",
        relation="requires",
        limit=10,
        include_drafts=True,
        allow_drafts=True,
    )

    assert_surface_parity(default_payloads)
    assert_surface_parity(allowed_payloads)
    default_neighbors = default_payloads["service"]
    allowed_neighbors = allowed_payloads["service"]

    assert [node["id"] for node in default_neighbors["nodes"]] == ["page:index"]
    assert default_neighbors["edges"] == []
    assert "draft" not in json.dumps(default_neighbors)
    assert {node["id"] for node in allowed_neighbors["nodes"]} == {
        "page:index",
        "page:draft",
    }
    assert allowed_neighbors["edges"][0]["relation"] == "requires"


def context_across_surfaces(root: Path, query: str, *, limit: int) -> dict[str, dict[str, Any]]:
    client = TestClient(create_app(root))
    http_response = client.post("/query", json={"query": query, "limit": limit})
    assert http_response.status_code == 200, http_response.text

    return {
        "service": LlmWikiService(root).context(query, limit=limit).model_dump(),
        "http": http_response.json(),
        "mcp-json-rpc": mcp_tool_call(
            client,
            "llmwiki_context",
            {"query": query, "limit": limit},
        ),
        "mcp-streamable-http": mcp_stream_tool_call(
            root,
            "llmwiki_context",
            {"query": query, "limit": limit},
        ),
    }


def graph_neighbors_across_surfaces(
    root: Path,
    *,
    seed: str,
    depth: int,
    direction: str,
    relation: str = "",
    limit: int,
    include_drafts: bool = False,
    allow_drafts: bool = False,
) -> dict[str, dict[str, Any]]:
    client = TestClient(create_app(root, allow_drafts=allow_drafts))
    service_relations = [relation] if relation else []
    http_params: dict[str, str] = {
        "seed": seed,
        "depth": str(depth),
        "direction": direction,
        "limit": str(limit),
    }
    mcp_args: dict[str, Any] = {
        "seed": seed,
        "depth": depth,
        "direction": direction,
        "limit": limit,
    }
    if relation:
        http_params["relation"] = relation
        mcp_args["relation"] = relation
    if include_drafts:
        http_params["include_drafts"] = "true"
        mcp_args["include_drafts"] = True

    http_response = client.get("/graph/neighborhood", params=http_params)
    assert http_response.status_code == 200, http_response.text

    return {
        "service": LlmWikiService(root)
        .graph_neighbors(
            seeds=[seed],
            depth=depth,
            direction=direction,
            relations=service_relations,
            limit=limit,
            include_drafts=include_drafts,
        )
        .model_dump(),
        "http": http_response.json(),
        "mcp-json-rpc": mcp_tool_call(client, "llmwiki_graph_neighbors", mcp_args),
        "mcp-streamable-http": mcp_stream_tool_call(
            root,
            "llmwiki_graph_neighbors",
            mcp_args,
            allow_drafts=allow_drafts,
        ),
    }


def mcp_tool_call(client: TestClient, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "error" not in payload
    return payload["result"]


def mcp_stream_tool_call(
    root: Path,
    name: str,
    arguments: dict[str, Any],
    *,
    allow_drafts: bool = False,
) -> dict[str, Any]:
    with TestClient(
        create_app(root, allow_drafts=allow_drafts),
        base_url="http://127.0.0.1:8000",
        follow_redirects=False,
    ) as client:
        response = client.post(
            MCP_STREAM_PATH,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers=STREAM_HEADERS,
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert "error" not in payload
    result = payload["result"]
    assert result["isError"] is False
    return result["structuredContent"]


def assert_surface_parity(payloads: dict[str, dict[str, Any]]) -> None:
    service_payload = payloads["service"]
    for surface, payload in payloads.items():
        assert payload == service_payload, surface


def forbid_full_graph_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_full_graph_payload(*_args: object, **_kwargs: object) -> dict[str, Any]:
        raise AssertionError("GT matrix should use context and graph_neighbors, not full graph")

    monkeypatch.setattr(LlmWikiService, "graph", fail_full_graph_payload)


def write_draft_loop_wiki(root: Path) -> None:
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Draft Traversal Matrix
review_state: approved
---
# Draft Traversal Matrix

Approved entry point.
""",
    )
    write_markdown(
        root / "draft.md",
        """
---
title: Draft Dependency
review_state: draft
---
# Draft Dependency

Draft-only dependency.
""",
    )
    graph_dir = root / "graph"
    graph_dir.mkdir()
    (graph_dir / "graph.json").write_text(
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
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def write_markdown(path: Path, content: str) -> None:
    path.write_text(content.strip() + "\n", encoding="utf-8")
