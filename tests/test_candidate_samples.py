from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from llmwiki_serve.adapters import SUPPORTED_IMPLEMENTATIONS, LoadedWiki, load_wiki
from llmwiki_serve.api import NETWORK_MANIFEST_ROOT, create_app
from llmwiki_serve.service import LlmWikiService
from scripts.candidate_sample_artifacts import (
    CANDIDATE_SAMPLES,
    CandidateSample,
    candidate_hot_page_path,
    candidate_sidecar_graph_path,
    candidate_source_root,
    candidate_sync_page_location,
    create_candidate_sample,
    frontmatter,
    sidecar_endpoint,
    tree_hash,
    write_json,
    write_markdown,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "candidate",
    CANDIDATE_SAMPLES,
    ids=[candidate.directory_name for candidate in CANDIDATE_SAMPLES],
)
def test_candidate_sample_projection_shape(candidate: CandidateSample, tmp_path: Path) -> None:
    root = tmp_path / candidate.directory_name
    generated = create_candidate_sample(root, candidate)
    representative_page_id = generated.representative_page_id
    hidden_page_id = generated.hidden_page_id
    before_hash = tree_hash(root)

    loaded = load_wiki(root)
    service = LlmWikiService(root)
    manifest = service.manifest()
    context = service.context("release readiness projection", limit=4)
    graph = service.graph(limit=500)
    read = service.read(representative_page_id)
    search = service.search("release readiness projection", limit=4)

    profiles = {profile.implementation: profile for profile in SUPPORTED_IMPLEMENTATIONS}
    assert profiles[candidate.catalog_implementation].adapter == candidate.expected_adapter
    assert loaded.adapter == candidate.expected_adapter
    assert loaded.implementation == candidate.expected_implementation
    assert manifest.adapter == candidate.expected_adapter
    assert manifest.implementation == candidate.expected_implementation
    assert manifest.page_count >= 2
    assert manifest.approved_page_count >= 2

    assert context.answerable
    assert context.evidence
    assert search
    assert read["title"]
    assert read["text"]
    assert service.read(hidden_page_id) == {"found": False, "reason": "not approved for serving"}
    assert service.read(hidden_page_id, include_drafts=True)["id"] == hidden_page_id
    assert all(item["page_id"] != hidden_page_id for item in service.search("zzcandidateembargo"))
    assert any(
        item["page_id"] == hidden_page_id
        for item in service.search("zzcandidateembargo", include_drafts=True)
    )

    assert len(loaded.pages) >= 2
    assert all(page.headings for page in loaded.pages)
    assert all(page.tags for page in loaded.pages)
    assert all(page.source_refs for page in loaded.pages)
    assert any(page.links for page in loaded.pages)
    if candidate.expected_adapter == "obsidian":
        assert manifest.overview_page == "wiki/overview.md"
        assert any(
            page.path == "wiki/overview.md" and page.role == "overview" for page in loaded.pages
        )
        assert any(
            item.path == "wiki/overview.md" and item.role == "overview"
            for item in context.orientation
        )

    node_ids = {node["id"] for node in graph["nodes"]}
    page_node_ids = {node_id for node_id in node_ids if node_id.startswith("page:")}
    assert len(page_node_ids) >= 2
    assert f"page:{hidden_page_id}" not in node_ids
    assert any(node["kind"] == "heading" for node in graph["nodes"])
    assert any(node["kind"] == "source_ref" for node in graph["nodes"])
    assert any(node["kind"] == "tag" for node in graph["nodes"])
    assert any(edge["relation"] == "links_to" for edge in graph["edges"])
    assert all(edge["source"] in node_ids and edge["target"] in node_ids for edge in graph["edges"])

    if candidate.expect_sidecar:
        assert loaded.sidecar_graph_edges
        sidecar_edges = [
            edge
            for edge in graph["edges"]
            if edge["metadata"].get("source") == "graph.json"
            and edge["metadata"].get("path") == "graph/graph.json"
        ]
        assert any(edge["relation"] == "supports" for edge in sidecar_edges)
        assert any(edge["relation"] == "tracks" for edge in sidecar_edges)
        assert any(edge["metadata"].get("confidence") == 0.91 for edge in sidecar_edges)
        duplicate_links = [
            edge
            for edge in graph["edges"]
            if edge["source"] == "page:index"
            and edge["target"] == f"page:{representative_page_id}"
            and edge["relation"] == "links_to"
        ]
        assert len(duplicate_links) == 1
        assert duplicate_links[0]["metadata"].get("source") == "graph.json"
        assert duplicate_links[0]["metadata"].get("path") == "graph/graph.json"
        assert duplicate_links[0]["metadata"].get("confidence") == 0.77
        assert not any(
            edge["source"] == f"page:{representative_page_id}"
            and edge["target"] == f"page:{hidden_page_id}"
            and edge["relation"] == "draft_neighbor"
            for edge in graph["edges"]
        )
        draft_graph = service.graph(limit=500, include_drafts=True)
        assert any(
            edge["source"] == f"page:{representative_page_id}"
            and edge["target"] == f"page:{hidden_page_id}"
            and edge["relation"] == "draft_neighbor"
            for edge in draft_graph["edges"]
        )
    else:
        assert not loaded.sidecar_graph_edges

    run_candidate_protocol_surface_smoke(root, candidate, representative_page_id, hidden_page_id)

    assert tree_hash(root) == before_hash

    run_candidate_sync_probe(root, candidate, service, loaded, representative_page_id)


@pytest.mark.parametrize(
    "candidate",
    CANDIDATE_SAMPLES,
    ids=[candidate.directory_name for candidate in CANDIDATE_SAMPLES],
)
def test_candidate_sample_refreshes_review_state_and_hub_pages(
    candidate: CandidateSample, tmp_path: Path
) -> None:
    root = tmp_path / f"{candidate.directory_name}-state-hub"
    representative_page_id = candidate.build(root, candidate)
    loaded = load_wiki(root)
    service = LlmWikiService(root)
    representative_page = next(page for page in loaded.pages if page.id == representative_page_id)
    page_path = candidate_source_root(root, loaded) / representative_page.path
    original_text = page_path.read_text(encoding="utf-8")
    draft_phrase = f"zzcandidatereviewdraft-{candidate.directory_name}"
    approved_phrase = f"zzcandidatereviewapproved-{candidate.directory_name}"

    assert 'review_state: "approved"' in original_text
    assert service.read(representative_page_id)["id"] == representative_page_id

    page_path.write_text(
        original_text.replace('review_state: "approved"', 'review_state: "draft"', 1)
        + f"\n\n{draft_phrase} should be visible only when drafts are included.\n",
        encoding="utf-8",
    )
    draft_hash = tree_hash(root)

    assert service.read(representative_page_id) == {
        "found": False,
        "reason": "not approved for serving",
    }
    assert all(item["page_id"] != representative_page_id for item in service.search(draft_phrase))
    assert any(
        item["page_id"] == representative_page_id
        for item in service.search(draft_phrase, include_drafts=True)
    )
    assert all(
        item.page_id != representative_page_id for item in service.context(draft_phrase).evidence
    )
    assert any(
        item.page_id == representative_page_id
        for item in service.context(draft_phrase, include_drafts=True).evidence
    )
    assert f"page:{representative_page_id}" not in {node["id"] for node in service.graph()["nodes"]}
    assert f"page:{representative_page_id}" in {
        node["id"] for node in service.graph(include_drafts=True)["nodes"]
    }
    assert_service_read_only(root, service, draft_phrase, representative_page_id)
    assert tree_hash(root) == draft_hash

    page_path.write_text(
        original_text + f"\n\n{approved_phrase} confirms approval state refresh.\n",
        encoding="utf-8",
    )
    approved_hash = tree_hash(root)

    assert service.read(representative_page_id)["id"] == representative_page_id
    assert any(
        item["page_id"] == representative_page_id for item in service.search(approved_phrase)
    )
    assert any(
        item.page_id == representative_page_id for item in service.context(approved_phrase).evidence
    )
    assert f"page:{representative_page_id}" in {node["id"] for node in service.graph()["nodes"]}
    assert_service_read_only(root, service, approved_phrase, representative_page_id)
    assert tree_hash(root) == approved_hash

    hot_path = candidate_hot_page_path(root, candidate, loaded)
    if hot_path is None:
        return

    assert service.manifest().hot_page == ""
    hot_phrase = f"zzcandidatehotadd-{candidate.directory_name}"
    write_markdown(
        hot_path,
        frontmatter(
            title="Candidate Hot Page",
            review_state="approved",
            tags=["candidate-hot"],
            source_refs=["CANDIDATE-HOT"],
        )
        + f"""
# Candidate Hot Page

{hot_phrase} confirms hot page addition is projected.
""",
    )
    hot_hash = tree_hash(root)

    assert service.manifest().hot_page == "hot.md"
    assert any(item["page_id"] == "hot" for item in service.search(hot_phrase))
    assert service.context("").orientation[0].role == "hot"
    assert "page:hot" in {node["id"] for node in service.graph()["nodes"]}
    assert_service_read_only(root, service, hot_phrase, "hot")
    assert tree_hash(root) == hot_hash

    hot_update_phrase = f"zzcandidatehotupdate-{candidate.directory_name}"
    hot_path.write_text(
        hot_path.read_text(encoding="utf-8")
        + f"\n## Hot Update\n\n{hot_update_phrase} confirms hot page updates.\n",
        encoding="utf-8",
    )
    hot_update_hash = tree_hash(root)

    assert any(item["page_id"] == "hot" for item in service.search(hot_update_phrase))
    assert service.context("").orientation[0].page_id == "hot"
    assert_service_read_only(root, service, hot_update_phrase, "hot")
    assert tree_hash(root) == hot_update_hash

    hot_path.unlink()
    hot_delete_hash = tree_hash(root)

    assert service.manifest().hot_page == ""
    assert all(item["page_id"] != "hot" for item in service.search(hot_phrase))
    assert service.read("hot") == {"found": False}
    assert "page:hot" not in {node["id"] for node in service.graph(include_drafts=True)["nodes"]}
    assert_service_read_only(root, service, "release readiness projection", representative_page_id)
    assert tree_hash(root) == hot_delete_hash


@pytest.mark.parametrize(
    "candidate",
    CANDIDATE_SAMPLES,
    ids=[candidate.directory_name for candidate in CANDIDATE_SAMPLES],
)
def test_candidate_sample_refreshes_sidecar_add_update_delete_with_adapter_paths(
    candidate: CandidateSample, tmp_path: Path
) -> None:
    root = tmp_path / f"{candidate.directory_name}-sidecar"
    candidate.build(root, candidate)
    loaded = load_wiki(root)
    service = LlmWikiService(root)
    pages = [page for page in loaded.pages if page.approved_for_serving]
    source_page = pages[0]
    target_page = next(page for page in pages if page.id != source_page.id)
    relation_v1 = f"sync_path_{candidate.directory_name.replace('-', '_')}_v1"
    relation_v2 = f"sync_path_{candidate.directory_name.replace('-', '_')}_v2"
    sidecar_path = candidate_sidecar_graph_path(root, loaded)

    assert not sidecar_edges(service, relation_v1)
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        sidecar_path,
        {
            "edges": [
                {
                    "from": sidecar_endpoint(loaded, source_page),
                    "to": sidecar_endpoint(loaded, target_page),
                    "type": relation_v1,
                    "confidence": 0.31,
                }
            ]
        },
    )
    created_hash = tree_hash(root)

    created_edges = sidecar_edges(service, relation_v1)
    assert any(
        edge["source"] == f"page:{source_page.id}"
        and edge["target"] == f"page:{target_page.id}"
        and edge["metadata"].get("confidence") == 0.31
        and edge["metadata"].get("path") == sidecar_path.relative_to(root).as_posix()
        for edge in created_edges
    )
    assert_service_read_only(root, service, source_page.title, source_page.id)
    assert tree_hash(root) == created_hash

    write_json(
        sidecar_path,
        {
            "edges": [
                {
                    "from": sidecar_endpoint(loaded, source_page),
                    "to": sidecar_endpoint(loaded, target_page),
                    "type": relation_v2,
                    "confidence": 0.32,
                }
            ]
        },
    )
    updated_hash = tree_hash(root)

    assert not sidecar_edges(service, relation_v1)
    updated_edges = sidecar_edges(service, relation_v2)
    assert any(edge["metadata"].get("confidence") == 0.32 for edge in updated_edges)
    assert_service_read_only(root, service, target_page.title, target_page.id)
    assert tree_hash(root) == updated_hash

    sidecar_path.unlink()
    deleted_hash = tree_hash(root)

    assert not sidecar_edges(service, relation_v2)
    assert_service_read_only(root, service, source_page.title, source_page.id)
    assert tree_hash(root) == deleted_hash


def test_generated_candidate_sample_artifacts_drive_serve_projection_and_sync(
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "candidate-artifacts"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate_candidate_samples.py"),
            str(output_root),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((output_root / "candidate-samples.json").read_text(encoding="utf-8"))
    generated_samples = manifest["samples"]
    assert manifest["schema"] == "llmwiki-serve-candidate-samples-v1"
    assert len(generated_samples) == len(CANDIDATE_SAMPLES)

    candidates_by_directory = {
        candidate.directory_name: candidate for candidate in CANDIDATE_SAMPLES
    }
    refreshed_over_http = False
    for sample in generated_samples:
        candidate = candidates_by_directory[str(sample["directory_name"])]
        root = output_root / str(sample["path"])
        representative_page_id = str(sample["representative_page_id"])
        hidden_page_id = str(sample["hidden_page_id"])
        before_hash = tree_hash(root)

        loaded = load_wiki(root)
        service = LlmWikiService(root)
        assert loaded.adapter == candidate.expected_adapter
        assert service.manifest().approved_page_count >= 2
        assert_closed_graph(service.graph(limit=1))
        run_candidate_protocol_surface_smoke(
            root, candidate, representative_page_id, hidden_page_id
        )
        assert tree_hash(root) == before_hash

        if candidate.expect_sidecar and not refreshed_over_http:
            run_candidate_http_refresh_smoke(root, candidate, representative_page_id)
            refreshed_over_http = True

        run_candidate_sync_probe(root, candidate, service, loaded, representative_page_id)

    assert refreshed_over_http


def run_candidate_sync_probe(
    root: Path,
    candidate: CandidateSample,
    service: LlmWikiService,
    loaded: LoadedWiki,
    representative_page_id: str,
) -> None:
    page = next(page for page in loaded.pages if page.id == representative_page_id)
    source_root = candidate_source_root(root, loaded)
    page_path = source_root / page.path
    sync_phrase = f"zzcandidatesyncprobe-{candidate.directory_name}"

    assert not any(
        item["page_id"] == representative_page_id for item in service.search(sync_phrase)
    )

    page_path.write_text(
        page_path.read_text(encoding="utf-8")
        + f"\n## Candidate Sync Probe\n\n{sync_phrase} confirms service refresh.\n",
        encoding="utf-8",
    )
    after_markdown_sync_hash = tree_hash(root)

    refreshed_search = service.search(sync_phrase)
    refreshed_graph = service.graph(limit=500, include_drafts=True)

    assert any(item["page_id"] == representative_page_id for item in refreshed_search)
    assert any(
        node["kind"] == "heading"
        and node["path"] == page.path
        and node["label"] == "Candidate Sync Probe"
        for node in refreshed_graph["nodes"]
    )
    assert tree_hash(root) == after_markdown_sync_hash

    added_slug = f"candidate-added-sync-{candidate.directory_name}"
    added_page_path, added_page_id = candidate_sync_page_location(
        root, candidate, loaded, added_slug
    )
    added_phrase = f"zzcandidateadded-{candidate.directory_name}"

    assert service.read(added_page_id) == {"found": False}
    write_markdown(
        added_page_path,
        frontmatter(
            title="Candidate Added Sync",
            review_state="approved",
            tags=["candidate-sync"],
            source_refs=["CANDIDATE-ADDED-SYNC"],
        )
        + f"""
# Candidate Added Sync

{added_phrase} confirms new Markdown pages are indexed after service start.
""",
    )
    added_graph = service.graph(limit=500, include_drafts=True)

    assert any(item["page_id"] == added_page_id for item in service.search(added_phrase))
    assert service.read(added_page_id)["id"] == added_page_id
    assert f"page:{added_page_id}" in {node["id"] for node in added_graph["nodes"]}

    added_page_path.unlink()

    assert all(item["page_id"] != added_page_id for item in service.search(added_phrase))
    assert service.read(added_page_id) == {"found": False}
    assert f"page:{added_page_id}" not in {
        node["id"] for node in service.graph(limit=500, include_drafts=True)["nodes"]
    }
    assert tree_hash(root) == after_markdown_sync_hash

    if candidate.expect_sidecar:
        graph_path = root / "graph" / "graph.json"
        graph_data = json.loads(graph_path.read_text(encoding="utf-8"))
        graph_data["edges"].append(
            {
                "from": representative_page_id,
                "to": f"SYNC-{candidate.directory_name.upper()}-1",
                "type": "sync_confirms",
                "confidence": 0.66,
            }
        )
        write_json(graph_path, graph_data)
        after_sidecar_sync_hash = tree_hash(root)

        sidecar_graph = service.graph(limit=500, include_drafts=True)

        assert any(
            edge["source"] == f"page:{representative_page_id}"
            and edge["relation"] == "sync_confirms"
            and edge["metadata"].get("confidence") == 0.66
            for edge in sidecar_graph["edges"]
        )
        assert tree_hash(root) == after_sidecar_sync_hash


def run_candidate_http_refresh_smoke(
    root: Path, candidate: CandidateSample, representative_page_id: str
) -> None:
    client = TestClient(create_app(root))
    a2a_client = TestClient(create_app(root, enable_a2a_compat=True))
    loaded = load_wiki(root)
    page = next(page for page in loaded.pages if page.id == representative_page_id)
    page_path = candidate_source_root(root, loaded) / page.path

    markdown_phrase = f"zzcandidatehttprefresh-{candidate.directory_name}"
    assert all(
        item["page_id"] != representative_page_id
        for item in client.post("/search", json={"query": markdown_phrase}).json()["results"]
    )
    page_path.write_text(
        page_path.read_text(encoding="utf-8")
        + f"\n## HTTP Refresh Probe\n\n{markdown_phrase} confirms HTTP refresh.\n",
        encoding="utf-8",
    )
    markdown_hash = tree_hash(root)

    assert any(
        item["page_id"] == representative_page_id
        for item in client.post("/search", json={"query": markdown_phrase}).json()["results"]
    )
    assert any(
        item["page_id"] == representative_page_id
        for item in mcp_tool_call(
            client,
            "llmwiki_search",
            {"query": markdown_phrase},
        )["results"]
    )
    assert any(
        item["page_id"] == representative_page_id
        for item in a2a_context_data(a2a_client, markdown_phrase)["evidence"]
    )
    assert tree_hash(root) == markdown_hash

    hot_path = candidate_hot_page_path(root, candidate, loaded)
    if hot_path is not None:
        hot_phrase = f"zzcandidatehttprefreshhot-{candidate.directory_name}"
        write_markdown(
            hot_path,
            frontmatter(
                title="HTTP Refresh Hot Page",
                review_state="approved",
                tags=["candidate-http-refresh"],
                source_refs=["CANDIDATE-HTTP-HOT"],
            )
            + f"""
# HTTP Refresh Hot Page

{hot_phrase} confirms hot-page refresh through network surfaces.
""",
        )
        hot_hash = tree_hash(root)

        assert client.get("/manifest").json()["hot_page"] == "hot.md"
        assert any(
            item["page_id"] == "hot"
            for item in client.post("/search", json={"query": hot_phrase}).json()["results"]
        )
        assert any(
            item["page_id"] == "hot"
            for item in mcp_tool_call(client, "llmwiki_context", {"query": hot_phrase})["evidence"]
        )
        assert any(
            item["page_id"] == "hot"
            for item in a2a_context_data(a2a_client, hot_phrase)["evidence"]
        )
        assert tree_hash(root) == hot_hash

    if candidate.expect_sidecar:
        sidecar_phrase = f"HTTP-{candidate.directory_name.upper()}-REFRESH"
        graph_path = root / "graph" / "graph.json"
        graph_data = json.loads(graph_path.read_text(encoding="utf-8"))
        graph_data["edges"].append(
            {
                "from": representative_page_id,
                "to": sidecar_phrase,
                "type": "http_refresh_confirms",
                "confidence": 0.68,
            }
        )
        write_json(graph_path, graph_data)
        sidecar_hash = tree_hash(root)

        graph = client.get("/graph?limit=500").json()
        assert any(
            edge["source"] == f"page:{representative_page_id}"
            and edge["relation"] == "http_refresh_confirms"
            for edge in graph["edges"]
        )
        assert any(
            edge["source"] == f"page:{representative_page_id}"
            and edge["relation"] == "http_refresh_confirms"
            for edge in mcp_tool_call(client, "llmwiki_graph", {"limit": 500})["edges"]
        )
        assert any(
            edge["source"] == f"page:{representative_page_id}"
            and edge["relation"] == "http_refresh_confirms"
            for edge in a2a_context_data(a2a_client, markdown_phrase)["graph"]["edges"]
        )
        assert tree_hash(root) == sidecar_hash


def run_candidate_protocol_surface_smoke(
    root: Path, candidate: CandidateSample, representative_page_id: str, hidden_page_id: str
) -> None:
    client = TestClient(create_app(root))
    a2a_client = TestClient(create_app(root, enable_a2a_compat=True))
    query_text = "release readiness projection"

    manifest_response = client.get("/manifest")
    assert manifest_response.status_code == 200
    manifest = manifest_response.json()
    assert manifest["adapter"] == candidate.expected_adapter
    assert manifest["implementation"] == candidate.expected_implementation
    assert manifest["root"] == NETWORK_MANIFEST_ROOT
    assert str(root) not in manifest_response.text
    assert_payload_omits_source_roots(manifest, root)

    query_response = client.post("/query", json={"query": query_text, "limit": 4})
    assert query_response.status_code == 200
    query = query_response.json()
    assert query["answerable"] is True
    assert query["evidence"]
    assert query["graph"]["nodes"]
    assert_closed_graph(query["graph"])
    assert_payload_omits_source_roots(query, root)

    search_response = client.post("/search", json={"query": query_text, "limit": 4})
    assert search_response.status_code == 200
    search = search_response.json()
    assert search["results"]
    assert any(item["page_id"] == representative_page_id for item in search["results"])
    assert_payload_omits_source_roots(search, root)

    read_response = client.get(f"/read/{quote(representative_page_id, safe='/')}")
    assert read_response.status_code == 200
    read = read_response.json()
    assert read["id"] == representative_page_id
    assert read["text"]
    assert_payload_omits_source_roots(read, root)

    graph_response = client.get("/graph?limit=500")
    assert graph_response.status_code == 200
    graph = graph_response.json()
    assert graph["nodes"]
    assert_closed_graph(graph)
    assert_payload_omits_source_roots(graph, root)

    limit_one_graph_response = client.get("/graph?limit=1")
    assert limit_one_graph_response.status_code == 200
    limit_one_graph = limit_one_graph_response.json()
    assert len(limit_one_graph["nodes"]) == 1
    assert_closed_graph(limit_one_graph)
    assert_payload_omits_source_roots(limit_one_graph, root)

    mcp_response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": candidate.directory_name,
            "method": "tools/call",
            "params": {
                "name": "llmwiki_context",
                "arguments": {"query": query_text, "limit": 4},
            },
        },
    )
    assert mcp_response.status_code == 200
    mcp = mcp_response.json()
    assert "error" not in mcp
    assert mcp["id"] == candidate.directory_name
    assert mcp["result"]["evidence"]
    assert mcp["result"]["graph"]["nodes"]
    assert_closed_graph(mcp["result"]["graph"])
    assert_payload_omits_source_roots(mcp, root)

    mcp_search = mcp_tool_call(
        client,
        "llmwiki_search",
        {"query": query_text, "limit": 4},
    )
    mcp_read = mcp_tool_call(
        client,
        "llmwiki_read",
        {"page_id": representative_page_id},
    )
    mcp_graph = mcp_tool_call(
        client,
        "llmwiki_graph",
        {"limit": 500},
    )

    assert mcp_search == search
    assert mcp_read == read
    assert mcp_graph == graph
    assert_closed_graph(mcp_graph)
    assert_payload_omits_source_roots(mcp_search, root)
    assert_payload_omits_source_roots(mcp_read, root)
    assert_payload_omits_source_roots(mcp_graph, root)

    mcp_limit_one_graph = mcp_tool_call(
        client,
        "llmwiki_graph",
        {"limit": 1},
    )
    assert len(mcp_limit_one_graph["nodes"]) == 1
    assert_closed_graph(mcp_limit_one_graph)
    assert_payload_omits_source_roots(mcp_limit_one_graph, root)

    assert client.post("/message:send", json={"data": {"query": query_text}}).status_code == 404

    a2a_response = a2a_client.post("/message:send", json={"data": {"query": query_text}})
    assert a2a_response.status_code == 200
    a2a = a2a_response.json()
    assert a2a["status"] == "completed"
    assert a2a["message"]["role"] == "agent"
    context_artifact = next(
        artifact for artifact in a2a["artifacts"] if artifact["name"] == "llmwiki_context"
    )
    context_data = next(
        part["data"] for part in context_artifact["parts"] if part["kind"] == "data"
    )
    assert context_data["evidence"]
    assert context_data["graph"]["nodes"]
    assert_closed_graph(context_data["graph"])
    assert_payload_omits_source_roots(a2a, root)

    assert_hidden_draft_blocked_on_network_surfaces(client, hidden_page_id)


def assert_hidden_draft_blocked_on_network_surfaces(
    client: TestClient, hidden_page_id: str
) -> None:
    hidden_node_id = f"page:{hidden_page_id}"

    query = client.post(
        "/query", json={"query": "zzcandidateembargo", "include_drafts": True}
    ).json()
    search = client.post(
        "/search", json={"query": "zzcandidateembargo", "include_drafts": True}
    ).json()
    read = client.get(f"/read/{quote(hidden_page_id, safe='/')}?include_drafts=true").json()
    graph = client.get("/graph?limit=500&include_drafts=true").json()
    mcp_context = mcp_tool_call(
        client,
        "llmwiki_context",
        {"query": "zzcandidateembargo", "include_drafts": True},
    )
    mcp_search = mcp_tool_call(
        client,
        "llmwiki_search",
        {"query": "zzcandidateembargo", "include_drafts": True},
    )
    mcp_read = mcp_tool_call(
        client,
        "llmwiki_read",
        {"page_id": hidden_page_id, "include_drafts": True},
    )
    mcp_graph = mcp_tool_call(
        client,
        "llmwiki_graph",
        {"limit": 500, "include_drafts": True},
    )

    assert all(item["page_id"] != hidden_page_id for item in query["evidence"])
    assert all(item["page_id"] != hidden_page_id for item in search["results"])
    assert read == {"found": False, "reason": "not approved for serving"}
    assert hidden_node_id not in {node["id"] for node in query["graph"]["nodes"]}
    assert hidden_node_id not in {node["id"] for node in graph["nodes"]}
    assert all(item["page_id"] != hidden_page_id for item in mcp_context["evidence"])
    assert all(item["page_id"] != hidden_page_id for item in mcp_search["results"])
    assert hidden_node_id not in {node["id"] for node in mcp_context["graph"]["nodes"]}
    assert mcp_read == {"found": False, "reason": "not approved for serving"}
    assert hidden_node_id not in {node["id"] for node in mcp_graph["nodes"]}


def mcp_tool_call(client: TestClient, name: str, arguments: dict[str, object]) -> dict[str, object]:
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


def a2a_context_data(client: TestClient, query: str) -> dict[str, object]:
    response = client.post("/message:send", json={"data": {"query": query}})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    context_artifact = next(
        artifact for artifact in payload["artifacts"] if artifact["name"] == "llmwiki_context"
    )
    return next(part["data"] for part in context_artifact["parts"] if part["kind"] == "data")


def assert_closed_graph(graph: dict[str, list[dict[str, object]]]) -> None:
    node_ids = {str(node["id"]) for node in graph["nodes"]}
    assert all(edge["source"] in node_ids and edge["target"] in node_ids for edge in graph["edges"])


def assert_payload_omits_source_roots(payload: object, root: Path) -> None:
    serialized = json.dumps(payload, sort_keys=True)
    assert str(root.resolve()) not in serialized
    assert str(ROOT.resolve()) not in serialized


def sidecar_edges(service: LlmWikiService, relation: str) -> list[dict[str, object]]:
    return [
        edge
        for edge in service.graph(limit=500, include_drafts=True)["edges"]
        if edge["relation"] == relation
    ]


def assert_service_read_only(root: Path, service: LlmWikiService, query: str, page_id: str) -> None:
    before_hash = tree_hash(root)

    service.manifest()
    service.context(query, include_drafts=True)
    service.search(query, include_drafts=True)
    service.read(page_id, include_drafts=True)
    service.graph(include_drafts=True)

    assert tree_hash(root) == before_hash
