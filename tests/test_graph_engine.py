from __future__ import annotations

from pathlib import Path

import pytest

from llmwiki_serve.graph_engine import InMemoryGraphEngineProvider
from llmwiki_serve.models import GraphQueryRequest
from llmwiki_serve.service import LlmWikiService

FIXTURE = Path(__file__).parent / "fixtures" / "sample-wiki"


def test_in_memory_graph_engine_reports_safe_typed_capabilities() -> None:
    capabilities = InMemoryGraphEngineProvider().capabilities()

    assert capabilities.backend_kind == "in-memory"
    assert capabilities.query_language == "typed"
    assert capabilities.structured_query is True
    assert capabilities.raw_query is False
    assert capabilities.safe_for_default is True


def test_graph_query_neighbors_uses_relation_allowlist_and_depth() -> None:
    service = LlmWikiService(FIXTURE)

    response = service.graph_query(
        GraphQueryRequest(
            operation="neighbors",
            start_node_id="page:index",
            direction="out",
            relation_allowlist=["links_to"],
            max_depth=2,
            limit=10,
        )
    )

    node_ids = {node.id for node in response.nodes}
    edge_keys = {(edge.source, edge.target, edge.relation) for edge in response.edges}

    assert {"page:index", "page:hot", "page:artwork-review", "page:requester-return"} <= node_ids
    assert ("page:index", "page:artwork-review", "links_to") in edge_keys
    assert ("page:artwork-review", "page:requester-return", "links_to") in edge_keys
    assert {edge.relation for edge in response.edges} == {"links_to"}


def test_graph_query_backlinks_finds_incoming_page_edges() -> None:
    service = LlmWikiService(FIXTURE)

    response = service.graph_query(
        GraphQueryRequest(
            operation="backlinks",
            start_node_id="page:requester-return",
            relation_allowlist=["links_to"],
        )
    )

    assert {node.id for node in response.nodes} == {
        "page:requester-return",
        "page:artwork-review",
    }
    assert [
        (edge.source, edge.target, edge.relation) for edge in response.edges
    ] == [("page:artwork-review", "page:requester-return", "links_to")]


def test_graph_query_paths_returns_path_evidence() -> None:
    service = LlmWikiService(FIXTURE)

    response = service.graph_query(
        GraphQueryRequest(
            operation="paths",
            start_node_id="page:index",
            target_node_id="page:requester-return",
            relation_allowlist=["links_to"],
            direction="out",
            max_depth=2,
            limit=5,
        )
    )

    assert response.paths
    assert response.paths[0].node_ids == [
        "page:index",
        "page:artwork-review",
        "page:requester-return",
    ]
    assert [(edge.source, edge.target, edge.relation) for edge in response.paths[0].edges] == [
        ("page:index", "page:artwork-review", "links_to"),
        ("page:artwork-review", "page:requester-return", "links_to"),
    ]


def test_graph_query_by_source_ref_returns_citing_page_and_source_node() -> None:
    service = LlmWikiService(FIXTURE)

    response = service.graph_query(
        GraphQueryRequest(operation="by_source_ref", source_ref="SRC-HOT")
    )

    assert {node.id for node in response.nodes} == {"source:SRC-HOT", "page:hot"}
    assert [(edge.source, edge.target, edge.relation) for edge in response.edges] == [
        ("page:hot", "source:SRC-HOT", "cites")
    ]


def test_graph_query_by_tag_returns_tagged_pages() -> None:
    service = LlmWikiService(FIXTURE)

    response = service.graph_query(GraphQueryRequest(operation="by_tag", tag="packaging"))

    assert {"tag:packaging", "page:artwork-review"} <= {node.id for node in response.nodes}
    assert ("page:artwork-review", "tag:packaging", "tagged") in {
        (edge.source, edge.target, edge.relation) for edge in response.edges
    }


def test_graph_query_preserves_draft_visibility_gate() -> None:
    service = LlmWikiService(FIXTURE)

    default_response = service.graph_query(
        GraphQueryRequest(operation="by_source_ref", source_ref="SRC-DRAFT")
    )
    draft_response = service.graph_query(
        GraphQueryRequest(operation="by_source_ref", source_ref="SRC-DRAFT"),
        include_drafts=True,
    )

    assert "page:draft-note" not in {node.id for node in default_response.nodes}
    assert "page:draft-note" in {node.id for node in draft_response.nodes}


def test_graph_query_rejects_unknown_nodes() -> None:
    service = LlmWikiService(FIXTURE)

    with pytest.raises(ValueError, match="unknown node"):
        service.graph_query(
            GraphQueryRequest(operation="neighbors", start_node_id="page:missing")
        )
