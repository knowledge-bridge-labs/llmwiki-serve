from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from llmwiki_serve.adapters import SUPPORTED_IMPLEMENTATIONS, load_wiki
from llmwiki_serve.service import LlmWikiService

FIXTURES = Path(__file__).parent / "fixtures"
REPRESENTATIVE_ADAPTER_FIXTURES = {
    "llmwiki-compiler-output": "llmwiki-markdown",
    "native-wiki-root": "llmwiki-markdown",
    "obsidian-vault": "obsidian",
    "foam-workspace": "foam",
    "dendron-workspace": "dendron",
    "quartz-site": "quartz",
    "quartz-yaml-site": "quartz",
    "logseq-graph": "logseq",
}


def test_supported_implementation_catalog_covers_supported_targets() -> None:
    names = {profile.implementation for profile in SUPPORTED_IMPLEMENTATIONS}
    assert names == {
        "atomicstrata/llm-wiki-compiler",
        "nashsu/llm_wiki",
        "SamurAIGPT/llm-wiki-agent",
        "lucasastorian/llmwiki",
        "Pratiyush/llm-wiki",
        "langchain-ai/deepagents examples/llm-wiki",
        "langchain-ai/openwiki",
        "Obsidian vault",
        "logseq/logseq",
        "foambubble/foam",
        "dendronhq/dendron",
        "jackyzha0/quartz",
    }


def test_adapter_detection_for_representative_wiki_formats() -> None:
    for fixture, adapter_name in REPRESENTATIVE_ADAPTER_FIXTURES.items():
        loaded = load_wiki(FIXTURES / fixture)
        assert loaded.adapter == adapter_name
        assert loaded.pages


@pytest.mark.parametrize("adapter_name", ["foam", "dendron", "quartz", "logseq"])
def test_marker_only_adapter_roots_are_unsupported(tmp_path: Path, adapter_name: str) -> None:
    root = tmp_path / f"{adapter_name}-marker-only"
    create_marker_only_root(root, adapter_name)

    with pytest.raises(FileNotFoundError, match="No supported wiki files were found"):
        load_wiki(root)
    with pytest.raises(FileNotFoundError, match="No supported wiki files were found"):
        LlmWikiService(root).manifest()


def test_representative_formats_serve_canonical_graphs() -> None:
    for fixture in [
        "llmwiki-compiler-output",
        "obsidian-vault",
        "foam-workspace",
        "dendron-workspace",
        "quartz-site",
        "logseq-graph",
    ]:
        service = LlmWikiService(FIXTURES / fixture)
        manifest = service.manifest()
        graph = service.graph()
        context = service.context("")
        assert manifest.adapter
        assert manifest.page_count >= 2
        assert graph["nodes"]
        assert any(node["id"].startswith("page:") for node in graph["nodes"])
        assert any(edge["relation"] in {"links_to", "contains", "cites"} for edge in graph["edges"])
        assert context.answerable


@pytest.mark.parametrize("fixture", REPRESENTATIVE_ADAPTER_FIXTURES)
def test_adapters_do_not_write_source_files(fixture: str) -> None:
    root = FIXTURES / fixture
    before = tree_hash(root)
    loaded = load_wiki(root)
    service = LlmWikiService(root)

    service.manifest()
    service.graph(include_drafts=True)
    service.context("release", include_drafts=True)
    service.search("release", include_drafts=True)
    service.read(loaded.pages[0].id, include_drafts=True)

    assert tree_hash(root) == before


def test_native_llmwiki_nested_wiki_source_root_and_overview_hub() -> None:
    root = FIXTURES / "native-wiki-root"
    loaded = load_wiki(root)

    assert loaded.root == root.resolve()
    assert loaded.adapter == "llmwiki-markdown"
    assert loaded.metadata["source_root"] == "wiki"
    assert loaded.title == "Native Wiki Fixture"
    assert any(page.role == "overview" for page in loaded.pages)
    assert {page.path for page in loaded.pages} >= {
        "overview.md",
        "CRITICAL_FACTS.md",
        "concepts/release.md",
    }

    index = LlmWikiService(root).index()
    assert index.metadata["source_root"] == "wiki"
    assert any(
        node.id == "page:overview" and node.metadata["source_root"] == "wiki"
        for node in index.nodes
    )
    service = LlmWikiService(root)
    assert service.read("concepts/release")["path"] == "concepts/release.md"
    assert service.read("concepts/release.md")["path"] == "concepts/release.md"
    assert service.read("wiki/concepts/release.md") == {"found": False}


def test_openwiki_quickstart_entrypoint_is_index_hub(tmp_path: Path) -> None:
    root = tmp_path / "openwiki"
    (root / "architecture").mkdir(parents=True)
    write_markdown(
        root / "quickstart.md",
        """
---
wiki_title: OpenWiki Fixture
review_state: approved
---
# OpenWiki Quickstart

Start here for the generated repository documentation.
""",
    )
    write_markdown(
        root / "architecture" / "overview.md",
        """
---
review_state: approved
---
# Architecture Overview

Architecture details.
""",
    )

    loaded = load_wiki(root)
    service = LlmWikiService(root)
    roles = {page.path: page.role for page in loaded.pages}
    manifest = service.manifest()
    context = service.context("")

    assert loaded.adapter == "llmwiki-markdown"
    assert loaded.metadata["source_root"] == "."
    assert loaded.title == "OpenWiki Fixture"
    assert roles["quickstart.md"] == "index"
    assert roles["architecture/overview.md"] == "topic"
    assert manifest.index_page == "quickstart.md"
    assert manifest.hot_page == ""
    assert manifest.overview_page == ""
    assert [item.path for item in context.orientation] == [
        "quickstart.md",
        "architecture/overview.md",
    ]
    assert [item.role for item in context.orientation] == ["index", "topic"]


def test_repository_root_uses_nested_openwiki_source_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "openwiki" / "domain").mkdir(parents=True)
    write_markdown(
        root / "README.md",
        """
# Repository README

This should not be served when openwiki/ is the detected source root.
""",
    )
    write_markdown(
        root / "openwiki" / "quickstart.md",
        """
---
wiki_title: Nested OpenWiki Fixture
review_state: approved
---
# Quickstart

Generated entrypoint.
""",
    )
    write_markdown(
        root / "openwiki" / "domain" / "concepts.md",
        """
---
review_state: approved
---
# Concepts

Generated domain page.
""",
    )

    loaded = load_wiki(root)
    service = LlmWikiService(root)

    assert loaded.adapter == "llmwiki-markdown"
    assert loaded.metadata["source_root"] == "openwiki"
    assert loaded.title == "Nested OpenWiki Fixture"
    assert {page.path for page in loaded.pages} == {"quickstart.md", "domain/concepts.md"}
    assert service.manifest().index_page == "quickstart.md"
    assert service.read("quickstart")["path"] == "quickstart.md"
    assert service.read("openwiki/quickstart") == {"found": False}


def test_generic_nested_quickstart_does_not_become_hub_root(tmp_path: Path) -> None:
    root = tmp_path / "generic-docs"
    (root / "guide").mkdir(parents=True)
    write_markdown(
        root / "README.md",
        """
# Generic Docs

Root readme.
""",
    )
    write_markdown(
        root / "guide" / "quickstart.md",
        """
# Guide Quickstart

Generic quickstart content.
""",
    )

    loaded = load_wiki(root)
    service = LlmWikiService(root)
    roles = {page.path: page.role for page in loaded.pages}
    manifest = service.manifest()
    context = service.context("")

    assert loaded.adapter == "generic-markdown"
    assert loaded.metadata["source_root"] == "."
    assert roles["README.md"] == "topic"
    assert roles["guide/quickstart.md"] == "topic"
    assert manifest.index_page == ""
    assert [item.path for item in context.orientation] == [
        "README.md",
        "guide/quickstart.md",
    ]
    assert [item.role for item in context.orientation] == ["topic", "topic"]


def test_obsidian_adapter_with_nested_wiki_recognizes_hubs_and_serves_whole_vault(
    tmp_path: Path,
) -> None:
    root = tmp_path / "obsidian-vault"
    (root / ".obsidian" / "templates").mkdir(parents=True)
    (root / "wiki" / "concepts").mkdir(parents=True)
    (root / ".raw").mkdir()
    write_markdown(
        root / "root-topic.md",
        """
---
review_state: approved
---
# Root Topic

Root-level Obsidian note.
""",
    )
    write_markdown(
        root / "wiki" / "hot.md",
        """
---
review_state: approved
---
# Nested Wiki Hot

Nested wiki hot page should lead orientation.
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

Nested wiki index should be treated as an index hub in an Obsidian vault.
""",
    )
    write_markdown(
        root / "wiki" / "overview.md",
        """
---
review_state: approved
---
# Nested Wiki Overview

Nested wiki overview should stay in orientation even when the vault root is preserved.
""",
    )
    write_markdown(
        root / "wiki" / "concepts" / "topic.md",
        """
---
review_state: approved
---
# Nested Topic

Nested Obsidian wiki topic.
""",
    )
    write_markdown(
        root / ".raw" / "raw-note.md",
        """
# Raw Note

Raw Obsidian note.
""",
    )
    write_markdown(
        root / ".obsidian" / "templates" / "internal.md",
        """
# Internal Template

This workspace template is not served.
""",
    )

    loaded = load_wiki(root)
    service = LlmWikiService(root)
    page_paths = {page.path for page in loaded.pages}
    roles = {page.path: page.role for page in loaded.pages}
    manifest = service.manifest()
    context = service.context("")

    assert loaded.adapter == "obsidian"
    assert loaded.metadata["source_root"] == "."
    assert loaded.title == "Nested Wiki Title"
    assert {
        "root-topic.md",
        "wiki/hot.md",
        "wiki/index.md",
        "wiki/overview.md",
        "wiki/concepts/topic.md",
        ".raw/raw-note.md",
    } <= page_paths
    assert roles["wiki/hot.md"] == "hot"
    assert roles["wiki/index.md"] == "index"
    assert roles["wiki/overview.md"] == "overview"
    assert roles["wiki/concepts/topic.md"] == "topic"
    assert manifest.hot_page == "wiki/hot.md"
    assert manifest.index_page == "wiki/index.md"
    assert manifest.overview_page == "wiki/overview.md"
    assert [item.path for item in context.orientation[:3]] == [
        "wiki/hot.md",
        "wiki/index.md",
        "wiki/overview.md",
    ]
    assert all(not path.startswith(".obsidian/") for path in page_paths)
    assert service.read("wiki/concepts/topic")["path"] == "wiki/concepts/topic.md"
    assert service.read("concepts/topic") == {"found": False}


def test_root_level_sidecar_endpoints_resolve_nested_source_root_paths(tmp_path: Path) -> None:
    root = tmp_path / "native-root"
    source_root = root / "wiki"
    concepts = source_root / "concepts"
    concepts.mkdir(parents=True)
    write_markdown(
        source_root / "overview.md",
        """
---
wiki_title: Nested Native Fixture
review_state: approved
---
# Nested Native Fixture

Overview links to [[concepts/release]].
""",
    )
    write_markdown(
        concepts / "release.md",
        """
---
title: Release
review_state: approved
---
# Release

Release topic.
""",
    )
    graph = root / "graph"
    graph.mkdir()
    (graph / "graph.json").write_text(
        """
{
  "edges": [
    {
      "from": "wiki/overview.md",
      "to": "wiki/concepts/release.md",
      "type": "supports",
      "confidence": 0.51
    },
    {
      "from": "overview",
      "to": "concepts/release",
      "type": "documents",
      "confidence": 0.52
    }
  ]
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    projected = LlmWikiService(root).graph(include_drafts=True)
    edge_keys = {(edge["source"], edge["target"], edge["relation"]) for edge in projected["edges"]}

    assert ("page:overview", "page:concepts/release", "supports") in edge_keys
    assert ("page:overview", "page:concepts/release", "documents") in edge_keys
    assert all(
        not edge["target"].startswith("placeholder:")
        for edge in projected["edges"]
        if edge["relation"] in {"supports", "documents"}
    )


def test_nested_index_is_topic_and_does_not_steal_manifest_title(tmp_path: Path) -> None:
    root = tmp_path / "native-root"
    source_root = root / "wiki"
    nested = source_root / "area"
    nested.mkdir(parents=True)
    write_markdown(
        source_root / "overview.md",
        """
---
wiki_title: Root Wiki Title
review_state: approved
---
# Root Overview

Top-level overview.
""",
    )
    write_markdown(
        nested / "index.md",
        """
---
wiki_title: Nested Title Must Not Win
review_state: approved
---
# Nested Area Index

Nested topic content.
""",
    )

    loaded = load_wiki(root)
    roles = {page.path: page.role for page in loaded.pages}
    context = LlmWikiService(root).context("")

    assert loaded.title == "Root Wiki Title"
    assert roles["overview.md"] == "overview"
    assert roles["area/index.md"] == "topic"
    assert [item.path for item in context.orientation] == ["overview.md", "area/index.md"]
    assert [item.role for item in context.orientation] == ["overview", "topic"]


def test_projection_adds_tags_unresolved_links_and_graph_json_edges() -> None:
    graph = LlmWikiService(FIXTURES / "native-wiki-root").graph(include_drafts=True)
    nodes = {node["id"]: node for node in graph["nodes"]}
    edge_keys = {(edge["source"], edge["target"], edge["relation"]) for edge in graph["edges"]}

    assert "tag:native" in nodes
    assert "tag:ship-checks" in nodes
    assert "placeholder:missing-topic" in nodes
    assert nodes["external:GH-42"]["kind"] == "external_issue"
    assert ("page:overview", "page:concepts/release", "supports") in edge_keys
    assert ("page:concepts/release", "external:GH-42", "tracks") in edge_keys
    assert ("page:overview", "placeholder:missing-topic", "links_to") in edge_keys
    assert any(
        edge["relation"] == "supports"
        and edge["metadata"].get("confidence") == 0.88
        and edge["metadata"].get("path") == "graph/graph.json"
        for edge in graph["edges"]
    )


def test_adapter_loads_graph_json_as_sidecar_facts() -> None:
    loaded = load_wiki(FIXTURES / "native-wiki-root")

    assert [fact.path for fact in loaded.sidecar_graph_edges] == [
        "graph/graph.json",
        "graph/graph.json",
    ]
    assert {fact.source for fact in loaded.sidecar_graph_edges} == {"graph.json"}
    assert loaded.sidecar_graph_edges[0].confidence == 0.88
    assert loaded.sidecar_graph_edges[1].confidence == 0.7
    assert loaded.sidecar_graph_edges[0].data["from"] == "overview"


def test_duplicate_wikilink_and_sidecar_edges_merge_metadata(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    root.mkdir()
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Duplicate Edge Fixture
review_state: approved
---
# Duplicate Edge Fixture

Index links to [[topic]].
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

Topic content.
""",
    )
    graph = root / "graph"
    graph.mkdir()
    (graph / "graph.json").write_text(
        """
{
  "edges": [
    {"from": "index", "to": "topic", "type": "links_to", "confidence": 0.77}
  ]
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    projected = LlmWikiService(root).graph(include_drafts=True)
    matches = [
        edge
        for edge in projected["edges"]
        if edge["source"] == "page:index"
        and edge["target"] == "page:topic"
        and edge["relation"] == "links_to"
    ]

    assert len(matches) == 1
    assert matches[0]["metadata"]["source"] == "graph.json"
    assert matches[0]["metadata"]["path"] == "graph/graph.json"
    assert matches[0]["metadata"]["confidence"] == 0.77


def test_dendron_vault_source_hierarchy_and_alias_direction() -> None:
    root = FIXTURES / "dendron-workspace"
    loaded = load_wiki(root)

    assert loaded.metadata["source_root"] == "vault"
    assert "root.md" in {page.path for page in loaded.pages}
    assert "vault/root.md" not in {page.path for page in loaded.pages}

    graph = LlmWikiService(root).graph(include_drafts=True)
    edge_keys = {(edge["source"], edge["target"], edge["relation"]) for edge in graph["edges"]}
    assert ("page:process", "page:process.review", "parent_of") in edge_keys
    assert ("page:root", "page:process.review", "links_to") in edge_keys
    assert all(node["label"] != "Review Note" for node in graph["nodes"])


def test_dendron_multi_vault_scans_configured_vault_roots_only(tmp_path: Path) -> None:
    root = tmp_path / "dendron-workspace"
    vault = root / "vault"
    assets = root / "assets"
    other_files = root / "other-files"
    vault.mkdir(parents=True)
    assets.mkdir()
    other_files.mkdir()
    (root / "dendron.yml").write_text(
        """
version: 5
workspace:
  vaults:
    - fsPath: vault
    - fsPath: assets
""".lstrip(),
        encoding="utf-8",
    )
    write_markdown(
        vault / "root.md",
        """
---
title: Vault Root
review_state: approved
---
# Vault Root

Vault root links to [[process]].
""",
    )
    write_markdown(
        vault / "process.md",
        """
---
title: Process
review_state: approved
---
# Process

Configured vault page.
""",
    )
    write_markdown(
        assets / "root.md",
        """
---
title: Assets Root
review_state: approved
---
# Assets Root

Second configured vault page.
""",
    )
    write_markdown(
        other_files / "not-a-note.md",
        """
---
title: Non Vault Markdown
review_state: approved
---
# Non Vault Markdown

This file is outside configured Dendron vaults.
""",
    )

    loaded = load_wiki(root)
    paths = {page.path for page in loaded.pages}
    graph = LlmWikiService(root).graph(include_drafts=True)
    node_ids = {node["id"] for node in graph["nodes"]}
    edge_keys = {(edge["source"], edge["target"], edge["relation"]) for edge in graph["edges"]}

    assert loaded.metadata["source_root"] == "."
    assert loaded.metadata["vault_roots"] == "vault,assets"
    assert paths == {"vault/root.md", "vault/process.md", "assets/root.md"}
    assert "page:other-files/not-a-note" not in node_ids
    assert "page:vault/root" in node_ids
    assert "page:assets/root" in node_ids
    assert ("page:vault/root", "page:vault/process", "links_to") in edge_keys


def test_adapter_metadata_markdown_files_are_not_served_as_pages(tmp_path: Path) -> None:
    foam = tmp_path / "foam"
    (foam / ".foam" / "templates").mkdir(parents=True)
    write_markdown(
        foam / ".foam" / "templates" / "new-note.md",
        """
# Internal Foam Template

This template should not be served as knowledge.
""",
    )
    write_markdown(
        foam / "index.md",
        """
# Foam User Page

This is the user-visible Foam page.
""",
    )

    obsidian = tmp_path / "obsidian"
    (obsidian / ".obsidian" / "templates").mkdir(parents=True)
    write_markdown(
        obsidian / ".obsidian" / "templates" / "daily.md",
        """
# Internal Obsidian Template

This template should not be served as knowledge.
""",
    )
    write_markdown(
        obsidian / "index.md",
        """
# Obsidian User Page

This is the user-visible Obsidian page.
""",
    )

    vscode = tmp_path / "vscode"
    (vscode / ".vscode").mkdir(parents=True)
    write_markdown(
        vscode / ".vscode" / "secret.md",
        """
# Internal VS Code Markdown

This workspace note should not be served as knowledge.
""",
    )
    write_markdown(
        vscode / "index.md",
        """
# VS Code User Page

This is the user-visible workspace page.
""",
    )

    assert {page.path for page in load_wiki(foam).pages} == {"index.md"}
    assert {page.path for page in load_wiki(obsidian).pages} == {"index.md"}
    assert {page.path for page in load_wiki(vscode).pages} == {"index.md"}


def test_symlinked_markdown_outside_root_is_not_served(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

Approved in-root content.
""",
    )
    write_markdown(
        outside / "secret.md",
        """
---
review_state: approved
---
# Secret

zzsymlinksecret should never be served through the wiki root.
""",
    )
    symlink_or_skip(outside / "secret.md", root / "leaked.md")

    service = LlmWikiService(root)
    graph = service.graph(include_drafts=True)
    results = service.search("zzsymlinksecret", include_drafts=True)

    assert service.manifest().page_count == 1
    assert results == []
    assert all(item["page_id"] != "leaked" for item in results)
    assert "zzsymlinksecret" not in str(results)
    assert service.read("leaked", include_drafts=True) == {"found": False}
    assert "page:leaked" not in {node["id"] for node in graph["nodes"]}


def test_symlinked_graph_sidecar_outside_root_is_not_loaded(tmp_path: Path) -> None:
    root = tmp_path / "wiki"
    outside = tmp_path / "outside"
    graph_dir = root / "graph"
    graph_dir.mkdir(parents=True)
    outside.mkdir()
    write_markdown(
        root / "index.md",
        """
---
review_state: approved
---
# Index

Index links to [[Topic]].
""",
    )
    write_markdown(
        root / "topic.md",
        """
---
review_state: approved
---
# Topic

Topic content.
""",
    )
    (outside / "graph.json").write_text(
        '{"edges":[{"from":"index","to":"topic","type":"supports","confidence":0.9}]}\n',
        encoding="utf-8",
    )
    symlink_or_skip(outside / "graph.json", graph_dir / "graph.json")

    graph = LlmWikiService(root).graph(include_drafts=True)

    assert all(edge["relation"] != "supports" for edge in graph["edges"])


def test_quartz_yaml_config_detection_uses_content_root() -> None:
    loaded = load_wiki(FIXTURES / "quartz-yaml-site")

    assert loaded.adapter == "quartz"
    assert loaded.metadata["source_root"] == "content"
    assert [page.path for page in loaded.pages] == ["index.md"]


def test_logseq_namespace_titles_are_decoded_and_readable() -> None:
    service = LlmWikiService(FIXTURES / "logseq-graph")

    namespace_page = service.read("pages/Project___Alpha")
    journal_page = service.read("journals/2026_07_01")

    assert namespace_page["title"] == "Project/Alpha"
    assert journal_page["title"] == "2026-07-01"


def test_logseq_adapter_ingests_raw_org_pages() -> None:
    service = LlmWikiService(FIXTURES / "logseq-graph")

    org_page = service.read("pages/Org-Page")
    graph = service.graph()
    edge_keys = {(edge["source"], edge["target"], edge["relation"]) for edge in graph["edges"]}

    assert org_page["title"] == "Org Page"
    assert org_page["path"] == "pages/Org-Page.org"
    assert "Logseq Org page links to [[Product]]" in org_page["text"]
    assert org_page["links"] == ["Product"]
    assert "org-fixture" in org_page["tags"]
    assert ("page:pages/Org-Page", "page:pages/Product", "links_to") in edge_keys


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def write_markdown(path: Path, content: str) -> None:
    path.write_text(content.strip() + "\n", encoding="utf-8")


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


def symlink_or_skip(target: Path, link: Path) -> None:
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symlinks are not available: {error}")
