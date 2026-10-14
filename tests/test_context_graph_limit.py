"""Operator-configurable graph sample size inside context packs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from llmwiki_serve.api import create_app
from llmwiki_serve.cli import app as cli_app
from llmwiki_serve.service import DEFAULT_CONTEXT_GRAPH_LIMIT, LlmWikiService

FIXTURE = Path(__file__).parent / "fixtures" / "sample-wiki"
STREAM_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-06-18",
}


def write_linked_wiki(root: Path, pages: int) -> Path:
    root.mkdir()
    (root / "index.md").write_text(
        "---\nwiki_title: Graph Limit Fixture\nreview_state: approved\n---\n# Index\n\n"
        + " ".join(f"[[p{i}]]" for i in range(pages))
        + "\n",
        encoding="utf-8",
    )
    for i in range(pages):
        (root / f"p{i}.md").write_text(
            f"---\ntitle: Page {i}\nreview_state: approved\n---\n# Page {i}\n\n"
            f"graphlimitcanary page {i} links [[p{(i + 1) % pages}]].\n",
            encoding="utf-8",
        )
    return root


def test_default_context_graph_limit_is_unchanged() -> None:
    assert DEFAULT_CONTEXT_GRAPH_LIMIT == 120
    pack = LlmWikiService(FIXTURE).context("artwork")
    assert pack.graph["nodes"]


def test_service_context_graph_limit_bounds_nodes_and_edges(tmp_path: Path) -> None:
    root = write_linked_wiki(tmp_path / "wiki", 40)
    default_pack = LlmWikiService(root).context("graphlimitcanary")
    limited_pack = LlmWikiService(root, context_graph_limit=5).context("graphlimitcanary")

    assert len(default_pack.graph["nodes"]) > 5
    assert len(limited_pack.graph["nodes"]) == 5
    assert len(limited_pack.graph["edges"]) <= 5
    node_ids = {node["id"] for node in limited_pack.graph["nodes"]}
    for edge in limited_pack.graph["edges"]:
        assert edge["source"] in node_ids and edge["target"] in node_ids
    # Evidence, orientation, and guidance are untouched by the graph setting.
    assert [e.page_id for e in limited_pack.evidence] == [e.page_id for e in default_pack.evidence]
    assert limited_pack.retrieval_guidance == default_pack.retrieval_guidance


def test_zero_context_graph_limit_keeps_field_shape_with_no_graph(tmp_path: Path) -> None:
    root = write_linked_wiki(tmp_path / "wiki", 10)
    pack = LlmWikiService(root, context_graph_limit=0).context("graphlimitcanary")
    assert pack.graph == {"nodes": [], "edges": []}
    assert pack.evidence


@pytest.mark.parametrize("value", [-1, 2_001, True, 1.5, "3"])
def test_invalid_context_graph_limit_is_rejected(value: Any) -> None:
    with pytest.raises(ValueError, match="context_graph_limit"):
        LlmWikiService(FIXTURE, context_graph_limit=value)
    if value in (-1, 2_001):
        with pytest.raises(ValueError, match="context_graph_limit"):
            create_app(FIXTURE, context_graph_limit=value)


def test_context_graph_limit_applies_to_http_jsonrpc_and_streamable_mcp(
    tmp_path: Path,
) -> None:
    root = write_linked_wiki(tmp_path / "wiki", 10)
    with TestClient(
        create_app(root, context_graph_limit=0), base_url="http://127.0.0.1:8000"
    ) as client:
        http_pack = client.post("/query", json={"query": "graphlimitcanary"}).json()
        assert http_pack["graph"] == {"nodes": [], "edges": []}
        assert http_pack["evidence"]

        call = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "llmwiki_context", "arguments": {"query": "graphlimitcanary"}},
        }
        jsonrpc = client.post("/mcp", json=call).json()["result"]
        assert jsonrpc["graph"] == {"nodes": [], "edges": []}

        init = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "graph-limit-test", "version": "1.0"},
            },
        }
        assert client.post("/mcp/stream", json=init, headers=STREAM_HEADERS).status_code == 200
        stream = client.post("/mcp/stream", json=call, headers=STREAM_HEADERS)
        assert stream.status_code == 200, stream.text
        structured = stream.json()["result"]["structuredContent"]
        assert structured["graph"] == {"nodes": [], "edges": []}


def test_cli_flag_and_env_set_context_graph_limit(tmp_path: Path, monkeypatch: Any) -> None:
    import uvicorn

    root = write_linked_wiki(tmp_path / "wiki", 10)
    captured: list[int] = []

    def fake_run(app: Any, **kwargs: Any) -> None:
        pack = TestClient(app).post("/query", json={"query": "graphlimitcanary"}).json()
        captured.append(len(pack["graph"]["nodes"]))

    monkeypatch.setattr(uvicorn, "run", fake_run)
    flag = CliRunner().invoke(
        cli_app, ["serve", str(root), "--io-log", "off", "--context-graph-limit", "3"]
    )
    env = CliRunner().invoke(
        cli_app,
        ["serve", str(root), "--io-log", "off"],
        env={"LLMWIKI_CONTEXT_GRAPH_LIMIT": "0"},
    )
    bad = CliRunner().invoke(
        cli_app, ["serve", str(root), "--io-log", "off", "--context-graph-limit", "-1"]
    )

    assert flag.exit_code == 0, flag.output
    assert env.exit_code == 0, env.output
    assert captured == [3, 0]
    assert bad.exit_code != 0
