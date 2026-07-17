from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

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

PUBLIC_NEEDLE = "zzgovpublicneedle"
DRAFT_NEEDLE = "zzgovdraftsecretneedle"
STREAM_HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
}


def test_gov010_network_output_redaction_matrix(tmp_path: Path) -> None:
    """GOV-010: network-facing public outputs never expose tmp wiki root paths."""
    root = write_governance_wiki(tmp_path)
    client = TestClient(create_app(root))
    payloads: list[tuple[str, Any]] = []

    manifest_response = client.get("/manifest")
    assert manifest_response.status_code == 200, manifest_response.text
    manifest = manifest_response.json()
    assert manifest["root"] == NETWORK_MANIFEST_ROOT
    payloads.append(("http-manifest", manifest))

    for label, response in (
        ("http-source-bundle", client.get("/source-bundle")),
        ("http-source-refs", client.get("/source-refs")),
        (
            "http-graph-neighbors",
            client.get(
                "/graph/neighborhood",
                params={
                    "seed": "index",
                    "depth": "1",
                    "direction": "out",
                    "relation": "supports",
                    "limit": "10",
                },
            ),
        ),
        ("http-read", client.get("/read/topic")),
        ("http-search", client.post("/search", json={"query": PUBLIC_NEEDLE, "limit": 4})),
        ("http-context", client.post("/query", json={"query": PUBLIC_NEEDLE, "limit": 4})),
    ):
        assert response.status_code == 200, response.text
        payloads.append((label, response.json()))

    for tool_name, arguments in public_tool_calls():
        payload = mcp_json_rpc_response(client, tool_name, arguments)
        assert "error" not in payload
        payloads.append((f"mcp-json-rpc-{tool_name}", payload))

    with TestClient(
        create_app(root),
        base_url="http://127.0.0.1:8000",
        follow_redirects=False,
    ) as stream_client:
        for tool_name, arguments in public_tool_calls():
            payload = mcp_stream_response(stream_client, tool_name, arguments)
            assert payload["result"]["isError"] is False
            payloads.append((f"mcp-streamable-http-{tool_name}", payload))

    for label, payload in payloads:
        assert_no_absolute_root_leak(label, payload, root, tmp_path)


@pytest.mark.parametrize(
    "surface",
    ["http", "mcp-json-rpc", "mcp-streamable-http"],
)
def test_gov020_graph_neighbors_draft_visibility_opt_in_matrix(
    tmp_path: Path,
    surface: str,
) -> None:
    """GOV-020: graph neighbors reveal draft-only nodes only after double opt-in."""
    root = write_governance_wiki(tmp_path)

    cases = (
        ("default-denied", False, False, False),
        ("request-only-denied", False, True, False),
        ("server-only-denied", True, False, False),
        ("server-and-request-allowed", True, True, True),
    )
    for label, allow_drafts, include_drafts, expect_draft in cases:
        payload = graph_neighbors_payload(
            root,
            surface,
            allow_drafts=allow_drafts,
            include_drafts=include_drafts,
        )
        assert_draft_neighbor_visibility(
            f"{surface}:{label}",
            payload,
            expect_draft=expect_draft,
        )
        assert_no_absolute_root_leak(f"{surface}:{label}", payload, root, tmp_path)


def test_gov030_controlled_error_surface_matrix(tmp_path: Path) -> None:
    """GOV-030: missing roots, unsupported roots, and bad requests use safe errors."""
    missing_root = tmp_path / "private" / "missing-wiki"
    unsupported_root = tmp_path / "marker-only"
    unsupported_root.mkdir()
    valid_root = write_governance_wiki(tmp_path)
    valid_client = TestClient(create_app(valid_root))

    missing_http = TestClient(create_app(missing_root)).get("/manifest")
    unsupported_http = TestClient(create_app(unsupported_root)).post(
        "/query",
        json={"query": "release readiness"},
    )
    invalid_query = valid_client.post(
        "/query",
        json={"query": PUBLIC_NEEDLE, "limit": "many"},
    )
    invalid_graph_direction = valid_client.get(
        "/graph/neighborhood",
        params={"seed": "index", "direction": "sideways"},
    )
    unsupported_mcp = valid_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": str(tmp_path / "private-method")},
    )
    unknown_tool_mcp = valid_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": str(tmp_path / "private-tool"), "arguments": {}},
        },
    )
    missing_mcp = TestClient(create_app(missing_root)).post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "llmwiki_context", "arguments": {"query": "release"}},
        },
    )

    assert missing_http.status_code == 404
    assert missing_http.json() == {
        "error": {
            "code": WIKI_ROOT_MISSING_CODE,
            "message": WIKI_ROOT_MISSING_SAFE_MESSAGE,
        }
    }
    assert unsupported_http.status_code == 422
    assert unsupported_http.json() == {
        "error": {
            "code": WIKI_ROOT_UNSUPPORTED_CODE,
            "message": WIKI_ROOT_UNSUPPORTED_SAFE_MESSAGE,
        }
    }
    assert invalid_query.status_code == 422
    assert invalid_graph_direction.status_code == 422
    assert unsupported_mcp.status_code == 200
    assert unsupported_mcp.json()["error"] == {
        "code": -32601,
        "message": MCP_UNSUPPORTED_METHOD_MESSAGE,
    }
    assert unknown_tool_mcp.status_code == 200
    assert unknown_tool_mcp.json()["error"] == {
        "code": -32602,
        "message": MCP_UNKNOWN_TOOL_MESSAGE,
    }
    assert missing_mcp.status_code == 200
    assert missing_mcp.json()["error"] == {
        "code": -32000,
        "message": MCP_INTERNAL_FAILURE_MESSAGE,
    }

    for label, response in (
        ("missing-http", missing_http),
        ("unsupported-http", unsupported_http),
        ("invalid-query", invalid_query),
        ("invalid-graph-direction", invalid_graph_direction),
        ("unsupported-mcp", unsupported_mcp),
        ("unknown-tool-mcp", unknown_tool_mcp),
        ("missing-mcp", missing_mcp),
    ):
        assert_controlled_error_output(label, response.json(), tmp_path)


def public_tool_calls() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("llmwiki_source_bundle", {}),
        ("llmwiki_source_refs", {}),
        (
            "llmwiki_graph_neighbors",
            {
                "seed": "index",
                "depth": 1,
                "direction": "out",
                "relation": "supports",
                "limit": 10,
            },
        ),
        ("llmwiki_read", {"page_id": "topic"}),
        ("llmwiki_search", {"query": PUBLIC_NEEDLE, "limit": 4}),
        ("llmwiki_context", {"query": PUBLIC_NEEDLE, "limit": 4}),
    ]


def graph_neighbors_payload(
    root: Path,
    surface: str,
    *,
    allow_drafts: bool,
    include_drafts: bool,
) -> dict[str, Any]:
    if surface == "http":
        client = TestClient(create_app(root, allow_drafts=allow_drafts))
        params = {
            "seed": "index",
            "depth": "1",
            "direction": "out",
            "relation": "requires",
            "limit": "10",
        }
        if include_drafts:
            params["include_drafts"] = "true"
        response = client.get("/graph/neighborhood", params=params)
        assert response.status_code == 200, response.text
        return response.json()

    arguments: dict[str, Any] = {
        "seed": "index",
        "depth": 1,
        "direction": "out",
        "relation": "requires",
        "limit": 10,
    }
    if include_drafts:
        arguments["include_drafts"] = True

    if surface == "mcp-json-rpc":
        client = TestClient(create_app(root, allow_drafts=allow_drafts))
        payload = mcp_json_rpc_response(client, "llmwiki_graph_neighbors", arguments)
        assert "error" not in payload
        return payload["result"]

    if surface == "mcp-streamable-http":
        with TestClient(
            create_app(root, allow_drafts=allow_drafts),
            base_url="http://127.0.0.1:8000",
            follow_redirects=False,
        ) as client:
            payload = mcp_stream_response(client, "llmwiki_graph_neighbors", arguments)
        assert payload["result"]["isError"] is False
        return payload["result"]["structuredContent"]

    raise AssertionError(f"unknown governance surface: {surface}")


def mcp_json_rpc_response(
    client: TestClient,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def mcp_stream_response(
    client: TestClient,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(
        MCP_STREAM_PATH,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
        headers=STREAM_HEADERS,
    )
    assert response.status_code == 200, response.text
    return response.json()


def assert_draft_neighbor_visibility(
    label: str,
    payload: dict[str, Any],
    *,
    expect_draft: bool,
) -> None:
    node_ids = {node["id"] for node in payload["nodes"]}
    edge_keys = {(edge["source"], edge["target"], edge["relation"]) for edge in payload["edges"]}

    assert payload["seeds"] == ["page:index"], label
    assert payload["unmatched"] == [], label
    if expect_draft:
        assert "page:draft-secret" in node_ids, label
        assert ("page:index", "page:draft-secret", "requires") in edge_keys, label
        return

    encoded = stable_json(payload)
    assert node_ids == {"page:index"}, label
    assert payload["edges"] == [], label
    assert "page:draft-secret" not in encoded, label
    assert "draft-secret.md" not in encoded, label
    assert "SRC-DRAFT" not in encoded, label
    assert DRAFT_NEEDLE not in encoded, label


def assert_no_absolute_root_leak(label: str, payload: Any, root: Path, tmp_path: Path) -> None:
    encoded = stable_json(payload)
    for path in (root.resolve(), tmp_path.resolve()):
        for forbidden in path_spellings(path):
            assert forbidden not in encoded, f"{label} leaked {forbidden!r}"


def assert_controlled_error_output(label: str, payload: Any, tmp_path: Path) -> None:
    encoded = stable_json(payload)
    assert "Traceback" not in encoded, label
    assert "traceback" not in encoded.lower(), label
    assert "LLMWiki root" not in encoded, label
    for forbidden in path_spellings(tmp_path.resolve()):
        assert forbidden not in encoded, f"{label} leaked {forbidden!r}"


def path_spellings(path: Path) -> set[str]:
    text = str(path)
    return {
        text,
        text.replace("\\", "\\\\"),
        path.as_posix(),
        path.as_posix().replace("/", "\\/"),
    }


def stable_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def write_governance_wiki(tmp_path: Path) -> Path:
    root = tmp_path / "private" / "warehouse-wiki"
    root.mkdir(parents=True)
    write_markdown(
        root / "index.md",
        f"""
---
wiki_title: Governance Matrix Wiki
description: Synthetic governance fixture.
review_state: approved
source_refs: [SRC-INDEX]
---
# Governance Matrix Wiki

Approved entry point with {PUBLIC_NEEDLE}. See [[topic]].
""",
    )
    write_markdown(
        root / "topic.md",
        f"""
---
title: Public Topic
review_state: approved
source_refs: [SRC-TOPIC]
tags: [governance]
---
# Public Topic

Approved public content with {PUBLIC_NEEDLE}.
""",
    )
    write_markdown(
        root / "draft-secret.md",
        f"""
---
title: Draft Secret
review_state: draft
source_refs: [SRC-DRAFT]
tags: [private-governance]
---
# Draft Secret

Draft-only content with {DRAFT_NEEDLE}.
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
                        "to": "topic",
                        "type": "supports",
                        "confidence": 0.91,
                    },
                    {
                        "from": "index",
                        "to": "draft-secret",
                        "type": "requires",
                        "confidence": 0.92,
                    },
                ]
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    return root


def write_markdown(path: Path, content: str) -> None:
    path.write_text(content.strip() + "\n", encoding="utf-8")
