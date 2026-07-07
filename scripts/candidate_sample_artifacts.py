from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CandidateSample:
    label: str
    catalog_implementation: str
    directory_name: str
    expected_adapter: str
    expected_implementation: str
    build: Callable[[Path, CandidateSample], str]
    expect_sidecar: bool = False


@dataclass(frozen=True)
class GeneratedCandidateSample:
    label: str
    catalog_implementation: str
    directory_name: str
    root: Path
    expected_adapter: str
    expected_implementation: str
    representative_page_id: str
    hidden_page_id: str
    sidecar_graph_path: str | None

    def manifest_entry(self, output_root: Path) -> dict[str, object]:
        return {
            "label": self.label,
            "catalog_implementation": self.catalog_implementation,
            "directory_name": self.directory_name,
            "path": self.root.relative_to(output_root).as_posix(),
            "expected_adapter": self.expected_adapter,
            "expected_implementation": self.expected_implementation,
            "representative_page_id": self.representative_page_id,
            "hidden_page_id": self.hidden_page_id,
            "sidecar_graph_path": self.sidecar_graph_path,
        }


CANDIDATE_SAMPLES = (
    CandidateSample(
        label="atomicstrata/llm-wiki-compiler compatible Markdown output",
        catalog_implementation="atomicstrata/llm-wiki-compiler",
        directory_name="atomicstrata-llm-wiki-compiler",
        expected_adapter="llmwiki-markdown",
        expected_implementation="llmwiki-markdown",
        build=lambda root, candidate: build_llmwiki_markdown(root, candidate),
        expect_sidecar=True,
    ),
    CandidateSample(
        label="nashsu/llm_wiki compatible Markdown output",
        catalog_implementation="nashsu/llm_wiki",
        directory_name="nashsu-llm-wiki",
        expected_adapter="llmwiki-markdown",
        expected_implementation="llmwiki-markdown",
        build=lambda root, candidate: build_llmwiki_markdown(root, candidate),
    ),
    CandidateSample(
        label="SamurAIGPT/llm-wiki-agent compatible Markdown output",
        catalog_implementation="SamurAIGPT/llm-wiki-agent",
        directory_name="samuraigpt-llm-wiki-agent",
        expected_adapter="llmwiki-markdown",
        expected_implementation="llmwiki-markdown",
        build=lambda root, candidate: build_llmwiki_markdown(root, candidate),
        expect_sidecar=True,
    ),
    CandidateSample(
        label="lucasastorian/llmwiki compatible Markdown output",
        catalog_implementation="lucasastorian/llmwiki",
        directory_name="lucasastorian-llmwiki",
        expected_adapter="llmwiki-markdown",
        expected_implementation="llmwiki-markdown",
        build=lambda root, candidate: build_llmwiki_markdown(root, candidate),
    ),
    CandidateSample(
        label="Pratiyush/llm-wiki compatible Markdown output",
        catalog_implementation="Pratiyush/llm-wiki",
        directory_name="pratiyush-llm-wiki",
        expected_adapter="llmwiki-markdown",
        expected_implementation="llmwiki-markdown",
        build=lambda root, candidate: build_llmwiki_markdown(root, candidate),
        expect_sidecar=True,
    ),
    CandidateSample(
        label="LangChain DeepAgents llm-wiki workspace layout",
        catalog_implementation="langchain-ai/deepagents examples/llm-wiki",
        directory_name="langchain-deepagents-llm-wiki",
        expected_adapter="llmwiki-markdown",
        expected_implementation="llmwiki-markdown",
        build=lambda root, candidate: build_deepagents_llm_wiki(root, candidate),
    ),
    CandidateSample(
        label="Obsidian vault",
        catalog_implementation="Obsidian vault",
        directory_name="obsidian-vault",
        expected_adapter="obsidian",
        expected_implementation="Obsidian vault",
        build=lambda root, candidate: build_obsidian_vault(root, candidate),
    ),
    CandidateSample(
        label="Logseq graph",
        catalog_implementation="logseq/logseq",
        directory_name="logseq-graph",
        expected_adapter="logseq",
        expected_implementation="logseq/logseq",
        build=lambda root, candidate: build_logseq_graph(root, candidate),
    ),
    CandidateSample(
        label="Foam workspace",
        catalog_implementation="foambubble/foam",
        directory_name="foam-workspace",
        expected_adapter="foam",
        expected_implementation="foambubble/foam",
        build=lambda root, candidate: build_foam_workspace(root, candidate),
    ),
    CandidateSample(
        label="Dendron workspace",
        catalog_implementation="dendronhq/dendron",
        directory_name="dendron-workspace",
        expected_adapter="dendron",
        expected_implementation="dendronhq/dendron",
        build=lambda root, candidate: build_dendron_workspace(root, candidate),
    ),
    CandidateSample(
        label="Quartz content",
        catalog_implementation="jackyzha0/quartz",
        directory_name="quartz-content",
        expected_adapter="quartz",
        expected_implementation="jackyzha0/quartz",
        build=lambda root, candidate: build_quartz_content(root, candidate),
    ),
)


def generate_candidate_samples(
    output_root: Path,
    *,
    samples: Iterable[CandidateSample] = CANDIDATE_SAMPLES,
    force: bool = False,
) -> list[GeneratedCandidateSample]:
    output_root.mkdir(parents=True, exist_ok=True)
    generated: list[GeneratedCandidateSample] = []
    for candidate in samples:
        root = output_root / candidate.directory_name
        if root.exists():
            if not force:
                raise FileExistsError(
                    f"candidate sample directory already exists: {root}. "
                    "Use --force to replace generated candidate sample directories."
                )
            remove_tree(root)
        generated.append(create_candidate_sample(root, candidate))
    write_candidate_samples_manifest(output_root, generated)
    return generated


def create_candidate_sample(root: Path, candidate: CandidateSample) -> GeneratedCandidateSample:
    representative_page_id = candidate.build(root, candidate)
    hidden_page_id = add_hidden_candidate_draft(root, candidate)
    if candidate.expect_sidecar:
        add_sidecar_edge_to_hidden_candidate_draft(root, representative_page_id, hidden_page_id)
    sidecar_path = root / "graph" / "graph.json"
    return GeneratedCandidateSample(
        label=candidate.label,
        catalog_implementation=candidate.catalog_implementation,
        directory_name=candidate.directory_name,
        root=root,
        expected_adapter=candidate.expected_adapter,
        expected_implementation=candidate.expected_implementation,
        representative_page_id=representative_page_id,
        hidden_page_id=hidden_page_id,
        sidecar_graph_path=(
            sidecar_path.relative_to(root).as_posix() if candidate.expect_sidecar else None
        ),
    )


def write_candidate_samples_manifest(
    output_root: Path, generated: list[GeneratedCandidateSample]
) -> None:
    write_json(
        output_root / "candidate-samples.json",
        {
            "schema": "llmwiki-serve-candidate-samples-v1",
            "samples": [sample.manifest_entry(output_root) for sample in generated],
        },
    )


def remove_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir() and not path.is_symlink():
            path.rmdir()
        else:
            path.unlink()
    root.rmdir()


def build_llmwiki_markdown(root: Path, candidate: CandidateSample) -> str:
    root.mkdir(parents=True)
    (root / "concepts").mkdir()
    slug = candidate.directory_name
    topic_id = f"concepts/{slug}-release"
    write_markdown(
        root / "index.md",
        frontmatter(
            wiki_title=f"{candidate.catalog_implementation} Local Sample",
            description="Local compatible output target sample for projection tests.",
            review_state="approved",
            tags=[slug, "candidate-sample"],
            source_refs=[f"{slug.upper()}-INDEX"],
        )
        + f"""
# {candidate.catalog_implementation} Local Sample

This local sample covers release readiness projection and links to [[{topic_id}]].

## Orientation

The index supplies context, tags, source references, and a representative link.
""",
    )
    write_markdown(
        root / "concepts" / f"{slug}-release.md",
        frontmatter(
            title=f"{candidate.catalog_implementation} Release Topic",
            review_state="approved",
            tags=[slug, "release-readiness"],
            source_refs=[f"{slug.upper()}-TOPIC"],
        )
        + """
# Release Readiness Topic

Release readiness projection checks page links, headings, tags, and source refs.
It links back to [[index]] for orientation.

## Evidence

The topic is intentionally small but answerable for projection tests.
""",
    )
    if candidate.expect_sidecar:
        (root / "graph").mkdir()
        write_json(
            root / "graph" / "graph.json",
            {
                "edges": [
                    {
                        "from": "index",
                        "to": topic_id,
                        "type": "supports",
                        "confidence": 0.91,
                    },
                    {
                        "source": topic_id,
                        "target": "GH-910",
                        "type": "tracks",
                        "confidence": 0.82,
                    },
                    {
                        "from": "index",
                        "to": topic_id,
                        "type": "links_to",
                        "confidence": 0.77,
                    },
                ]
            },
        )
    return topic_id


def build_deepagents_llm_wiki(root: Path, candidate: CandidateSample) -> str:
    root.mkdir(parents=True)
    wiki = root / "wiki"
    (root / "raw").mkdir()
    wiki.mkdir()
    (wiki / "query").mkdir()
    slug = candidate.directory_name
    topic_id = "process-runbook"
    (root / "AGENTS.md").write_text(
        """
# LLM Wiki Agent Rules

- Treat `raw/` as immutable source input.
- Maintain canonical pages under `wiki/`.
- Read `wiki/index.md` first for navigation.
- Append timeline entries through the runner-managed `log.md`.
""".lstrip(),
        encoding="utf-8",
    )
    (root / "raw" / "release-notes.md").write_text(
        """
# Release Notes Source

The release readiness process records source takeaways before the agent files
canonical wiki updates.
""".lstrip(),
        encoding="utf-8",
    )
    (root / "log.md").write_text(
        """
# Timeline

## [2026-07-01] ingest.apply | outcome=applied

- timestamp: 2026-07-01T00:00:00Z
- summary: Created release readiness process pages from raw source notes.
""".lstrip(),
        encoding="utf-8",
    )
    write_markdown(
        wiki / "index.md",
        frontmatter(
            wiki_title="DeepAgents LLM Wiki Local Sample",
            description=(
                "Context Hub style LLM Wiki workspace with raw, wiki, query, and log folders."
            ),
            review_state="approved",
            tags=[slug, "candidate-sample", "deepagents"],
            source_refs=["DEEPAGENTS-INDEX"],
        )
        + """
# DeepAgents LLM Wiki Local Sample

The index is the first navigation surface for DeepAgents query and lint flows.
It links to [[process-runbook]] and keeps query pages as routing hints.

## Catalog

- [[process-runbook]] captures the canonical release readiness process.
- [[query/release-readiness-question]] records a durable question result.
""",
    )
    write_markdown(
        wiki / "process-runbook.md",
        frontmatter(
            title="Release Readiness Process",
            review_state="approved",
            tags=[slug, "process", "release-readiness"],
            source_refs=["DEEPAGENTS-RAW-1"],
        )
        + """
# Release Readiness Process

Release readiness projection checks source takeaways, canonical wiki updates,
timeline logging, and follow-up lint work. It links back to [[index]] for
orientation and forward to [[query/release-readiness-question]] for a durable
query result.

## Evidence

The page represents DeepAgents `ingest.apply` output after reviewing raw source
material.
""",
    )
    write_markdown(
        wiki / "query" / "release-readiness-question.md",
        frontmatter(
            title="Release Readiness Question",
            review_state="approved",
            tags=[slug, "query-result"],
            source_refs=["DEEPAGENTS-QUERY-1"],
        )
        + """
# Release Readiness Question

This durable query page is a routing hint. Primary evidence remains the
canonical [[process-runbook]] page.
""",
    )
    return topic_id


def build_obsidian_vault(root: Path, candidate: CandidateSample) -> str:
    root.mkdir(parents=True)
    (root / ".obsidian").mkdir()
    (root / "wiki").mkdir()
    write_markdown(
        root / "index.md",
        frontmatter(
            wiki_title=f"{candidate.label} Local Sample",
            review_state="approved",
            tags=["obsidian", "candidate-sample"],
            source_refs=["OBSIDIAN-INDEX"],
        )
        + """
# Obsidian Vault Local Sample

Release readiness projection starts with [[Release Checklist]] and local tags.

## Vault Context

The vault marker selects the Obsidian adapter.
""",
    )
    write_markdown(
        root / "Release Checklist.md",
        frontmatter(
            title="Release Checklist",
            review_state="approved",
            tags=["obsidian", "release-readiness"],
            source_refs=["OBSIDIAN-RELEASE"],
        )
        + """
# Release Checklist

Release readiness projection verifies source refs and links back to [[index]].

## Approval

The checklist has enough content for context and search.
""",
    )
    write_markdown(
        root / "wiki" / "overview.md",
        frontmatter(
            title="Nested Obsidian Wiki Overview",
            review_state="approved",
            tags=["obsidian", "nested-wiki", "candidate-sample"],
            source_refs=["OBSIDIAN-NESTED-OVERVIEW"],
        )
        + """
# Nested Obsidian Wiki Overview

This nested wiki overview links to [[Release Checklist]] so Obsidian vaults keep
LLMWiki hub orientation without hiding regular vault notes.

## Nested Context

The vault root remains the served source root while this page acts as overview.
""",
    )
    return "Release Checklist"


def build_logseq_graph(root: Path, candidate: CandidateSample) -> str:
    (root / "logseq").mkdir(parents=True)
    (root / "pages").mkdir()
    (root / "journals").mkdir()
    (root / "logseq" / "config.edn").write_text("{:meta/version 1}\n", encoding="utf-8")
    write_markdown(
        root / "pages" / "Product.md",
        frontmatter(
            title=f"{candidate.label} Product",
            review_state="approved",
            tags=["logseq", "candidate-sample"],
            source_refs=["LOGSEQ-PRODUCT"],
        )
        + """
# Product

- Release readiness projection links to [[Workflow]] for graph coverage.

## Page Context

- tags:: #release-readiness
""",
    )
    write_markdown(
        root / "pages" / "Workflow.md",
        frontmatter(
            title="Workflow",
            review_state="approved",
            tags=["logseq", "release-readiness"],
            source_refs=["LOGSEQ-WORKFLOW"],
        )
        + """
# Workflow

- Workflow context links back to [[Product]] for local projection checks.

## Review

- The page remains small and searchable.
""",
    )
    return "pages/Workflow"


def build_foam_workspace(root: Path, candidate: CandidateSample) -> str:
    root.mkdir(parents=True)
    (root / ".foam").mkdir()
    write_markdown(
        root / "index.md",
        frontmatter(
            wiki_title=f"{candidate.label} Local Sample",
            review_state="approved",
            tags=["foam", "candidate-sample"],
            source_refs=["FOAM-INDEX"],
        )
        + """
# Foam Workspace Local Sample

Release readiness projection starts with [[Team Notes]].

## Workspace Context

The Foam marker selects the Foam adapter.
""",
    )
    write_markdown(
        root / "Team Notes.md",
        frontmatter(
            title="Team Notes",
            review_state="approved",
            tags=["foam", "release-readiness"],
            source_refs=["FOAM-TEAM"],
        )
        + """
# Team Notes

Release readiness projection links back to [[index]] and cites team evidence.

## Decisions

The page includes tags, headings, and source refs.
""",
    )
    return "Team Notes"


def build_dendron_workspace(root: Path, candidate: CandidateSample) -> str:
    vault = root / "vault"
    vault.mkdir(parents=True)
    (root / "dendron.yml").write_text("version: 5\nvaults:\n  - fsPath: vault\n", encoding="utf-8")
    write_markdown(
        vault / "root.md",
        frontmatter(
            title=f"{candidate.label} Root",
            review_state="approved",
            tags=["dendron", "candidate-sample"],
            source_refs=["DENDRON-ROOT"],
        )
        + """
# Dendron Root

Release readiness projection starts with [[Review Note|process.review]].

## Workspace Context

The Dendron config selects the vault source root.
""",
    )
    write_markdown(
        vault / "process.review.md",
        frontmatter(
            title="Process Review",
            review_state="approved",
            tags=["dendron", "release-readiness"],
            source_refs=["DENDRON-REVIEW"],
        )
        + """
# Process Review

Release readiness projection links back to [[root]] and exercises hierarchy.

## Review Evidence

The dotted filename creates a Dendron hierarchy edge.
""",
    )
    return "process.review"


def build_quartz_content(root: Path, candidate: CandidateSample) -> str:
    content = root / "content"
    content.mkdir(parents=True)
    (root / "quartz.config.ts").write_text("export default {}\n", encoding="utf-8")
    write_markdown(
        content / "index.md",
        frontmatter(
            wiki_title=f"{candidate.label} Local Sample",
            review_state="approved",
            tags=["quartz", "candidate-sample"],
            source_refs=["QUARTZ-INDEX"],
        )
        + """
# Quartz Content Local Sample

Release readiness projection starts with [[topic]].

## Site Context

The Quartz config selects the content source root.
""",
    )
    write_markdown(
        content / "topic.md",
        frontmatter(
            title="Quartz Topic",
            review_state="approved",
            tags=["quartz", "release-readiness"],
            source_refs=["QUARTZ-TOPIC"],
        )
        + """
# Quartz Topic

Release readiness projection links back to [[index]] and remains searchable.

## Publishing Evidence

The content folder is projected as the served wiki source.
""",
    )
    return "topic"


def add_hidden_candidate_draft(root: Path, candidate: CandidateSample) -> str:
    if candidate.expected_adapter == "logseq":
        source_root = root / "pages"
        page_id = "pages/hidden-draft"
    elif candidate.catalog_implementation == "langchain-ai/deepagents examples/llm-wiki":
        source_root = root / "wiki"
        page_id = "hidden-draft"
    elif candidate.expected_adapter == "dendron":
        source_root = root / "vault"
        page_id = "hidden-draft"
    elif candidate.expected_adapter == "quartz":
        source_root = root / "content"
        page_id = "hidden-draft"
    else:
        source_root = root
        page_id = "hidden-draft"
    write_markdown(
        source_root / "hidden-draft.md",
        frontmatter(
            title="Hidden Candidate Draft",
            review_state="approved",
            tags=["candidate-hidden"],
            source_refs=["CANDIDATE-HIDDEN"],
            extra={"draft": "true"},
        )
        + """
# Hidden Candidate Draft

zzcandidateembargo should only appear when include_drafts is enabled.
""",
    )
    return page_id


def add_sidecar_edge_to_hidden_candidate_draft(
    root: Path, representative_page_id: str, hidden_page_id: str
) -> None:
    graph_path = root / "graph" / "graph.json"
    graph_data = json.loads(graph_path.read_text(encoding="utf-8"))
    graph_data["edges"].append(
        {
            "from": representative_page_id,
            "to": hidden_page_id,
            "type": "draft_neighbor",
            "confidence": 0.74,
        }
    )
    write_json(graph_path, graph_data)


def candidate_source_root(root: Path, loaded: Any) -> Path:
    source_root = loaded.metadata.get("source_root", ".")
    return root if source_root == "." else root / source_root


def candidate_hot_page_path(root: Path, candidate: CandidateSample, loaded: Any) -> Path | None:
    if candidate.expected_adapter == "logseq":
        return None
    return candidate_source_root(root, loaded) / "hot.md"


def candidate_sidecar_graph_path(root: Path, loaded: Any) -> Path:
    return candidate_source_root(root, loaded) / "graph" / "graph.json"


def candidate_sync_page_location(
    root: Path, candidate: CandidateSample, loaded: Any, slug: str
) -> tuple[Path, str]:
    if candidate.expected_adapter == "logseq":
        return root / "pages" / f"{slug}.md", f"pages/{slug}"
    return candidate_source_root(root, loaded) / f"{slug}.md", slug


def sidecar_endpoint(loaded: Any, page: Any) -> str:
    source_root = loaded.metadata.get("source_root", ".")
    if source_root and source_root != ".":
        return f"{source_root.rstrip('/')}/{page.path}"
    return page.path


def frontmatter(
    *,
    review_state: str,
    tags: list[str],
    source_refs: list[str],
    title: str | None = None,
    wiki_title: str | None = None,
    description: str | None = None,
    extra: dict[str, object] | None = None,
) -> str:
    values: dict[str, object] = {
        "review_state": review_state,
        "tags": tags,
        "source_refs": source_refs,
    }
    if title is not None:
        values["title"] = title
    if wiki_title is not None:
        values["wiki_title"] = wiki_title
    if description is not None:
        values["description"] = description
    if extra:
        values.update(extra)
    return "---\n" + "\n".join(yaml_line(key, value) for key, value in values.items()) + "\n---\n"


def yaml_line(key: str, value: object) -> str:
    if isinstance(value, list):
        return f"{key}: [{', '.join(json.dumps(item) for item in value)}]"
    return f"{key}: {json.dumps(value)}"


def write_markdown(path: Path, content: str) -> None:
    path.write_text(content.strip() + "\n", encoding="utf-8")


def write_json(path: Path, content: object) -> None:
    path.write_text(json.dumps(content, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
