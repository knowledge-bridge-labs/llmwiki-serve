from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

import yaml

from .models import GraphEdge, GraphNode, PageRole, ReviewState, WikiIndex, WikiPage

WikilinkAliasMode = Literal["target-first", "target-last"]

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
MD_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+?\.(?:md|org))(?:#[^)]+)?\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
INLINE_TAG_RE = re.compile(r"(?<![\w/])#([A-Za-z0-9가-힣][A-Za-z0-9가-힣_./-]*)")
LOGSEQ_TAGS_RE = re.compile(r"(?im)^\s*tags::\s*(.+?)\s*$")


def build_index(root: Path) -> WikiIndex:
    """Deprecated compatibility wrapper for the adapter/projection orchestration."""
    import warnings

    warnings.warn(
        "llmwiki_serve.parser.build_index() is deprecated; use "
        "llmwiki_serve.adapters.load_wiki() with llmwiki_serve.projection.project_wiki(), "
        "or LlmWikiService.index().",
        DeprecationWarning,
        stacklevel=2,
    )

    from .adapters import load_wiki
    from .projection import project_wiki

    return project_wiki(load_wiki(root))


def include_page(path: Path) -> bool:
    parts = set(path.parts)
    return not bool(parts & {".git", "node_modules", ".venv", "__pycache__"})


def parse_page(
    root: Path,
    path: Path,
    *,
    wikilink_alias: WikilinkAliasMode = "target-first",
    decode_namespace_title: bool = False,
    hub_roots: Iterable[Path] = (),
) -> WikiPage:
    raw = path.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(raw)
    rel = path.relative_to(root).as_posix()
    role = page_role(root, path, hub_roots=hub_roots)
    title = clean_text(
        str(
            frontmatter.get("title")
            or first_heading(body)
            or title_from_path(path, decode_namespace_title=decode_namespace_title)
        )
    )
    page_id = clean_id(str(frontmatter.get("id") or frontmatter.get("object_id") or rel))
    source_refs = string_list(frontmatter.get("source_refs") or frontmatter.get("sources"))
    tags = unique([*string_list(frontmatter.get("tags")), *extract_inline_tags(body)])
    links = extract_links(body, wikilink_alias=wikilink_alias)
    headings = [clean_text(match.group(2)) for match in HEADING_RE.finditer(body)]
    return WikiPage(
        id=page_id,
        title=title,
        path=rel,
        role=role,
        text=body.strip(),
        summary=summary_text(body),
        frontmatter=frontmatter,
        review_state=normalize_review_state(frontmatter),
        status=clean_text(str(frontmatter.get("status") or "")),
        source_refs=source_refs,
        tags=tags,
        links=links,
        headings=headings[:40],
        updated_at=clean_text(
            str(frontmatter.get("updated_at") or frontmatter.get("last_updated") or "")
        ),
    )


def split_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    if not raw.startswith("---\n"):
        return {}, raw
    end = raw.find("\n---", 4)
    if end < 0:
        return {}, raw
    block = raw[4:end]
    body = raw[end + 4 :]
    try:
        data = yaml.safe_load(block) or {}
    except yaml.YAMLError:
        return {}, raw
    if not isinstance(data, dict):
        data = {}
    return data, body


def page_role(root: Path, path: Path, *, hub_roots: Iterable[Path] = ()) -> PageRole:
    rel = path.relative_to(root)
    role = hub_role(path.name)
    if role is None:
        return "topic"
    if rel.parent == Path("."):
        return role
    for hub_root in hub_roots:
        try:
            hub_rel = path.relative_to(hub_root)
        except ValueError:
            continue
        if hub_rel.parent == Path("."):
            return role
    return "topic"


def hub_role(name: str) -> PageRole | None:
    name = name.lower()
    if name == "hot.md":
        return "hot"
    if name == "index.md":
        return "index"
    if name == "overview.md":
        return "overview"
    return None


def first_heading(text: str) -> str:
    match = HEADING_RE.search(text)
    return clean_text(match.group(2)) if match else ""


def title_from_path(path: Path, *, decode_namespace_title: bool) -> str:
    stem = path.stem.replace("___", "/") if decode_namespace_title else path.stem
    return stem.replace("-", " ")


def summary_text(text: str, limit: int = 360) -> str:
    cleaned = clean_text(re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE))
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "..."


def extract_links(text: str, *, wikilink_alias: WikilinkAliasMode = "target-first") -> list[str]:
    values = [
        wikilink_target(value, wikilink_alias=wikilink_alias) for value in WIKILINK_RE.findall(text)
    ]
    values.extend(markdown_link_target(value) for value in MD_LINK_RE.findall(text))
    return unique([value for value in values if value])


def wikilink_target(value: str, *, wikilink_alias: WikilinkAliasMode) -> str:
    parts = value.split("|", maxsplit=1)
    target = parts[-1] if wikilink_alias == "target-last" and len(parts) == 2 else parts[0]
    return markdown_link_target(target)


def markdown_link_target(value: str) -> str:
    target = value.split("#", maxsplit=1)[0].strip().replace("\\", "/")
    if target.endswith(".md") or target.endswith(".org"):
        target = target.rsplit(".", maxsplit=1)[0]
    return target.removeprefix("./")


def extract_inline_tags(text: str) -> list[str]:
    tags = [match.group(1).strip(".,;:!?") for match in INLINE_TAG_RE.finditer(text)]
    for match in LOGSEQ_TAGS_RE.finditer(text):
        raw = match.group(1).replace(",", " ")
        tags.extend(part.strip("#") for part in raw.split())
    return unique([clean_text(tag.strip("#")) for tag in tags if clean_text(tag.strip("#"))])


def string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        return []
    return unique([clean_text(str(item)) for item in values if clean_text(str(item))])


def normalize_review_state(frontmatter: dict[str, Any]) -> ReviewState:
    value = clean_text(
        str(frontmatter.get("review_state") or frontmatter.get("state") or "")
    ).lower()
    states: dict[str, ReviewState] = {
        "approved": "approved",
        "reviewed": "reviewed",
        "verified": "verified",
        "draft": "draft",
        "proposed": "proposed",
        "needs_review": "needs_review",
    }
    return states.get(value, "unknown")


def wiki_metadata(root: Path, pages: list[WikiPage]) -> dict[str, str]:
    index = next((page for page in pages if page.role == "index"), None) or next(
        (page for page in pages if page.role == "overview"), None
    )
    return {
        "title": str(
            (index.frontmatter if index else {}).get("wiki_title")
            or (index.title if index else root.name)
        ),
        "description": str((index.frontmatter if index else {}).get("description") or ""),
    }


def page_lookup(pages: list[WikiPage]) -> dict[str, WikiPage]:
    lookup: dict[str, WikiPage] = {}
    for page in pages:
        for key in {page.id, page.title, Path(page.path).stem, page.path}:
            lookup[normalize_key(key)] = page
    return lookup


def clean_id(value: str) -> str:
    value = value.strip().replace("\\", "/")
    if value.endswith(".md") or value.endswith(".org"):
        return value.rsplit(".", maxsplit=1)[0]
    return value


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]+", "", value.lower())


def slug(value: str) -> str:
    slugged = re.sub(r"[^A-Za-z0-9가-힣._-]+", "-", value.strip()).strip("-")
    return slugged or "item"


def clean_text(value: str) -> str:
    return " ".join(value.split())


def unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.lower()
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def dedupe_nodes(nodes: list[GraphNode]) -> list[GraphNode]:
    seen: set[str] = set()
    result: list[GraphNode] = []
    for node in nodes:
        if node.id in seen:
            continue
        seen.add(node.id)
        result.append(node)
    return result


def dedupe_edges(edges: list[GraphEdge]) -> list[GraphEdge]:
    seen: set[tuple[str, str, str]] = set()
    result: list[GraphEdge] = []
    for edge in edges:
        key = (edge.source, edge.target, edge.relation)
        if key in seen:
            continue
        seen.add(key)
        result.append(edge)
    return result
