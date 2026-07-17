from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from llmwiki_serve.api import MCP_STREAM_PATH, create_app
from llmwiki_serve.service import LlmWikiService

FS_STRICT = "FS-STRICT"
FS_INTERVAL = "FS-INTERVAL"
FS_PRODUCER = "FS-PRODUCER"
CURRENT_FRESHNESS_MODES = (FS_STRICT, FS_INTERVAL, FS_PRODUCER)
REFRESH_INTERVAL_SECONDS = 10.0

OLD_TOKEN = "zzmatrixoldneedle"
NEW_TOKEN = "zzmatrixnewneedle"
ADDED_TOKEN = "zzmatrixaddedneedle"
BLOCKED_TOKEN = "zzmatrixblockedneedle"
RESTORED_TOKEN = "zzmatrixrestoredneedle"
SURFACE_OLD_TOKEN = "zzproducersurfaceoldneedle"
SURFACE_NEW_TOKEN = "zzproducersurfacenewneedle"
STREAM_HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
}


@dataclass
class FreshnessControl:
    mode_id: str
    marker: Path | None = None
    generation: int = 0
    now: float = 0.0

    def clock(self) -> float:
        return self.now

    def service(self, root: Path) -> LlmWikiService:
        if self.mode_id == FS_STRICT:
            return LlmWikiService(root)
        if self.mode_id == FS_INTERVAL:
            return LlmWikiService(
                root,
                refresh_interval_seconds=REFRESH_INTERVAL_SECONDS,
                clock=self.clock,
            )
        if self.mode_id == FS_PRODUCER:
            if self.marker is None:
                self.marker = root / ".llmwiki-producer-manifest.json"
                self.bump_marker()
            return LlmWikiService(root, producer_manifest_path=self.marker.name)
        raise AssertionError(f"unknown freshness mode: {self.mode_id}")

    def bump_marker(self) -> None:
        assert self.marker is not None
        self.generation += 1
        self.marker.write_text(f'{{"build":{self.generation}}}\n', encoding="utf-8")


@pytest.mark.parametrize("mode_id", CURRENT_FRESHNESS_MODES, ids=CURRENT_FRESHNESS_MODES)
def test_current_modes_fl020_markdown_rewrite_matrix(tmp_path: Path, mode_id: str) -> None:
    """FS-STRICT/FS-INTERVAL/FS-PRODUCER: FL-020 Markdown rewrite."""
    root = tmp_path / "wiki"
    topic = write_matrix_wiki(root, OLD_TOKEN)
    control = FreshnessControl(mode_id)
    service = control.service(root)

    assert_topic_generation(service, OLD_TOKEN, NEW_TOKEN)

    rewrite_preserving_stat(topic, topic_markdown(NEW_TOKEN))

    if mode_id == FS_STRICT:
        assert_topic_generation(service, NEW_TOKEN, OLD_TOKEN)
        return

    if mode_id == FS_INTERVAL:
        control.now = REFRESH_INTERVAL_SECONDS - 1
        assert_topic_generation(service, OLD_TOKEN, NEW_TOKEN)
        control.now = REFRESH_INTERVAL_SECONDS
        assert_topic_generation(service, NEW_TOKEN, OLD_TOKEN)
        return

    assert mode_id == FS_PRODUCER
    assert_topic_generation(service, OLD_TOKEN, NEW_TOKEN)
    control.bump_marker()
    assert_topic_generation(service, NEW_TOKEN, OLD_TOKEN)


@pytest.mark.parametrize("mode_id", CURRENT_FRESHNESS_MODES, ids=CURRENT_FRESHNESS_MODES)
def test_current_modes_fl030_sidecar_graph_rewrite_matrix(tmp_path: Path, mode_id: str) -> None:
    """FS-STRICT/FS-INTERVAL/FS-PRODUCER: FL-030 sidecar graph rewrite."""
    root = tmp_path / "wiki"
    write_matrix_wiki(root, OLD_TOKEN)
    graph_json = write_sidecar_graph(root, "supports")
    control = FreshnessControl(mode_id)
    service = control.service(root)

    assert_graph_relation(service, expected="supports", absent="requires")

    rewrite_preserving_stat(graph_json, sidecar_graph_json("requires"))

    if mode_id == FS_STRICT:
        assert_graph_relation(service, expected="requires", absent="supports")
        return

    if mode_id == FS_INTERVAL:
        control.now = REFRESH_INTERVAL_SECONDS - 1
        assert_graph_relation(service, expected="supports", absent="requires")
        control.now = REFRESH_INTERVAL_SECONDS
        assert_graph_relation(service, expected="requires", absent="supports")
        return

    assert mode_id == FS_PRODUCER
    assert_graph_relation(service, expected="supports", absent="requires")
    control.bump_marker()
    assert_graph_relation(service, expected="requires", absent="supports")


@pytest.mark.parametrize("mode_id", CURRENT_FRESHNESS_MODES, ids=CURRENT_FRESHNESS_MODES)
def test_current_modes_fl040_add_delete_page_matrix(tmp_path: Path, mode_id: str) -> None:
    """FS-STRICT/FS-INTERVAL/FS-PRODUCER: FL-040 add/delete source page."""
    root = tmp_path / "wiki"
    write_matrix_wiki(root, OLD_TOKEN)
    control = FreshnessControl(mode_id)
    service = control.service(root)

    assert_added_page_absent(service)

    write_index(root, include_added_link=True)
    write_added_page(root)

    if mode_id == FS_STRICT:
        assert_added_page_visible(service)
        delete_added_page(root)
        assert_added_page_absent(service)
        return

    if mode_id == FS_INTERVAL:
        control.now = REFRESH_INTERVAL_SECONDS - 1
        assert_added_page_absent(service)
        control.now = REFRESH_INTERVAL_SECONDS
        assert_added_page_visible(service)

        delete_added_page(root)
        control.now = (REFRESH_INTERVAL_SECONDS * 2) - 1
        assert_added_page_visible(service)
        control.now = REFRESH_INTERVAL_SECONDS * 2
        assert_added_page_absent(service)
        return

    assert mode_id == FS_PRODUCER
    assert_added_page_absent(service)
    control.bump_marker()
    assert_added_page_visible(service)
    delete_added_page(root)
    assert_added_page_visible(service)
    control.bump_marker()
    assert_added_page_absent(service)


@pytest.mark.parametrize("mode_id", CURRENT_FRESHNESS_MODES, ids=CURRENT_FRESHNESS_MODES)
def test_current_modes_fl050_visibility_change_matrix(tmp_path: Path, mode_id: str) -> None:
    """FS-STRICT/FS-INTERVAL/FS-PRODUCER: FL-050 visibility change."""
    root = tmp_path / "wiki"
    topic = write_matrix_wiki(root, OLD_TOKEN)
    control = FreshnessControl(mode_id)
    service = control.service(root)

    assert_topic_visible(service, OLD_TOKEN)

    write_markdown(topic, topic_visibility_markdown(BLOCKED_TOKEN, status="blocked"))

    if mode_id == FS_STRICT:
        assert_topic_blocked(service, BLOCKED_TOKEN)
        write_markdown(topic, topic_visibility_markdown(RESTORED_TOKEN, status="published"))
        assert_topic_visible(service, RESTORED_TOKEN)
        return

    if mode_id == FS_INTERVAL:
        control.now = REFRESH_INTERVAL_SECONDS - 1
        assert_topic_visible(service, OLD_TOKEN)
        control.now = REFRESH_INTERVAL_SECONDS
        assert_topic_blocked(service, BLOCKED_TOKEN)

        write_markdown(topic, topic_visibility_markdown(RESTORED_TOKEN, status="published"))
        control.now = (REFRESH_INTERVAL_SECONDS * 2) - 1
        assert_topic_blocked(service, BLOCKED_TOKEN)
        control.now = REFRESH_INTERVAL_SECONDS * 2
        assert_topic_visible(service, RESTORED_TOKEN)
        return

    assert mode_id == FS_PRODUCER
    assert_topic_visible(service, OLD_TOKEN)
    control.bump_marker()
    assert_topic_blocked(service, BLOCKED_TOKEN)
    write_markdown(topic, topic_visibility_markdown(RESTORED_TOKEN, status="published"))
    assert_topic_blocked(service, BLOCKED_TOKEN)
    control.bump_marker()
    assert_topic_visible(service, RESTORED_TOKEN)


@pytest.mark.parametrize("mode_id", CURRENT_FRESHNESS_MODES, ids=CURRENT_FRESHNESS_MODES)
def test_current_modes_fl060_adapter_config_change_matrix(tmp_path: Path, mode_id: str) -> None:
    """FS-STRICT/FS-INTERVAL/FS-PRODUCER: FL-060 adapter marker/config change."""
    root = tmp_path / "wiki"
    write_matrix_wiki(root, OLD_TOKEN)
    control = FreshnessControl(mode_id)
    service = control.service(root)

    assert_adapter_projection(service, "generic-markdown", "generic-markdown")

    write_foam_extension_config(root, enabled=True)

    if mode_id == FS_STRICT:
        assert_adapter_projection(service, "foam", "foambubble/foam")
        write_foam_extension_config(root, enabled=False)
        assert_adapter_projection(service, "generic-markdown", "generic-markdown")
        return

    if mode_id == FS_INTERVAL:
        control.now = REFRESH_INTERVAL_SECONDS - 1
        assert_adapter_projection(service, "generic-markdown", "generic-markdown")
        control.now = REFRESH_INTERVAL_SECONDS
        assert_adapter_projection(service, "foam", "foambubble/foam")

        write_foam_extension_config(root, enabled=False)
        control.now = (REFRESH_INTERVAL_SECONDS * 2) - 1
        assert_adapter_projection(service, "foam", "foambubble/foam")
        control.now = REFRESH_INTERVAL_SECONDS * 2
        assert_adapter_projection(service, "generic-markdown", "generic-markdown")
        return

    assert mode_id == FS_PRODUCER
    assert_adapter_projection(service, "generic-markdown", "generic-markdown")
    control.bump_marker()
    assert_adapter_projection(service, "foam", "foambubble/foam")
    write_foam_extension_config(root, enabled=False)
    assert_adapter_projection(service, "foam", "foambubble/foam")
    control.bump_marker()
    assert_adapter_projection(service, "generic-markdown", "generic-markdown")


@pytest.mark.parametrize("mode_id", CURRENT_FRESHNESS_MODES, ids=CURRENT_FRESHNESS_MODES)
def test_current_modes_fl070_explicit_refresh_matrix(tmp_path: Path, mode_id: str) -> None:
    """FS-STRICT/FS-INTERVAL/FS-PRODUCER: FL-070 explicit refresh."""
    root = tmp_path / "wiki"
    topic = write_matrix_wiki(root, OLD_TOKEN)
    control = FreshnessControl(mode_id)
    service = control.service(root)

    assert_topic_generation(service, OLD_TOKEN, NEW_TOKEN)
    rewrite_preserving_stat(topic, topic_markdown(NEW_TOKEN))
    control.now = 1.0

    service.index(refresh=True)

    assert_topic_generation(service, NEW_TOKEN, OLD_TOKEN)


@pytest.mark.parametrize("mode_id", CURRENT_FRESHNESS_MODES, ids=CURRENT_FRESHNESS_MODES)
def test_current_modes_fl080_restart_matrix(tmp_path: Path, mode_id: str) -> None:
    """FS-STRICT/FS-INTERVAL/FS-PRODUCER: FL-080 restart behavior."""
    root = tmp_path / "wiki"
    topic = write_matrix_wiki(root, OLD_TOKEN)
    control = FreshnessControl(mode_id)
    service = control.service(root)

    assert_topic_generation(service, OLD_TOKEN, NEW_TOKEN)
    rewrite_preserving_stat(topic, topic_markdown(NEW_TOKEN))
    control.now = 1.0

    restarted = control.service(root)

    assert_topic_generation(restarted, NEW_TOKEN, OLD_TOKEN)


def test_fs_producer_fl090_outside_root_marker_falls_back_to_strict_scan(
    tmp_path: Path,
) -> None:
    """FS-PRODUCER: FL-090 unsafe producer marker fallback."""
    root = tmp_path / "wiki"
    topic = write_matrix_wiki(root, OLD_TOKEN)
    outside_marker = tmp_path / ".llmwiki-producer-manifest.json"
    outside_marker.write_text('{"build":1}\n', encoding="utf-8")
    service = LlmWikiService(root, producer_manifest_path=outside_marker)

    assert_topic_generation(service, OLD_TOKEN, NEW_TOKEN)

    rewrite_preserving_stat(topic, topic_markdown(NEW_TOKEN))

    assert_topic_generation(service, NEW_TOKEN, OLD_TOKEN)


def test_fs_producer_surfaces_reuse_until_marker_changes(tmp_path: Path) -> None:
    """FS-PRODUCER: FL-020 HTTP/MCP surfaces stay stale until marker changes."""
    root = tmp_path / "wiki"
    topic = write_producer_surface_wiki(root, SURFACE_OLD_TOKEN)
    marker = root / ".llmwiki-producer-manifest.json"
    write_producer_marker(marker, generation=1)

    app = create_app(root, producer_manifest_path=marker.name)
    with TestClient(
        app,
        base_url="http://127.0.0.1:8000",
        follow_redirects=False,
    ) as client:
        initial_identity = producer_surface_identity(client)
        assert_surface_generation(client, SURFACE_OLD_TOKEN, SURFACE_NEW_TOKEN)

        write_producer_surface_topic(topic, SURFACE_NEW_TOKEN)

        stale_identity = producer_surface_identity(client)
        assert stale_identity == initial_identity
        assert_surface_generation(client, SURFACE_OLD_TOKEN, SURFACE_NEW_TOKEN)

        write_producer_marker(marker, generation=2)

        refreshed_identity = producer_surface_identity(client)
        assert refreshed_identity["health_signature"] != initial_identity["health_signature"]
        assert refreshed_identity["health_bundle_id"] != initial_identity["health_bundle_id"]
        assert refreshed_identity["bundle_signature"] != initial_identity["bundle_signature"]
        assert refreshed_identity["bundle_id"] != initial_identity["bundle_id"]
        assert_surface_generation(client, SURFACE_NEW_TOKEN, SURFACE_OLD_TOKEN)


def write_matrix_wiki(root: Path, topic_token: str) -> Path:
    root.mkdir()
    write_index(root)
    topic = root / "topic.md"
    write_markdown(topic, topic_markdown(topic_token))
    return topic


def write_index(root: Path, *, include_added_link: bool = False) -> None:
    added_link = " and [[added]]" if include_added_link else ""
    write_markdown(
        root / "index.md",
        f"""
---
wiki_title: Freshness Matrix Fixture
review_state: approved
---
# Freshness Matrix Fixture

Start with [[topic]]{added_link}.
""",
    )


def topic_markdown(token: str) -> str:
    return f"""
---
title: Topic
review_state: approved
---
# Topic

{token} appears in the freshness matrix.
"""


def topic_visibility_markdown(token: str, *, status: str) -> str:
    return f"""
---
title: Topic
status: {status}
---
# Topic

{token} appears in the visibility matrix.
"""


def write_added_page(root: Path) -> None:
    write_markdown(
        root / "added.md",
        f"""
---
title: Added
review_state: approved
---
# Added

{ADDED_TOKEN} appears after the page is added.
""",
    )


def delete_added_page(root: Path) -> None:
    (root / "added.md").unlink()


def write_foam_extension_config(root: Path, *, enabled: bool) -> None:
    config = root / ".vscode" / "extensions.json"
    config.parent.mkdir(exist_ok=True)
    recommendations = ["foam.foam-vscode"] if enabled else []
    config.write_text(
        json.dumps({"recommendations": recommendations}, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def write_sidecar_graph(root: Path, relation: str) -> Path:
    graph_dir = root / "graph"
    graph_dir.mkdir()
    graph_json = graph_dir / "graph.json"
    graph_json.write_text(sidecar_graph_json(relation), encoding="utf-8")
    return graph_json


def sidecar_graph_json(relation: str) -> str:
    return (
        json.dumps(
            {"edges": [{"from": "index", "to": "topic", "type": relation, "confidence": 0.5}]},
            separators=(",", ":"),
        )
        + "\n"
    )


def rewrite_preserving_stat(path: Path, content: str) -> None:
    before = path.stat()
    path.write_text(content.strip() + "\n", encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    after = path.stat()
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns


def write_markdown(path: Path, content: str) -> None:
    path.write_text(content.strip() + "\n", encoding="utf-8")


def write_producer_surface_wiki(root: Path, token: str) -> Path:
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Producer Surface Wiki
review_state: approved
---
# Producer Surface Wiki

Start with [[topic]].
""",
    )
    topic = root / "topic.md"
    write_producer_surface_topic(topic, token)
    return topic


def write_producer_surface_topic(path: Path, token: str) -> None:
    write_markdown(
        path,
        f"""
---
title: Producer Surface {token}
review_state: approved
source_refs: [PRODUCER-SURFACE-SRC]
---
# Producer Surface {token}

{token} appears in the producer manifest surface matrix.
""",
    )


def write_producer_marker(marker: Path, *, generation: int) -> None:
    marker.write_text(
        json.dumps({"build": generation, "note": f"surface-{generation}"}, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def assert_topic_generation(
    service: LlmWikiService, expected_token: str, absent_token: str
) -> None:
    page = service.read("topic")
    assert expected_token in page["text"]
    assert absent_token not in page["text"]
    assert search_has_page(service, expected_token, "topic")
    assert not search_has_page(service, absent_token, "topic")
    context = service.context(expected_token, limit=2)
    assert context.answerable is True
    assert any(item.page_id == "topic" for item in context.evidence)


def search_has_page(service: LlmWikiService, query: str, page_id: str) -> bool:
    return any(item["page_id"] == page_id for item in service.search(query, limit=4))


def assert_added_page_visible(service: LlmWikiService) -> None:
    manifest = service.manifest()
    assert manifest.page_count == 3
    assert manifest.approved_page_count == 3

    page = service.read("added")
    assert page["title"] == "Added"
    assert ADDED_TOKEN in page["text"]
    assert search_has_page(service, ADDED_TOKEN, "added")

    context = service.context(ADDED_TOKEN, limit=3)
    assert context.answerable is True
    assert any(item.page_id == "added" for item in context.evidence)

    graph = service.graph()
    assert "page:added" in {node["id"] for node in graph["nodes"]}
    assert graph_neighbors_link_to(service, "page:added")


def assert_added_page_absent(service: LlmWikiService) -> None:
    manifest = service.manifest()
    assert manifest.page_count == 2
    assert manifest.approved_page_count == 2

    assert service.read("added") == {"found": False}
    assert not search_has_page(service, ADDED_TOKEN, "added")

    context = service.context(ADDED_TOKEN, limit=3)
    assert context.answerable is False
    assert context.evidence == []

    graph = service.graph()
    assert "page:added" not in {node["id"] for node in graph["nodes"]}
    assert not graph_neighbors_link_to(service, "page:added")


def assert_topic_visible(service: LlmWikiService, expected_token: str) -> None:
    manifest = service.manifest()
    assert manifest.page_count == 2
    assert manifest.approved_page_count == 2

    page = service.read("topic")
    assert page["title"] == "Topic"
    assert expected_token in page["text"]
    assert search_has_page(service, expected_token, "topic")

    context = service.context(expected_token, limit=3)
    assert context.answerable is True
    assert any(item.page_id == "topic" for item in context.evidence)

    graph = service.graph()
    assert "page:topic" in {node["id"] for node in graph["nodes"]}
    assert graph_neighbors_link_to(service, "page:topic")


def assert_topic_blocked(service: LlmWikiService, expected_token: str) -> None:
    manifest = service.manifest()
    assert manifest.page_count == 2
    assert manifest.approved_page_count == 1

    assert service.read("topic") == {"found": False, "reason": "not approved for serving"}
    draft_page = service.read("topic", include_drafts=True)
    assert expected_token in draft_page["text"]

    assert not search_has_page(service, expected_token, "topic")
    assert any(
        item["page_id"] == "topic"
        for item in service.search(expected_token, limit=4, include_drafts=True)
    )

    context = service.context(expected_token, limit=3)
    assert context.answerable is False
    assert context.evidence == []
    draft_context = service.context(expected_token, limit=3, include_drafts=True)
    assert draft_context.answerable is True
    assert any(item.page_id == "topic" for item in draft_context.evidence)

    graph = service.graph()
    assert "page:topic" not in {node["id"] for node in graph["nodes"]}
    draft_graph = service.graph(include_drafts=True)
    assert "page:topic" in {node["id"] for node in draft_graph["nodes"]}
    assert not graph_neighbors_link_to(service, "page:topic")
    assert graph_neighbors_link_to(service, "page:topic", include_drafts=True)


def assert_adapter_projection(
    service: LlmWikiService, expected_adapter: str, expected_implementation: str
) -> None:
    manifest = service.manifest()
    assert manifest.adapter == expected_adapter
    assert manifest.implementation == expected_implementation

    bundle = service.source_bundle()
    assert bundle.adapter == expected_adapter
    assert bundle.implementation == expected_implementation

    assert OLD_TOKEN in service.read("topic")["text"]
    assert search_has_page(service, OLD_TOKEN, "topic")

    context = service.context(OLD_TOKEN, limit=3)
    assert context.adapter == expected_adapter
    assert context.implementation == expected_implementation
    assert any(item.page_id == "topic" for item in context.evidence)

    graph = service.graph()
    assert "page:topic" in {node["id"] for node in graph["nodes"]}
    assert graph_neighbors_link_to(service, "page:topic")


def assert_surface_generation(client: TestClient, expected_token: str, absent_token: str) -> None:
    http_read = response_json(client.get("/read/topic"))
    assert expected_token in http_read["title"]
    assert expected_token in http_read["text"]
    assert absent_token not in json.dumps(http_read)

    http_query = response_json(client.post("/query", json={"query": expected_token, "limit": 2}))
    assert http_query["answerable"] is True
    assert any(item["page_id"] == "topic" for item in http_query["evidence"])

    absent_query = response_json(client.post("/query", json={"query": absent_token, "limit": 2}))
    assert absent_query["answerable"] is False
    assert absent_query["evidence"] == []

    assert_surface_neighbor_generation(http_graph_neighbors(client), expected_token, absent_token)

    mcp_read = mcp_tool_call(client, "llmwiki_read", {"page_id": "topic"})
    assert expected_token in mcp_read["title"]
    assert expected_token in mcp_read["text"]
    assert absent_token not in json.dumps(mcp_read)

    mcp_neighbors = mcp_tool_call(
        client,
        "llmwiki_graph_neighbors",
        producer_surface_neighbor_args(),
    )
    assert_surface_neighbor_generation(mcp_neighbors, expected_token, absent_token)

    stream_read = mcp_stream_tool_call(client, "llmwiki_read", {"page_id": "topic"})
    assert expected_token in stream_read["title"]
    assert expected_token in stream_read["text"]
    assert absent_token not in json.dumps(stream_read)

    stream_neighbors = mcp_stream_tool_call(
        client,
        "llmwiki_graph_neighbors",
        producer_surface_neighbor_args(),
    )
    assert_surface_neighbor_generation(stream_neighbors, expected_token, absent_token)


def assert_surface_neighbor_generation(
    neighbors: dict[str, Any], expected_token: str, absent_token: str
) -> None:
    encoded = json.dumps(neighbors)
    assert expected_token in encoded
    assert absent_token not in encoded
    assert neighbors["seeds"] == ["page:index"]
    assert any(node["id"] == "page:topic" for node in neighbors["nodes"])
    assert any(
        edge["source"] == "page:index"
        and edge["target"] == "page:topic"
        and edge["relation"] == "links_to"
        for edge in neighbors["edges"]
    )


def producer_surface_identity(client: TestClient) -> dict[str, str]:
    health = response_json(client.get("/health"))["source"]
    bundle = response_json(client.get("/source-bundle"))
    identity = {
        "health_signature": health["projection"]["signature"],
        "health_bundle_id": health["bundle_id"],
        "bundle_signature": bundle["projection"]["signature"],
        "bundle_id": bundle["bundle_id"],
    }
    assert identity["health_signature"].startswith("sha256:")
    assert identity["bundle_signature"] == identity["health_signature"]
    assert identity["bundle_id"] == identity["health_bundle_id"]
    return identity


def graph_neighbors_link_to(
    service: LlmWikiService, target: str, *, include_drafts: bool = False
) -> bool:
    neighborhood = service.graph_neighbors(
        seeds=["index"],
        relations=["links_to"],
        include_drafts=include_drafts,
    )
    return any(edge.source == "page:index" and edge.target == target for edge in neighborhood.edges)


def assert_graph_relation(service: LlmWikiService, *, expected: str, absent: str) -> None:
    graph = service.graph(include_drafts=True)
    relations = {edge["relation"] for edge in graph["edges"]}
    assert expected in relations
    assert absent not in relations

    neighborhood = service.graph_neighbors(
        seeds=["index"],
        relations=[expected],
        include_drafts=True,
    )
    assert any(edge.relation == expected for edge in neighborhood.edges)

    absent_neighborhood = service.graph_neighbors(
        seeds=["index"],
        relations=[absent],
        include_drafts=True,
    )
    assert all(edge.relation != absent for edge in absent_neighborhood.edges)


def http_graph_neighbors(client: TestClient) -> dict[str, Any]:
    return response_json(
        client.get(
            "/graph/neighborhood",
            params=producer_surface_neighbor_args(http=True),
        )
    )


def producer_surface_neighbor_args(*, http: bool = False) -> dict[str, Any]:
    if http:
        return {
            "seed": "index",
            "depth": "1",
            "direction": "out",
            "relation": "links_to",
            "limit": "10",
        }
    return {
        "seed": "index",
        "depth": 1,
        "direction": "out",
        "relation": "links_to",
        "limit": 10,
    }


def mcp_tool_call(client: TestClient, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    payload = response_json(
        client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
    )
    assert "error" not in payload
    return payload["result"]


def mcp_stream_tool_call(
    client: TestClient, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    payload = response_json(
        client.post(
            MCP_STREAM_PATH,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers=STREAM_HEADERS,
        )
    )
    assert "error" not in payload
    result = payload["result"]
    assert result["isError"] is False
    return result["structuredContent"]


def response_json(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload, dict)
    return payload
