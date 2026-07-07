from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

from .models import WikiPage
from .parser import WikilinkAliasMode, parse_page

LLMWIKI_TYPED_DIRS = {
    "concepts",
    "entities",
    "sources",
    "queries",
    "comparisons",
    "synthesis",
    "syntheses",
    "projects",
    "categories",
    "questions",
}
LLMWIKI_HUB_FILES = {"index.md", "overview.md", "hot.md", "critical_facts.md"}
IGNORED_ADAPTER_PARTS = {
    ".git",
    ".obsidian",
    ".foam",
    "node_modules",
    ".venv",
    "__pycache__",
    "dist",
    "build",
}
IGNORED_HUB_ROOT_NAMES = {*IGNORED_ADAPTER_PARTS, ".vscode"}
WIKI_ROOT_MISSING_CODE = "wiki_root_missing"
WIKI_ROOT_MISSING_SAFE_MESSAGE = "Configured wiki root does not exist or is not a directory."
WIKI_ROOT_UNSUPPORTED_CODE = "wiki_root_unsupported"
WIKI_ROOT_UNSUPPORTED_SAFE_MESSAGE = "No supported wiki files were found under the configured root."


class WikiRootError(FileNotFoundError):
    code: str
    safe_message: str
    status_code: int

    def __init__(
        self,
        message: str,
        *,
        code: str,
        safe_message: str,
        status_code: int,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = safe_message
        self.status_code = status_code


@dataclass(frozen=True)
class SidecarGraphEdgeFact:
    data: dict[str, Any]
    path: str
    source: str = "graph.json"
    confidence: int | float | None = None


@dataclass(frozen=True)
class LoadedWiki:
    root: Path
    pages: list[WikiPage]
    title: str
    description: str = ""
    adapter: str = "generic-markdown"
    implementation: str = "generic-markdown"
    metadata: dict[str, str] = field(default_factory=dict)
    sidecar_graph_edges: list[SidecarGraphEdgeFact] = field(default_factory=list)


@dataclass(frozen=True)
class AdapterProfile:
    implementation: str
    adapter: str
    maturity: str
    notes: str


class WikiAdapter(Protocol):
    name: str
    implementation: str

    def detect(self, root: Path) -> bool: ...

    def load(self, root: Path) -> LoadedWiki: ...


SUPPORTED_IMPLEMENTATIONS: tuple[AdapterProfile, ...] = (
    AdapterProfile(
        "atomicstrata/llm-wiki-compiler",
        "llmwiki-markdown",
        "compatible-output",
        "Compatible with Markdown outputs that match the native LLMWiki folder contract.",
    ),
    AdapterProfile(
        "nashsu/llm_wiki",
        "llmwiki-markdown",
        "compatible-output",
        "Compatible with generated knowledge bases when exported or stored as Markdown files.",
    ),
    AdapterProfile(
        "SamurAIGPT/llm-wiki-agent",
        "llmwiki-markdown",
        "compatible-output",
        "Compatible with persistent agent-maintained outputs that use the Markdown wiki shape.",
    ),
    AdapterProfile(
        "lucasastorian/llmwiki",
        "llmwiki-markdown",
        "compatible-output",
        "Compatible with generated LLMWiki-style Markdown folders on disk.",
    ),
    AdapterProfile(
        "Pratiyush/llm-wiki",
        "llmwiki-markdown",
        "compatible-output",
        "Compatible with agent-session-derived knowledge bases when exported as Markdown files.",
    ),
    AdapterProfile(
        "langchain-ai/deepagents examples/llm-wiki",
        "llmwiki-markdown",
        "compatible-output",
        (
            "Reads the generated raw/wiki/log workspace layout when the nested wiki/ "
            "folder contains Markdown pages."
        ),
    ),
    AdapterProfile(
        "Obsidian vault",
        "obsidian",
        "format",
        "Reads Markdown, YAML frontmatter, wikilinks, tags, and .obsidian marker directory.",
    ),
    AdapterProfile(
        "logseq/logseq",
        "logseq",
        "format",
        "Reads pages/ and journals/ Markdown or Org files plus page references.",
    ),
    AdapterProfile(
        "foambubble/foam",
        "foam",
        "format",
        "Reads VS Code Markdown workspaces using wikilinks and optional .foam markers.",
    ),
    AdapterProfile(
        "dendronhq/dendron",
        "dendron",
        "format",
        "Reads Dendron Markdown vaults and dotted hierarchy file names.",
    ),
    AdapterProfile(
        "jackyzha0/quartz",
        "quartz",
        "format",
        "Reads Quartz content/ Markdown folders and generated-site source vaults.",
    ),
)


class MarkdownWikiAdapter:
    name = "generic-markdown"
    implementation = "generic-markdown"

    def detect(self, root: Path) -> bool:
        return any(include_adapter_file(path, root=root) for path in root.rglob("*.md"))

    def load(self, root: Path) -> LoadedWiki:
        pages = parse_markdown_pages(root, hub_roots=llmwiki_hub_source_roots(root))
        title, description = markdown_metadata(root, pages)
        return LoadedWiki(
            root=root,
            pages=pages,
            title=title,
            description=description,
            adapter=self.name,
            implementation=self.implementation,
            metadata=source_root_metadata(root, root),
            sidecar_graph_edges=load_sidecar_graph_edges(root, root),
        )


class LlmWikiMarkdownAdapter(MarkdownWikiAdapter):
    name = "llmwiki-markdown"
    implementation = "llmwiki-markdown"

    def detect(self, root: Path) -> bool:
        return llmwiki_source_root(root) is not None

    def load(self, root: Path) -> LoadedWiki:
        source_root = llmwiki_source_root(root) or root
        pages = parse_markdown_pages(source_root)
        title, description = markdown_metadata(source_root, pages)
        return LoadedWiki(
            root=root,
            pages=pages,
            title=title,
            description=description,
            adapter=self.name,
            implementation=self.implementation,
            metadata=source_root_metadata(root, source_root),
            sidecar_graph_edges=load_sidecar_graph_edges(root, source_root),
        )


class ObsidianAdapter(MarkdownWikiAdapter):
    name = "obsidian"
    implementation = "Obsidian vault"

    def detect(self, root: Path) -> bool:
        return include_adapter_dir(root / ".obsidian", root=root) and super().detect(root)


class FoamAdapter(MarkdownWikiAdapter):
    name = "foam"
    implementation = "foambubble/foam"

    def detect(self, root: Path) -> bool:
        return include_adapter_path(root / ".foam", root=root) or has_vscode_extension_hint(
            root, "foam"
        )


class DendronAdapter(MarkdownWikiAdapter):
    name = "dendron"
    implementation = "dendronhq/dendron"

    def detect(self, root: Path) -> bool:
        return include_adapter_file(root / "dendron.yml", root=root)

    def load(self, root: Path) -> LoadedWiki:
        source_roots, configured_vault_count = dendron_source_roots(root)
        use_workspace_paths = configured_vault_count > 1
        source_root = (
            source_roots[0] if len(source_roots) == 1 and not use_workspace_paths else root
        )
        path_root = root if use_workspace_paths else source_root
        scan_roots = source_roots or ([] if configured_vault_count else [root])
        pages = parse_markdown_pages_for_roots(
            scan_roots,
            path_root=path_root,
            wikilink_alias="target-last",
        )
        title, description = markdown_metadata(path_root, pages)
        return LoadedWiki(
            root=root,
            pages=pages,
            title=title,
            description=description,
            adapter=self.name,
            implementation=self.implementation,
            metadata=dendron_source_metadata(
                root,
                source_roots,
                source_root,
                use_workspace_paths=use_workspace_paths,
            ),
            sidecar_graph_edges=load_sidecar_graph_edges_for_roots(root, scan_roots),
        )


class QuartzAdapter(MarkdownWikiAdapter):
    name = "quartz"
    implementation = "jackyzha0/quartz"

    def detect(self, root: Path) -> bool:
        return any(
            include_adapter_file(root / filename, root=root)
            for filename in (
                "quartz.config.ts",
                "quartz.config.js",
                "quartz.config.yaml",
                "quartz.config.yml",
            )
        )

    def load(self, root: Path) -> LoadedWiki:
        content = root / "content"
        source_root = content if include_adapter_dir(content, root=root) else root
        loaded = super().load(source_root)
        return LoadedWiki(
            root=root,
            pages=loaded.pages,
            title=loaded.title,
            description=loaded.description,
            adapter=self.name,
            implementation=self.implementation,
            metadata={
                "content_root": str(source_root.relative_to(root)) if source_root != root else ".",
                **source_root_metadata(root, source_root),
            },
            sidecar_graph_edges=load_sidecar_graph_edges(root, source_root),
        )


class LogseqAdapter(MarkdownWikiAdapter):
    name = "logseq"
    implementation = "logseq/logseq"

    def detect(self, root: Path) -> bool:
        return include_adapter_file(root / "logseq" / "config.edn", root=root) or (
            include_adapter_dir(root / "pages", root=root)
            and include_adapter_dir(root / "journals", root=root)
        )

    def load(self, root: Path) -> LoadedWiki:
        scan_roots = [
            path
            for path in (root / "pages", root / "journals")
            if include_adapter_dir(path, root=root)
        ]
        pages: list[WikiPage] = []
        for scan_root in scan_roots:
            pages.extend(
                parse_page(root, path, decode_namespace_title=True)
                for path in sorted(scan_root.rglob("*"))
                if path.suffix.lower() in {".md", ".org"} and include_adapter_file(path, root=root)
            )
        title, description = markdown_metadata(root, pages)
        return LoadedWiki(
            root=root,
            pages=pages,
            title=title,
            description=description,
            adapter=self.name,
            implementation=self.implementation,
            metadata=source_root_metadata(root, root),
            sidecar_graph_edges=load_sidecar_graph_edges(root, root),
        )


ADAPTERS: tuple[WikiAdapter, ...] = (
    ObsidianAdapter(),
    LogseqAdapter(),
    DendronAdapter(),
    FoamAdapter(),
    QuartzAdapter(),
    LlmWikiMarkdownAdapter(),
    MarkdownWikiAdapter(),
)


def load_wiki(root: Path | str) -> LoadedWiki:
    resolved = Path(root).expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise missing_wiki_root_error(resolved)
    for adapter in ADAPTERS:
        if adapter.detect(resolved):
            loaded = adapter.load(resolved)
            if not loaded.pages:
                raise unsupported_wiki_root_error(resolved)
            return loaded
    raise unsupported_wiki_root_error(resolved)


def missing_wiki_root_error(root: Path) -> WikiRootError:
    return WikiRootError(
        f"LLMWiki root does not exist or is not a directory: {root}",
        code=WIKI_ROOT_MISSING_CODE,
        safe_message=WIKI_ROOT_MISSING_SAFE_MESSAGE,
        status_code=404,
    )


def unsupported_wiki_root_error(root: Path) -> WikiRootError:
    return WikiRootError(
        f"No supported wiki files were found under: {root}",
        code=WIKI_ROOT_UNSUPPORTED_CODE,
        safe_message=WIKI_ROOT_UNSUPPORTED_SAFE_MESSAGE,
        status_code=422,
    )


def parse_markdown_pages(
    root: Path,
    *,
    wikilink_alias: WikilinkAliasMode = "target-first",
    decode_namespace_title: bool = False,
    path_root: Path | None = None,
    hub_roots: list[Path] | None = None,
) -> list[WikiPage]:
    page_root = path_root or root
    role_hub_roots = hub_roots or []
    return [
        parse_page(
            page_root,
            path,
            wikilink_alias=wikilink_alias,
            decode_namespace_title=decode_namespace_title,
            hub_roots=role_hub_roots,
        )
        for path in sorted(root.rglob("*.md"))
        if include_adapter_file(path, root=page_root)
    ]


def parse_markdown_pages_for_roots(
    roots: list[Path],
    *,
    path_root: Path,
    wikilink_alias: WikilinkAliasMode = "target-first",
    decode_namespace_title: bool = False,
    hub_roots: list[Path] | None = None,
) -> list[WikiPage]:
    pages: list[WikiPage] = []
    for root in roots:
        pages.extend(
            parse_markdown_pages(
                root,
                path_root=path_root,
                wikilink_alias=wikilink_alias,
                decode_namespace_title=decode_namespace_title,
                hub_roots=hub_roots,
            )
        )
    return pages


def include_adapter_path(path: Path, *, root: Path) -> bool:
    if not path.exists() or path.is_symlink():
        return False
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def include_adapter_dir(path: Path, *, root: Path) -> bool:
    return include_adapter_path(path, root=root) and path.is_dir()


def include_adapter_file(path: Path, *, root: Path) -> bool:
    if not include_adapter_path(path, root=root) or not path.is_file():
        return False
    relative_parts = path.relative_to(root).parts
    if set(relative_parts) & IGNORED_ADAPTER_PARTS:
        return False
    return not (".vscode" in relative_parts and path.suffix.lower() in {".md", ".org"})


def markdown_metadata(root: Path, pages: list[WikiPage]) -> tuple[str, str]:
    index = next((page for page in pages if page.role == "index"), None) or next(
        (page for page in pages if page.role == "overview"), None
    )
    title = str(
        (index.frontmatter if index else {}).get("wiki_title")
        or (index.title if index else root.name)
    )
    description = str((index.frontmatter if index else {}).get("description") or "")
    return title, description


def llmwiki_source_root(root: Path) -> Path | None:
    nested = root / "wiki"
    if is_llmwiki_markdown_root(nested, nested=True):
        return nested
    if is_llmwiki_markdown_root(root, nested=False):
        return root
    return None


def is_llmwiki_markdown_root(root: Path, *, nested: bool) -> bool:
    if not root.is_dir() or root.is_symlink():
        return False
    marker, markdown_names, has_typed_pages = llmwiki_root_signals(root)
    if marker:
        return True
    has_hub = bool(markdown_names & LLMWIKI_HUB_FILES)

    if nested:
        return has_hub or has_typed_pages
    return ("hot.md" in markdown_names and bool({"index.md", "overview.md"} & markdown_names)) or (
        has_hub and has_typed_pages
    )


def llmwiki_hub_source_roots(root: Path) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        return []
    roots: list[Path] = []
    preferred = root / "wiki"
    if is_llmwiki_markdown_root(preferred, nested=True):
        roots.append(preferred)
    for child in sorted(root.iterdir()):
        if child == preferred or child.name in IGNORED_HUB_ROOT_NAMES:
            continue
        if not include_adapter_dir(child, root=root):
            continue
        if is_probable_nested_llmwiki_source_root(child):
            roots.append(child)
    return roots


def is_probable_nested_llmwiki_source_root(root: Path) -> bool:
    if not root.is_dir() or root.is_symlink():
        return False
    marker, markdown_names, has_typed_pages = llmwiki_root_signals(root)
    if marker or has_typed_pages:
        return True
    return len(markdown_names & LLMWIKI_HUB_FILES) >= 2


def llmwiki_root_signals(root: Path) -> tuple[bool, set[str], bool]:
    marker = include_adapter_file(root / ".wiki-compiler.json", root=root)
    markdown_names = {
        path.name.casefold()
        for path in root.iterdir()
        if path.suffix.lower() == ".md" and include_adapter_file(path, root=root)
    }
    typed_dirs = [
        root / dirname
        for dirname in LLMWIKI_TYPED_DIRS
        if include_adapter_dir(root / dirname, root=root)
    ]
    has_typed_pages = any(
        any(include_adapter_file(path, root=root) for path in directory.rglob("*.md"))
        for directory in typed_dirs
    )
    return marker, markdown_names, has_typed_pages


def dendron_source_roots(root: Path) -> tuple[list[Path], int]:
    config = root / "dendron.yml"
    if not include_adapter_file(config, root=root):
        return [], 0
    try:
        data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return [], 0
    if not isinstance(data, dict):
        return [], 0
    roots: list[Path] = []
    seen: set[Path] = set()
    for vault in dendron_vault_entries(data):
        source_root = dendron_vault_path(root, vault)
        if source_root is None or source_root in seen:
            continue
        seen.add(source_root)
        if source_root.is_dir():
            roots.append(source_root)
    return roots, len(seen)


def dendron_vault_entries(data: dict[str, Any]) -> list[Any]:
    entries: list[Any] = []
    workspace = data.get("workspace")
    if isinstance(workspace, dict) and isinstance(workspace.get("vaults"), list):
        entries.extend(workspace["vaults"])
    if isinstance(data.get("vaults"), list):
        entries.extend(data["vaults"])
    return entries


def dendron_vault_path(root: Path, vault: Any) -> Path | None:
    if not isinstance(vault, dict):
        return None
    fs_path = vault.get("fsPath")
    if not isinstance(fs_path, str) or not fs_path.strip():
        return None
    source_root = (root / fs_path.replace("\\", "/")).resolve()
    try:
        source_root.relative_to(root)
    except ValueError:
        return None
    return source_root


def dendron_source_metadata(
    root: Path, source_roots: list[Path], source_root: Path, *, use_workspace_paths: bool
) -> dict[str, str]:
    metadata = source_root_metadata(root, source_root)
    if use_workspace_paths:
        metadata["vault_roots"] = ",".join(
            source.relative_to(root).as_posix() for source in source_roots
        )
    return metadata


def source_root_metadata(root: Path, source_root: Path) -> dict[str, str]:
    source_value = source_root.relative_to(root).as_posix() if source_root != root else "."
    return {"source_root": source_value}


def load_sidecar_graph_edges(root: Path, source_root: Path) -> list[SidecarGraphEdgeFact]:
    facts: list[SidecarGraphEdgeFact] = []
    for graph_path in graph_json_paths(root, source_root):
        try:
            data = json.loads(graph_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        path = graph_path.relative_to(root).as_posix()
        facts.extend(
            SidecarGraphEdgeFact(
                data=edge,
                path=path,
                confidence=graph_edge_confidence(edge),
            )
            for edge in graph_edges(data)
        )
    return facts


def load_sidecar_graph_edges_for_roots(
    root: Path, source_roots: list[Path]
) -> list[SidecarGraphEdgeFact]:
    facts: list[SidecarGraphEdgeFact] = []
    seen: set[Path] = set()
    for source_root in source_roots:
        for graph_path in graph_json_paths(root, source_root):
            resolved = graph_path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                data = json.loads(graph_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            path = graph_path.relative_to(root).as_posix()
            facts.extend(
                SidecarGraphEdgeFact(
                    data=edge,
                    path=path,
                    confidence=graph_edge_confidence(edge),
                )
                for edge in graph_edges(data)
            )
    return facts


def graph_json_paths(root: Path, source_root: Path) -> list[Path]:
    candidates = [
        root / "graph" / "graph.json",
        source_root / "graph" / "graph.json",
    ]
    seen: set[Path] = set()
    result: list[Path] = []
    for path in candidates:
        if not include_adapter_file(path, root=root):
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(path)
    return result


def graph_edges(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        candidates = data
    elif isinstance(data, dict) and isinstance(data.get("edges"), list):
        candidates = data["edges"]
    else:
        return []
    return [edge for edge in candidates if isinstance(edge, dict)]


def graph_edge_confidence(edge: dict[str, Any]) -> int | float | None:
    confidence = edge.get("confidence")
    if isinstance(confidence, bool):
        return None
    if isinstance(confidence, int | float):
        return confidence
    return None


def has_vscode_extension_hint(root: Path, needle: str) -> bool:
    extensions = root / ".vscode" / "extensions.json"
    if not include_adapter_file(extensions, root=root):
        return False
    try:
        data = json.loads(extensions.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    values = data.get("recommendations", []) if isinstance(data, dict) else []
    return any(needle.lower() in str(value).lower() for value in values)
