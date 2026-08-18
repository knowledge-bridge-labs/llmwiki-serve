from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from llmwiki_serve.api import create_app
from llmwiki_serve.cli import app as cli_app
from llmwiki_serve.graph_store import (
    GraphRecord,
    GraphStoreKey,
    SqliteGraphStore,
    create_graph_store,
    graph_record_for_view,
    safe_key_part,
)
from llmwiki_serve.models import GraphEdge, GraphNode
from llmwiki_serve.service import LlmWikiService

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "sample-wiki"
REPRESENTATIVE_VARIANT_FIXTURES = (
    ("llmwiki-compiler-output", "llmwiki-markdown", {"contains", "cites", "links_to"}),
    ("native-wiki-root", "llmwiki-markdown", {"contains", "cites", "links_to", "supports"}),
    ("obsidian-vault", "obsidian", {"contains", "cites", "links_to"}),
    ("foam-workspace", "foam", {"contains", "cites", "links_to"}),
    ("dendron-workspace", "dendron", {"contains", "cites", "links_to", "parent_of"}),
    ("quartz-site", "quartz", {"contains", "cites", "links_to"}),
    ("quartz-yaml-site", "quartz", {"contains", "cites"}),
    ("logseq-graph", "logseq", {"contains", "cites", "links_to", "tagged"}),
)


def test_sqlite_graph_store_round_trips_nodes_edges_and_metadata(tmp_path: Path) -> None:
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    key = GraphStoreKey(
        namespace="team/alpha",
        source_id="source:one",
        bundle_id="source:one:sha256:abcdef",
        projection_signature="sha256:abcdef",
        visibility_scope="approved",
    )
    nodes = [
        GraphNode(
            id="page:index",
            label="Index",
            kind="index",
            path="index.md",
            metadata={"source_refs": ["SRC-1"], "nested": {"score": 1}},
        ),
        GraphNode(id="tag:release", label="release", kind="tag", path="index.md"),
    ]
    edges = [
        GraphEdge(
            source="page:index",
            target="tag:release",
            relation="tagged",
            metadata={"source": "frontmatter"},
        )
    ]

    store.put(graph_record_for_view(key, nodes, edges))
    record = store.get(key)

    assert record is not None
    assert record.key == key
    assert record.nodes == nodes
    assert record.edges == edges


def test_sqlite_graph_store_keys_by_namespace_bundle_signature_and_visibility(
    tmp_path: Path,
) -> None:
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    key = GraphStoreKey(
        namespace="default",
        source_id="sample",
        bundle_id="sample:sha256:one",
        projection_signature="sha256:one",
        visibility_scope="approved",
    )
    approved = [GraphNode(id="page:index", label="Index", kind="index", path="index.md")]
    all_nodes = [
        GraphNode(id="page:index", label="Index", kind="index", path="index.md"),
        GraphNode(id="page:draft", label="Draft", kind="topic", path="draft.md"),
    ]

    store.put(graph_record_for_view(key, approved, []))
    store.put(graph_record_for_view(replace(key, visibility_scope="all"), all_nodes, []))

    assert [node.id for node in require_record(store.get(key)).nodes] == ["page:index"]
    all_scope_node_ids = [
        node.id for node in require_record(store.get(replace(key, visibility_scope="all"))).nodes
    ]
    assert all_scope_node_ids == ["page:index", "page:draft"]
    assert store.get(replace(key, projection_signature="sha256:two")) is None
    assert safe_key_part("sha256:one") != "sha256:one"


def test_sqlite_graph_store_corrupt_payload_is_cache_miss(tmp_path: Path) -> None:
    db_path = tmp_path / "graph.sqlite"
    store = SqliteGraphStore(db_path)
    key = GraphStoreKey(
        namespace="default",
        source_id="sample",
        bundle_id="sample:sha256:one",
        projection_signature="sha256:one",
    )
    store.put(
        graph_record_for_view(
            key,
            [GraphNode(id="page:index", label="Index", kind="index", path="index.md")],
            [],
        )
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE graph_nodes SET metadata_json = ?", ("{not-json",))

    assert store.get(key) is None


def test_sqlite_graph_store_digest_mismatch_is_cache_miss(tmp_path: Path) -> None:
    db_path = tmp_path / "graph.sqlite"
    store = SqliteGraphStore(db_path)
    key = GraphStoreKey(
        namespace="default",
        source_id="sample",
        bundle_id="sample:sha256:one",
        projection_signature="sha256:one",
    )
    store.put(
        graph_record_for_view(
            key,
            [GraphNode(id="page:index", label="Index", kind="index", path="index.md")],
            [],
        )
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE graph_nodes SET label = ?", ("Changed",))

    assert store.get(key) is None


def test_sqlite_graph_store_does_not_delete_existing_non_sqlite_file(tmp_path: Path) -> None:
    db_path = tmp_path / "graph.sqlite"
    original = b"this is not a sqlite database"
    db_path.write_bytes(original)

    with pytest.raises(ValueError, match="unable to open graph store database"):
        create_graph_store("sqlite", path=db_path)
    assert db_path.read_bytes() == original


def test_sqlite_graph_store_replace_removes_deleted_edges(tmp_path: Path) -> None:
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    key = GraphStoreKey(
        namespace="default",
        source_id="sample",
        bundle_id="sample:sha256:one",
        projection_signature="sha256:one",
    )
    first_nodes = [
        GraphNode(id="page:index", label="Index", kind="index", path="index.md"),
        GraphNode(id="page:topic", label="Topic", kind="topic", path="topic.md"),
    ]
    first_edges = [GraphEdge(source="page:index", target="page:topic", relation="links_to")]
    second_nodes = [GraphNode(id="page:index", label="Index", kind="index", path="index.md")]

    store.put(graph_record_for_view(key, first_nodes, first_edges))
    store.put(graph_record_for_view(key, second_nodes, []))
    record = require_record(store.get(key))

    assert [node.id for node in record.nodes] == ["page:index"]
    assert record.edges == []


def test_service_context_http_and_mcp_graph_match_no_store_with_sqlite(
    tmp_path: Path,
) -> None:
    default_service = LlmWikiService(FIXTURE)
    sqlite_service = LlmWikiService(
        FIXTURE,
        graph_store=SqliteGraphStore(tmp_path / "service.sqlite"),
    )

    assert sqlite_service.graph(limit=500) == default_service.graph(limit=500)
    assert (
        sqlite_service.context("required copy").graph
        == default_service.context("required copy").graph
    )

    default_client = TestClient(create_app(FIXTURE))
    sqlite_client = TestClient(
        create_app(FIXTURE, graph_store=SqliteGraphStore(tmp_path / "http.sqlite"))
    )

    assert sqlite_client.get("/manifest").json()["capabilities"][-2:] == [
        "llmwiki_graph_store",
        "llmwiki_graph_store_sqlite",
    ]
    assert (
        sqlite_client.get("/graph?limit=500").json()
        == default_client.get("/graph?limit=500").json()
    )
    assert mcp_tool_call(sqlite_client, "llmwiki_graph", {"limit": 500}) == mcp_tool_call(
        default_client,
        "llmwiki_graph",
        {"limit": 500},
    )


def test_sqlite_graph_store_preserves_draft_filtering_and_allow_drafts_gate(
    tmp_path: Path,
) -> None:
    default_client = TestClient(
        create_app(FIXTURE, graph_store=SqliteGraphStore(tmp_path / "default.sqlite"))
    )
    allowed_client = TestClient(
        create_app(
            FIXTURE,
            allow_drafts=True,
            graph_store=SqliteGraphStore(tmp_path / "allowed.sqlite"),
        )
    )

    default_graph = default_client.get("/graph?include_drafts=true").json()
    allowed_graph = allowed_client.get("/graph?include_drafts=true").json()

    assert all(node["id"] != "page:draft-note" for node in default_graph["nodes"])
    assert any(node["id"] == "page:draft-note" for node in allowed_graph["nodes"])


def test_sqlite_graph_store_backend_exception_falls_back_or_fails_by_policy() -> None:
    default_graph = LlmWikiService(FIXTURE).graph()

    assert LlmWikiService(FIXTURE, graph_store=FailingGraphStore()).graph() == default_graph

    fail_fast_service = LlmWikiService(
        FIXTURE,
        graph_store=FailingGraphStore(),
        graph_store_failure_policy="fail-fast",
    )
    with pytest.raises(RuntimeError) as raised:
        fail_fast_service.graph()

    message = str(raised.value)
    assert message == "GraphStore failed: backend operation failed"
    assert str(FIXTURE.resolve()) not in message


def test_sqlite_graph_store_hit_reuses_cached_payload_without_graph_recompute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    expected = LlmWikiService(FIXTURE, graph_store=store).graph(limit=500)

    def fail_graph_recompute(*_args: Any, **_kwargs: Any) -> object:
        raise AssertionError("valid SQLite graph cache hit should not recompute graph view")

    monkeypatch.setattr("llmwiki_serve.service.approved_graph_view", fail_graph_recompute)

    assert LlmWikiService(FIXTURE, graph_store=store).graph(limit=500) == expected


def test_sqlite_graph_store_misses_stale_service_projection_after_source_change(
    tmp_path: Path,
) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

Initial graph has no topic link.
""",
    )
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    service = LlmWikiService(root, graph_store=store)

    assert "page:topic" not in {node["id"] for node in service.graph()["nodes"]}

    write_markdown(
        root / "topic.md",
        """
---
title: Topic
review_state: approved
---
# Topic

Added after the first SQLite graph cache write.
""",
    )
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

Now the graph links to [[Topic]].
""",
    )

    refreshed_graph = service.graph()
    node_ids = {node["id"] for node in refreshed_graph["nodes"]}
    edge_keys = {
        (edge["source"], edge["target"], edge["relation"]) for edge in refreshed_graph["edges"]
    }

    assert "page:topic" in node_ids
    assert ("page:index", "page:topic", "links_to") in edge_keys


def test_cli_can_enable_sqlite_graph_store_outside_served_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import uvicorn

    graph_store_path = tmp_path / "graph.sqlite"
    captured: dict[str, Any] = {}

    def fake_run(app: Any, *, host: str, port: int) -> None:
        captured["host"] = host
        captured["port"] = port
        client = TestClient(app)
        captured["manifest"] = client.get("/manifest").json()
        captured["graph"] = client.get("/graph?limit=500").json()

    monkeypatch.setattr(uvicorn, "run", fake_run)
    result = CliRunner().invoke(
        cli_app,
        [
            "serve",
            str(FIXTURE),
            "--graph-store",
            "sqlite",
            "--graph-store-path",
            str(graph_store_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8765
    assert "llmwiki_graph_store_sqlite" in captured["manifest"]["capabilities"]
    assert graph_store_path.exists()
    assert_graph_payload_is_closed(captured["graph"])


def test_cli_rejects_sqlite_graph_store_path_inside_served_root() -> None:
    result = CliRunner().invoke(
        cli_app,
        [
            "serve",
            str(FIXTURE),
            "--graph-store",
            "sqlite",
            "--graph-store-path",
            str(FIXTURE / ".llmwiki-work" / "graph.sqlite"),
        ],
    )

    assert result.exit_code == 1, result.output
    assert "--graph-store-path must be outside the served root" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    ("fixture", "expected_adapter", "required_relations"),
    REPRESENTATIVE_VARIANT_FIXTURES,
    ids=[fixture for fixture, _adapter, _relations in REPRESENTATIVE_VARIANT_FIXTURES],
)
def test_sqlite_graph_store_matches_representative_variant_fixtures(
    tmp_path: Path,
    fixture: str,
    expected_adapter: str,
    required_relations: set[str],
) -> None:
    root = FIXTURES / fixture
    default_service = LlmWikiService(root)
    sqlite_service = LlmWikiService(
        root,
        graph_store=SqliteGraphStore(tmp_path / f"{fixture}.sqlite"),
        cache_namespace="fixture-variants",
    )

    assert default_service.manifest().adapter == expected_adapter
    assert sqlite_service.manifest().adapter == expected_adapter

    for include_drafts in (False, True):
        expected = default_service.graph(limit=2000, include_drafts=include_drafts)
        first = sqlite_service.graph(limit=2000, include_drafts=include_drafts)
        second = sqlite_service.graph(limit=2000, include_drafts=include_drafts)

        assert first == expected
        assert second == expected
        assert_graph_payload_is_closed(first)
        assert_source_ref_nodes_are_cited(first)
        assert required_relations <= {edge["relation"] for edge in first["edges"]}


def test_sqlite_graph_store_projects_openwiki_generated_markdown_docs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "openwiki"
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: OpenWiki Generated Docs Fixture
review_state: approved
tags: [openwiki, generated-docs]
source_refs: [OPENWIKI-INDEX]
---
# OpenWiki Generated Docs Fixture

OpenWiki can write generated Markdown docs. Serve links to [[update-workflow]]
without running OpenWiki or reading credentials.
""",
    )
    write_markdown(
        root / "update-workflow.md",
        """
---
title: OpenWiki Update Workflow
review_state: approved
tags: [openwiki, release-readiness]
source_refs: [OPENWIKI-WORKFLOW]
---
# OpenWiki Update Workflow

The generated docs can be projected as generic Markdown. This page links back to
[[index]] and keeps source refs explicit.
""",
    )

    default_service = LlmWikiService(root)
    sqlite_service = LlmWikiService(
        root,
        graph_store=SqliteGraphStore(tmp_path / "openwiki.sqlite"),
        cache_namespace="openwiki-generated-docs",
    )
    graph = sqlite_service.graph(limit=2000)
    edge_keys = {(edge["source"], edge["target"], edge["relation"]) for edge in graph["edges"]}

    assert default_service.manifest().adapter == "generic-markdown"
    assert sqlite_service.manifest().adapter == "generic-markdown"
    assert graph == default_service.graph(limit=2000)
    assert ("page:index", "page:update-workflow", "links_to") in edge_keys
    assert ("page:update-workflow", "page:index", "links_to") in edge_keys
    assert_source_ref_nodes_are_cited(graph)
    assert_graph_payload_is_closed(graph)


class FailingGraphStore:
    def get(self, key: GraphStoreKey) -> GraphRecord | None:
        raise sqlite3.OperationalError(f"failed to open {FIXTURE.resolve()}")

    def put(self, record: GraphRecord) -> None:
        raise AssertionError("put should not run after get failure")

    def invalidate_source(self, *, namespace: str, source_id: str) -> None:
        raise AssertionError("invalidate_source should not run")


def require_record(record: GraphRecord | None) -> GraphRecord:
    assert record is not None
    return record


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
    return cast("dict[str, Any]", payload["result"])


def write_markdown(path: Path, content: str) -> None:
    path.write_text(content.strip() + "\n", encoding="utf-8")


def assert_graph_payload_is_closed(graph: dict[str, list[dict[str, Any]]]) -> None:
    node_ids = {node["id"] for node in graph["nodes"]}
    assert all(edge["source"] in node_ids and edge["target"] in node_ids for edge in graph["edges"])


def assert_source_ref_nodes_are_cited(graph: dict[str, list[dict[str, Any]]]) -> None:
    source_ref_node_ids = {
        node["id"] for node in graph["nodes"] if node.get("kind") == "source_ref"
    }
    cited_source_ref_ids = {
        edge["target"] for edge in graph["edges"] if edge.get("relation") == "cites"
    }

    assert source_ref_node_ids
    assert source_ref_node_ids <= cited_source_ref_ids
