from __future__ import annotations

import re
from typing import Any

from .adapters import LoadedWiki
from .models import GraphEdge, GraphNode, WikiIndex, WikiPage


def project_wiki(loaded: LoadedWiki) -> WikiIndex:
    adapter_metadata = dict(loaded.metadata)
    page_by_key = page_lookup(loaded.pages, source_root=adapter_metadata.get("source_root", "."))
    nodes = [
        GraphNode(
            id=f"page:{page.id}",
            label=page.title,
            kind=page.role,
            path=page.path,
            metadata={
                "adapter": loaded.adapter,
                "implementation": loaded.implementation,
                **adapter_metadata,
                "source_refs": page.source_refs,
                "review_state": page.review_state,
            },
        )
        for page in loaded.pages
    ]
    edges: list[GraphEdge] = []
    for page in loaded.pages:
        for heading in page.headings:
            heading_id = slug(f"{page.id}:{heading}")
            nodes.append(
                GraphNode(id=f"heading:{heading_id}", label=heading, kind="heading", path=page.path)
            )
            edges.append(
                GraphEdge(
                    source=f"page:{page.id}", target=f"heading:{heading_id}", relation="contains"
                )
            )
        for ref in page.source_refs:
            source_id = f"source:{slug(ref)}"
            nodes.append(GraphNode(id=source_id, label=ref, kind="source_ref", path=page.path))
            edges.append(GraphEdge(source=f"page:{page.id}", target=source_id, relation="cites"))
        for tag in page.tags:
            tag_label = tag.strip("#")
            tag_id = f"tag:{slug(tag_label.lower())}"
            nodes.append(GraphNode(id=tag_id, label=tag_label, kind="tag", path=page.path))
            edges.append(GraphEdge(source=f"page:{page.id}", target=tag_id, relation="tagged"))
        for link in page.links:
            target = page_by_key.get(normalize_key(link))
            if target:
                edges.append(
                    GraphEdge(
                        source=f"page:{page.id}", target=f"page:{target.id}", relation="links_to"
                    )
                )
            else:
                node_id = unresolved_node_id(link)
                nodes.append(
                    GraphNode(
                        id=node_id,
                        label=link,
                        kind=external_node_kind(link),
                        path=page.path,
                        metadata={"unresolved": True, "source": "wikilink"},
                    )
                )
                edges.append(
                    GraphEdge(
                        source=f"page:{page.id}",
                        target=node_id,
                        relation="links_to",
                        metadata={"unresolved": True},
                    )
                )
    project_dendron_hierarchy(loaded, page_by_key, nodes, edges)
    project_graph_json(loaded, page_by_key, nodes, edges)
    return WikiIndex(
        root=loaded.root,
        title=loaded.title,
        description=loaded.description,
        pages=loaded.pages,
        nodes=dedupe_nodes(nodes),
        edges=dedupe_edges(edges),
        adapter=loaded.adapter,
        implementation=loaded.implementation,
        metadata=adapter_metadata,
    )


def project_dendron_hierarchy(
    loaded: LoadedWiki,
    page_by_key: dict[str, WikiPage],
    nodes: list[GraphNode],
    edges: list[GraphEdge],
) -> None:
    if loaded.adapter != "dendron":
        return
    for page in loaded.pages:
        stem = page.path.rsplit(".", maxsplit=1)[0]
        if "." not in stem:
            continue
        parent_key = stem.rsplit(".", maxsplit=1)[0]
        parent = page_by_key.get(normalize_key(parent_key))
        if parent:
            source_id = f"page:{parent.id}"
        else:
            source_id = unresolved_node_id(parent_key)
            nodes.append(
                GraphNode(
                    id=source_id,
                    label=parent_key,
                    kind="placeholder",
                    path=page.path,
                    metadata={"unresolved": True, "source": "dendron_hierarchy"},
                )
            )
        edges.append(
            GraphEdge(
                source=source_id,
                target=f"page:{page.id}",
                relation="parent_of",
                metadata={"source": "dendron_hierarchy"},
            )
        )


def project_graph_json(
    loaded: LoadedWiki,
    page_by_key: dict[str, WikiPage],
    nodes: list[GraphNode],
    edges: list[GraphEdge],
) -> None:
    for fact in loaded.sidecar_graph_edges:
        raw_edge = fact.data
        source_value = endpoint_value(raw_edge, "from", "source")
        target_value = endpoint_value(raw_edge, "to", "target")
        if not source_value or not target_value:
            continue
        source_page = page_by_key.get(normalize_key(source_value))
        target_page = page_by_key.get(normalize_key(target_value))
        source_path = endpoint_path(source_page, target_page)
        target_path = endpoint_path(target_page, source_page)
        source_id = graph_endpoint_id(source_value, source_page, source_path, nodes)
        target_id = graph_endpoint_id(target_value, target_page, target_path, nodes)
        metadata: dict[str, Any] = {
            "source": fact.source,
            "path": fact.path,
        }
        if fact.confidence is not None:
            metadata["confidence"] = fact.confidence
        edges.append(
            GraphEdge(
                source=source_id,
                target=target_id,
                relation=canonical_relation(str(raw_edge.get("type") or "related_to")),
                metadata=metadata,
            )
        )


def endpoint_value(edge: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = edge.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def graph_endpoint_id(value: str, page: WikiPage | None, path: str, nodes: list[GraphNode]) -> str:
    if page:
        return f"page:{page.id}"
    node_id = unresolved_node_id(value)
    nodes.append(
        GraphNode(
            id=node_id,
            label=value,
            kind=external_node_kind(value),
            path=path,
            metadata={"unresolved": True, "source": "graph.json"},
        )
    )
    return node_id


def endpoint_path(primary: WikiPage | None, fallback: WikiPage | None) -> str:
    if primary:
        return primary.path
    if fallback:
        return fallback.path
    return ""


def unresolved_node_id(value: str) -> str:
    prefix = "external" if external_node_kind(value).startswith("external") else "placeholder"
    return f"{prefix}:{slug(value)}"


def external_node_kind(value: str) -> str:
    if re.match(r"https?://", value):
        return "external_ref"
    if re.match(r"(?:#\d+|[A-Z][A-Z0-9]+-\d+|GH-\d+)$", value):
        return "external_issue"
    return "placeholder"


def canonical_relation(value: str) -> str:
    relation = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return relation or "related_to"


def page_lookup(pages: list[WikiPage], *, source_root: str = ".") -> dict[str, WikiPage]:
    lookup: dict[str, WikiPage] = {}
    for page in pages:
        keys = {page.id, page.title, page.path, page.path.rsplit("/", maxsplit=1)[-1]}
        if source_root and source_root != ".":
            keys.add(f"{source_root.rstrip('/')}/{page.id}")
            keys.add(f"{source_root.rstrip('/')}/{page.path}")
        for key in keys:
            lookup[normalize_key(key)] = page
    return lookup


def normalize_key(value: str) -> str:
    stem = value[:-3] if value.endswith(".md") else value
    return re.sub(r"[^a-z0-9가-힣]+", "", stem.lower())


def slug(value: str) -> str:
    slugged = re.sub(r"[^A-Za-z0-9가-힣._-]+", "-", value.strip()).strip("-")
    return slugged or "item"


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
    by_key: dict[tuple[str, str, str], GraphEdge] = {}
    result: list[GraphEdge] = []
    for edge in edges:
        key = (edge.source, edge.target, edge.relation)
        if key in seen:
            by_key[key].metadata = merge_metadata(by_key[key].metadata, edge.metadata)
            continue
        seen.add(key)
        by_key[key] = edge
        result.append(edge)
    return result


def merge_metadata(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if key not in merged or merged[key] is None or merged[key] == "":
            merged[key] = value
        elif merged[key] != value:
            merged[key] = merge_metadata_value(merged[key], value)
    return merged


def merge_metadata_value(existing: Any, incoming: Any) -> Any:
    values = list(existing) if isinstance(existing, list) else [existing]
    if incoming not in values:
        values.append(incoming)
    return values
