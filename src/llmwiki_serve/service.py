from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .adapters import load_wiki
from .models import (
    ContextPack,
    GraphEdge,
    GraphNeighborhoodDirection,
    GraphNeighborhoodResponse,
    GraphNode,
    ProjectionMetadata,
    RawOriginsMetadata,
    SourceBundleManifest,
    SourceRef,
    SourceRefsResponse,
    WikiIndex,
    WikiManifest,
)
from .projection import canonical_relation, normalize_key, project_wiki, slug
from .projection_store import (
    InMemoryProjectionStore,
    ProjectionKey,
    ProjectionRecord,
    ProjectionStore,
)
from .search import SearchCorpus, build_search_corpus, context_orientation, search_corpus

SourceSignature = tuple[tuple[str, int, int], ...]
_ProjectionSignature = tuple["_PathState", ...]
_PathKind = Literal["dir", "file", "missing"]
SIGNATURE_SUFFIXES = {".md", ".org"}
SIGNATURE_FILENAMES = {
    ".wiki-compiler.json",
    "dendron.yml",
    "quartz.config.js",
    "quartz.config.ts",
    "quartz.config.yaml",
    "quartz.config.yml",
}
SIGNATURE_MARKER_NAMES = {".foam", ".obsidian"}
SIGNATURE_RELATIVE_FILENAMES = {".vscode/extensions.json", "logseq/config.edn"}
IGNORED_SIGNATURE_PARTS = {
    ".git",
    ".runtime-logs",
    "node_modules",
    ".venv",
    "__pycache__",
    "dist",
    "build",
}


class LlmWikiService:
    def __init__(
        self,
        root: Path | str,
        *,
        refresh_interval_seconds: float = 0.0,
        producer_manifest_path: Path | str | None = None,
        projection_store: ProjectionStore | None = None,
        cache_namespace: str = "default",
        source_id: str | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if refresh_interval_seconds < 0:
            raise ValueError("refresh_interval_seconds must be non-negative")
        self.root = Path(root)
        self.refresh_interval_seconds = refresh_interval_seconds
        self.projection_store = projection_store or InMemoryProjectionStore()
        self.cache_namespace = cache_namespace
        self.explicit_source_id = source_id
        self._clock = clock or time.monotonic
        self._last_refresh_check: float | None = None
        self._index: WikiIndex | None = None
        self._views: _IndexViews | None = None
        self._signature: SourceSignature | None = None
        self._projection_signature: _ProjectionSignature | None = None
        self._freshness_signature: _ProjectionSignature | None = None
        self._signature_cache: _SourceSignatureCache | _ProducerManifestSignatureCache
        if producer_manifest_path is None:
            self._signature_cache = _SourceSignatureCache(self.root)
        else:
            self._signature_cache = _ProducerManifestSignatureCache(
                self.root,
                Path(producer_manifest_path),
            )

    def index(self, *, refresh: bool = False) -> WikiIndex:
        if self._can_reuse_cached_index(refresh=refresh):
            assert self._index is not None
            return self._index

        snapshot = self._signature_cache.current_snapshot(refresh=refresh)
        projection_signature = projection_signature_digest(snapshot.projection_signature)
        if (
            self._index is None
            or refresh
            or snapshot.signature != self._signature
            or snapshot.projection_signature != self._projection_signature
            or snapshot.freshness_signature != self._freshness_signature
        ):
            key = ProjectionKey(
                namespace=self.cache_namespace,
                source_id=self._cache_source_id(),
                projection_signature=projection_signature,
            )
            record = None if refresh else self.projection_store.get(key, root=self.root)
            if record is None:
                index = project_wiki(load_wiki(self.root))
                key = ProjectionKey(
                    namespace=self.cache_namespace,
                    source_id=self._cache_source_id(),
                    projection_signature=projection_signature,
                )
                record = ProjectionRecord(key=key, index=index)
                self.projection_store.put(record)
            self._index = record.index
            self._views = None
            self._signature = snapshot.signature
            self._projection_signature = snapshot.projection_signature
            self._freshness_signature = snapshot.freshness_signature
        self._last_refresh_check = self._clock() if self.refresh_interval_seconds > 0 else None
        return self._index

    def _can_reuse_cached_index(self, *, refresh: bool) -> bool:
        if (
            refresh
            or self._index is None
            or self.refresh_interval_seconds <= 0
            or self._last_refresh_check is None
        ):
            return False
        return self._clock() - self._last_refresh_check < self.refresh_interval_seconds

    def manifest(self, *, enable_a2a_compat: bool = False) -> WikiManifest:
        index = self.index()
        hot = next((page.path for page in index.pages if page.role == "hot"), "")
        idx = next((page.path for page in index.pages if page.role == "index"), "")
        overview = next((page.path for page in index.pages if page.role == "overview"), "")
        capabilities = [
            "llmwiki_source_bundle",
            "llmwiki_context",
            "llmwiki_search",
            "llmwiki_read",
            "llmwiki_graph",
            "llmwiki_graph_neighbors",
            "llmwiki_source_refs",
            "mcp-jsonrpc",
            "mcp-streamable-http",
        ]
        if enable_a2a_compat:
            capabilities.append("a2a-message")
        source_id = self._source_id_for_index(index)
        projection_signature = projection_signature_digest(self._projection_signature or ())
        bundle_id = source_bundle_id(source_id, projection_signature)
        return WikiManifest(
            title=index.title,
            description=index.description,
            root=str(index.root),
            source_id=source_id,
            bundle_id=bundle_id,
            public_uri=f"llmwiki://{source_id}",
            adapter=index.adapter,
            implementation=index.implementation,
            page_count=len(index.pages),
            approved_page_count=sum(1 for page in index.pages if page.approved_for_serving),
            hot_page=hot,
            index_page=idx,
            overview_page=overview,
            projection=ProjectionMetadata(
                signature=projection_signature,
                page_count=len(index.pages),
                approved_page_count=sum(1 for page in index.pages if page.approved_for_serving),
                graph_node_count=len(index.nodes),
                graph_edge_count=len(index.edges),
            ),
            raw_origins=raw_origins_metadata(index),
            capabilities=capabilities,
        )

    def _source_id_for_index(self, index: WikiIndex) -> str:
        return self.explicit_source_id or source_id_for_index(index)

    def _cache_source_id(self) -> str:
        return self.explicit_source_id or source_id_for_root(self.root)

    def context(self, query: str, *, limit: int = 8, include_drafts: bool = False) -> ContextPack:
        index = self.index()
        views = self._index_views(index)
        orientation = context_orientation(index, include_drafts=include_drafts)
        evidence = search_corpus(views.search_corpus(include_drafts), query, limit=limit)
        limitations: list[str] = []
        if not evidence:
            limitations.append("No matching approved LLMWiki page was found.")
        if not include_drafts:
            withheld = sum(1 for page in index.pages if not page.approved_for_serving)
            if withheld:
                limitations.append(f"{withheld} draft or unapproved page(s) were withheld.")
        return ContextPack(
            query=query,
            wiki_title=index.title,
            description=index.description,
            adapter=index.adapter,
            implementation=index.implementation,
            page_count=len(index.pages),
            approved_page_count=sum(1 for page in index.pages if page.approved_for_serving),
            answerable=bool(evidence),
            orientation=orientation,
            evidence=evidence,
            limitations=limitations,
            graph=views.graph_view(include_drafts).payload(120),
        )

    def search(
        self, query: str, *, limit: int = 8, include_drafts: bool = False
    ) -> list[dict[str, Any]]:
        views = self._index_views()
        return [
            item.model_dump()
            for item in search_corpus(views.search_corpus(include_drafts), query, limit=limit)
        ]

    def _index_views(self, index: WikiIndex | None = None) -> _IndexViews:
        current = index or self.index()
        if self._views is None or self._views.index is not current:
            self._views = _IndexViews.build(current)
        return self._views

    def read(self, page_id: str, *, include_drafts: bool = False) -> dict[str, Any]:
        for page in self.index().pages:
            if page.id == page_id or page.path == page_id:
                if not include_drafts and not page.approved_for_serving:
                    return {"found": False, "reason": "not approved for serving"}
                return page.model_dump(mode="json")
        return {"found": False}

    def source_refs(self, *, include_drafts: bool = False) -> SourceRefsResponse:
        index = self.index()
        manifest = self.manifest()
        refs: dict[str, SourceRef] = {}
        ids_by_label: dict[str, str] = {}
        used_ids: set[str] = set()
        pages = (
            index.pages
            if include_drafts
            else [page for page in index.pages if page.approved_for_serving]
        )
        for page in pages:
            for label in page.source_refs:
                ref_id = stable_source_ref_id(label, ids_by_label, used_ids)
                current = refs.get(ref_id)
                if current is None:
                    current = SourceRef(
                        id=ref_id,
                        label=label,
                        uri=f"llmwiki://{manifest.source_id}/source-refs/{ref_id}",
                    )
                    refs[ref_id] = current
                if page.path not in current.linked_pages:
                    current.linked_pages.append(page.path)
                if page.id not in current.linked_page_ids:
                    current.linked_page_ids.append(page.id)
        return SourceRefsResponse(
            source_id=manifest.source_id,
            bundle_id=manifest.bundle_id,
            source_refs=sorted(refs.values(), key=lambda item: item.id),
        )

    def source_bundle(
        self, *, include_drafts: bool = False, enable_a2a_compat: bool = False
    ) -> SourceBundleManifest:
        manifest = self.manifest(enable_a2a_compat=enable_a2a_compat)
        source_refs = self.source_refs(include_drafts=include_drafts)
        return SourceBundleManifest(
            source_id=manifest.source_id,
            bundle_id=manifest.bundle_id,
            public_uri=manifest.public_uri,
            title=manifest.title,
            description=manifest.description,
            adapter=manifest.adapter,
            implementation=manifest.implementation,
            projection=manifest.projection,
            raw_origins=manifest.raw_origins,
            capabilities=manifest.capabilities,
            source_refs=source_refs.source_refs,
        )

    def projection_store_diagnostics(self) -> dict[str, Any]:
        return {
            "backend": self.projection_store.__class__.__name__,
            "namespace": self.cache_namespace,
            "cache_source_id": self._cache_source_id(),
            "available": getattr(self.projection_store, "available", True),
            "last_error": getattr(self.projection_store, "last_error", ""),
        }

    def graph(
        self, *, limit: int = 500, include_drafts: bool = False
    ) -> dict[str, list[dict[str, Any]]]:
        return self._index_views().graph_view(include_drafts).payload(limit)

    def graph_neighbors(
        self,
        *,
        seeds: list[str] | None = None,
        depth: int = 1,
        direction: GraphNeighborhoodDirection = "both",
        relations: list[str] | None = None,
        limit: int = 50,
        include_drafts: bool = False,
    ) -> GraphNeighborhoodResponse:
        graph = self._index_views().graph_view(include_drafts)
        normalized_seeds = [item.strip() for item in seeds or [] if item.strip()]
        normalized_relations = normalize_relations(relations or [])
        resolved_seeds, unmatched = graph.resolve_seeds(normalized_seeds)
        neighborhood = graph.neighborhood(
            resolved_seeds,
            depth=max(0, depth),
            direction=direction,
            relations=normalized_relations,
            limit=max(1, limit),
        )
        return GraphNeighborhoodResponse(
            seeds=resolved_seeds,
            unmatched=unmatched,
            depth=max(0, depth),
            direction=direction,
            relations=normalized_relations,
            nodes=neighborhood.nodes,
            edges=neighborhood.edges,
        )


@dataclass
class _IndexViews:
    index: WikiIndex
    approved_search: SearchCorpus | None = None
    all_search: SearchCorpus | None = None
    approved_graph: _GraphView | None = None
    all_graph: _GraphView | None = None

    @classmethod
    def build(cls, index: WikiIndex) -> _IndexViews:
        return cls(index=index)

    def search_corpus(self, include_drafts: bool) -> SearchCorpus:
        if include_drafts:
            if self.all_search is None:
                self.all_search = build_search_corpus(self.index.pages)
            return self.all_search
        if self.approved_search is None:
            approved_pages = [page for page in self.index.pages if page.approved_for_serving]
            self.approved_search = build_search_corpus(approved_pages)
        return self.approved_search

    def graph_view(self, include_drafts: bool) -> _GraphView:
        if include_drafts:
            if self.all_graph is None:
                self.all_graph = _GraphView.build(self.index.nodes, self.index.edges)
            return self.all_graph
        if self.approved_graph is None:
            self.approved_graph = approved_graph_view(self.index)
        return self.approved_graph


@dataclass(frozen=True)
class _NeighborhoodResult:
    nodes: list[GraphNode]
    edges: list[GraphEdge]


@dataclass(frozen=True)
class _GraphView:
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    nodes_by_id: dict[str, GraphNode]
    outgoing: dict[str, tuple[GraphEdge, ...]]
    incoming: dict[str, tuple[GraphEdge, ...]]

    @classmethod
    def build(cls, nodes: list[GraphNode], edges: list[GraphEdge]) -> _GraphView:
        node_ids = {node.id for node in nodes}
        visible_edges = [
            edge for edge in edges if edge.source in node_ids and edge.target in node_ids
        ]
        outgoing: dict[str, list[GraphEdge]] = {}
        incoming: dict[str, list[GraphEdge]] = {}
        for edge in visible_edges:
            outgoing.setdefault(edge.source, []).append(edge)
            incoming.setdefault(edge.target, []).append(edge)
        return cls(
            nodes=tuple(nodes),
            edges=tuple(visible_edges),
            nodes_by_id={node.id: node for node in nodes},
            outgoing={key: tuple(value) for key, value in outgoing.items()},
            incoming={key: tuple(value) for key, value in incoming.items()},
        )

    def payload(self, limit: int) -> dict[str, list[dict[str, Any]]]:
        return closed_graph_payload(list(self.nodes), list(self.edges), limit)

    def resolve_seeds(self, seeds: list[str]) -> tuple[list[str], list[str]]:
        resolved: list[str] = []
        unmatched: list[str] = []
        for seed in seeds:
            matches = self._resolve_seed(seed)
            if not matches:
                unmatched.append(seed)
                continue
            for match in matches:
                if match not in resolved:
                    resolved.append(match)
        return resolved, unmatched

    def _resolve_seed(self, seed: str) -> list[str]:
        if seed in self.nodes_by_id:
            return [seed]
        page_candidate = f"page:{seed}"
        if page_candidate in self.nodes_by_id:
            return [page_candidate]
        slugged_page = f"page:{slug(seed)}"
        if slugged_page in self.nodes_by_id:
            return [slugged_page]

        key = normalize_graph_lookup(seed)
        matches = [
            node.id
            for node in self.nodes
            if key
            and key
            in {
                normalize_graph_lookup(node.id),
                normalize_graph_lookup(node.id.removeprefix("page:")),
                normalize_graph_lookup(node.label),
                normalize_graph_lookup(node.path),
                normalize_graph_lookup(Path(node.path).stem if node.path else ""),
            }
        ]
        return matches[:10]

    def neighborhood(
        self,
        seeds: list[str],
        *,
        depth: int,
        direction: GraphNeighborhoodDirection,
        relations: list[str],
        limit: int,
    ) -> _NeighborhoodResult:
        if not seeds:
            return _NeighborhoodResult(nodes=[], edges=[])
        relation_set = set(relations)
        visited: list[str] = []
        visited_set: set[str] = set()
        frontier = [seed for seed in seeds if seed in self.nodes_by_id]
        traversed_edges: list[GraphEdge] = []
        traversed_edge_keys: set[tuple[str, str, str]] = set()

        for seed in frontier:
            if seed not in visited_set:
                visited_set.add(seed)
                visited.append(seed)
        for _step in range(depth):
            if len(visited) >= limit:
                break
            next_frontier: list[str] = []
            for node_id in frontier:
                for edge, adjacent in self.iter_edges(
                    node_id, direction=direction, relation_set=relation_set
                ):
                    edge_key = (edge.source, edge.target, edge.relation)
                    if edge_key not in traversed_edge_keys:
                        traversed_edge_keys.add(edge_key)
                        traversed_edges.append(edge)
                    if adjacent not in visited_set and adjacent in self.nodes_by_id:
                        visited_set.add(adjacent)
                        visited.append(adjacent)
                        next_frontier.append(adjacent)
                    if len(visited) >= limit:
                        break
                if len(visited) >= limit:
                    break
            if not next_frontier:
                break
            frontier = next_frontier

        returned_ids = set(visited[:limit])
        nodes = [self.nodes_by_id[node_id] for node_id in visited[:limit]]
        edges = [
            edge
            for edge in traversed_edges
            if edge.source in returned_ids and edge.target in returned_ids
        ][:limit]
        return _NeighborhoodResult(nodes=nodes, edges=edges)

    def iter_edges(
        self,
        node_id: str,
        *,
        direction: GraphNeighborhoodDirection,
        relation_set: set[str],
    ) -> list[tuple[GraphEdge, str]]:
        result: list[tuple[GraphEdge, str]] = []
        if direction in {"out", "both"}:
            result.extend((edge, edge.target) for edge in self.outgoing.get(node_id, ()))
        if direction in {"in", "both"}:
            result.extend((edge, edge.source) for edge in self.incoming.get(node_id, ()))
        if relation_set:
            return [(edge, adjacent) for edge, adjacent in result if edge.relation in relation_set]
        return result


def approved_graph_view(index: WikiIndex) -> _GraphView:
    approved_pages = {f"page:{page.id}" for page in index.pages if page.approved_for_serving}
    approved_paths = {page.path for page in index.pages if page.approved_for_serving}
    unapproved_paths = {page.path for page in index.pages if not page.approved_for_serving}
    approved_adjacent_nodes = adjacent_non_page_nodes(index.edges, approved_pages)
    approved_adjacent_paths = adjacent_non_page_paths(index, approved_pages)
    visible_nodes = {
        node.id
        for node in index.nodes
        if node.id in approved_pages
        or (node.path and node.path in approved_paths)
        or node.id in approved_adjacent_nodes
    }
    nodes = [
        approved_graph_node(node, approved_adjacent_paths, unapproved_paths)
        for node in index.nodes
        if node.id in visible_nodes
    ]
    edges = [
        edge
        for edge in index.edges
        if edge.source in visible_nodes and edge.target in visible_nodes
    ]
    return _GraphView.build(nodes, edges)


def normalize_relations(relations: list[str]) -> list[str]:
    normalized: list[str] = []
    for relation in relations:
        canonical = canonical_relation(relation)
        if canonical and canonical not in normalized:
            normalized.append(canonical)
    return normalized


def normalize_graph_lookup(value: str) -> str:
    return normalize_key(value).casefold()


def closed_graph_payload(
    nodes: list[GraphNode], edges: list[GraphEdge], limit: int
) -> dict[str, list[dict[str, Any]]]:
    limited_nodes = nodes[:limit]
    node_ids = {node.id for node in limited_nodes}
    limited_edges = [edge for edge in edges if edge.source in node_ids and edge.target in node_ids][
        :limit
    ]
    return {
        "nodes": [node.model_dump() for node in limited_nodes],
        "edges": [edge.model_dump() for edge in limited_edges],
    }


def adjacent_non_page_nodes(edges: list[GraphEdge], approved_pages: set[str]) -> set[str]:
    visible: set[str] = set()
    for edge in edges:
        if edge.source not in approved_pages and edge.target not in approved_pages:
            continue
        if not edge.source.startswith("page:"):
            visible.add(edge.source)
        if not edge.target.startswith("page:"):
            visible.add(edge.target)
    return visible


def adjacent_non_page_paths(index: WikiIndex, approved_pages: set[str]) -> dict[str, str]:
    approved_page_paths = {
        f"page:{page.id}": page.path for page in index.pages if page.approved_for_serving
    }
    paths: dict[str, str] = {}
    for edge in index.edges:
        source_path = approved_page_paths.get(edge.source)
        target_path = approved_page_paths.get(edge.target)
        if source_path and not edge.target.startswith("page:"):
            paths.setdefault(edge.target, source_path)
        if target_path and not edge.source.startswith("page:"):
            paths.setdefault(edge.source, target_path)
    return paths


def approved_graph_node(
    node: GraphNode, approved_adjacent_paths: dict[str, str], unapproved_paths: set[str]
) -> GraphNode:
    if node.id.startswith("page:"):
        return node
    return node.model_copy(
        update={
            "path": approved_adjacent_paths.get(node.id, node.path),
            "metadata": sanitize_graph_node_metadata(node.metadata, unapproved_paths),
        }
    )


def sanitize_graph_node_metadata(
    metadata: dict[str, Any], unapproved_paths: set[str]
) -> dict[str, Any]:
    return {
        key: value
        for key, value in metadata.items()
        if not references_any_path(value, unapproved_paths)
    }


def references_any_path(value: Any, paths: set[str]) -> bool:
    if isinstance(value, str):
        return any(path and path in value for path in paths)
    if isinstance(value, dict):
        return any(references_any_path(item, paths) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(references_any_path(item, paths) for item in value)
    return False


def source_id_for_index(index: WikiIndex) -> str:
    return slug(index.title or index.root.name).lower()


def source_id_for_root(root: Path) -> str:
    return slug(root.name).lower()


def projection_signature_digest(signature: _ProjectionSignature) -> str:
    if not signature:
        return ""
    payload = "\n".join(
        "\t".join(
            [
                state.relative_path,
                state.kind,
                str(state.size),
                state.digest,
            ]
        )
        for state in signature
    )
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def source_bundle_id(source_id: str, projection_signature: str) -> str:
    if not projection_signature:
        return source_id
    algorithm, _, digest = projection_signature.partition(":")
    if algorithm and digest:
        return f"{source_id}:{algorithm}:{digest[:12]}"
    return f"{source_id}:{projection_signature[:12]}"


def raw_origins_metadata(index: WikiIndex) -> RawOriginsMetadata:
    labels = []
    source_root = str(index.metadata.get("source_root") or ".").strip("/") or "."
    for label in ("raw", "sources"):
        candidate = index.root / source_root / label if source_root != "." else index.root / label
        if candidate.exists() and candidate.is_dir():
            labels.append(label)
    return RawOriginsMetadata(
        enabled=False,
        metadata_only=True,
        public_root_labels=labels,
    )


def stable_source_ref_id(label: str, ids_by_label: dict[str, str], used_ids: set[str]) -> str:
    existing = ids_by_label.get(label)
    if existing:
        return existing
    base = slug(label).lower()
    candidate = base
    if candidate not in used_ids:
        ids_by_label[label] = candidate
        used_ids.add(candidate)
        return candidate
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:8]
    candidate = f"{base}-{digest}"
    counter = 2
    while candidate in used_ids:
        candidate = f"{base}-{digest}-{counter}"
        counter += 1
    ids_by_label[label] = candidate
    used_ids.add(candidate)
    return candidate


@dataclass(frozen=True)
class _PathState:
    relative_path: str
    kind: _PathKind
    device: int
    inode: int
    mtime_ns: int
    size: int
    digest: str = ""


@dataclass(frozen=True)
class _SourceSignatureSnapshot:
    signature: SourceSignature
    projection_signature: _ProjectionSignature
    paths: tuple[_PathState, ...]
    freshness_signature: _ProjectionSignature = ()


class _SourceSignatureCache:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._snapshot: _SourceSignatureSnapshot | None = None

    def current(self, *, refresh: bool = False) -> SourceSignature:
        return self.current_snapshot(refresh=refresh).signature

    def current_snapshot(self, *, refresh: bool = False) -> _SourceSignatureSnapshot:
        if refresh or self._snapshot is None or not self._is_current(self._snapshot):
            self._snapshot = _source_signature_snapshot(self.root)
        return self._snapshot

    def _is_current(self, snapshot: _SourceSignatureSnapshot) -> bool:
        return all(_current_path_state(self.root, state) == state for state in snapshot.paths)


class _ProducerManifestSignatureCache:
    def __init__(self, root: Path, manifest_path: Path) -> None:
        self.root = root
        self.manifest_path = manifest_path
        self._snapshot: _SourceSignatureSnapshot | None = None
        self._fallback = _SourceSignatureCache(root)

    def current(self, *, refresh: bool = False) -> SourceSignature:
        return self.current_snapshot(refresh=refresh).signature

    def current_snapshot(self, *, refresh: bool = False) -> _SourceSignatureSnapshot:
        marker_snapshot = producer_manifest_signature_snapshot(self.root, self.manifest_path)
        if marker_snapshot is None:
            self._snapshot = None
            return self._fallback.current_snapshot(refresh=refresh)
        if refresh or self._snapshot is None or not self._marker_matches(marker_snapshot):
            content_snapshot = _source_signature_snapshot(self.root)
            self._snapshot = _SourceSignatureSnapshot(
                signature=marker_snapshot.signature,
                projection_signature=content_snapshot.projection_signature,
                paths=marker_snapshot.paths,
                freshness_signature=marker_snapshot.freshness_signature,
            )
        return self._snapshot

    def _marker_matches(self, marker_snapshot: _SourceSignatureSnapshot) -> bool:
        if self._snapshot is None:
            return False
        return (
            self._snapshot.signature == marker_snapshot.signature
            and self._snapshot.freshness_signature == marker_snapshot.freshness_signature
        )


def source_signature(root: Path) -> SourceSignature:
    return _source_signature_snapshot(root).signature


def producer_manifest_signature_snapshot(
    root: Path, manifest_path: Path
) -> _SourceSignatureSnapshot | None:
    manifest_file = safe_producer_manifest_file(root, manifest_path)
    if manifest_file is None:
        return None
    try:
        root_resolved = root.expanduser().resolve()
        relative = manifest_file.relative_to(root_resolved).as_posix()
    except (OSError, ValueError):
        return None
    state = _path_state(manifest_file, relative, "file")
    if state is None:
        return None
    return _SourceSignatureSnapshot(
        signature=((state.relative_path, state.mtime_ns, state.size),),
        projection_signature=(),
        paths=(state,),
        freshness_signature=(state,),
    )


def safe_producer_manifest_file(root: Path, manifest_path: Path) -> Path | None:
    try:
        root_resolved = root.expanduser().resolve()
    except OSError:
        return None
    candidate = (
        manifest_path.expanduser() if manifest_path.is_absolute() else root_resolved / manifest_path
    )
    try:
        if candidate.is_symlink():
            return None
        resolved = candidate.resolve()
        resolved.relative_to(root_resolved)
    except (OSError, ValueError):
        return None
    if not resolved.is_file():
        return None
    return resolved


def _source_signature_snapshot(root: Path) -> _SourceSignatureSnapshot:
    entries: list[tuple[str, int, int]] = []
    projection_states: list[_PathState] = []
    path_states: list[_PathState] = []
    if not root.exists():
        return _SourceSignatureSnapshot((), (), (_PathState(".", "missing", 0, 0, 0, 0, ""),))

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            dirname for dirname in dirnames if dirname not in IGNORED_SIGNATURE_PARTS
        )
        current_dir = Path(dirpath)
        relative_dir = current_dir.relative_to(root).as_posix()
        dir_state = _path_state(current_dir, relative_dir, "dir", allow_symlink=relative_dir == ".")
        if dir_state is not None:
            path_states.append(dir_state)
        for dirname in [dirname for dirname in dirnames if dirname in SIGNATURE_MARKER_NAMES]:
            path = current_dir / dirname
            if path.is_symlink():
                continue
            try:
                stat = path.stat()
                relative = path.relative_to(root).as_posix()
            except OSError:
                continue
            if relative in SIGNATURE_MARKER_NAMES:
                entries.append((f"{relative}/", stat.st_mtime_ns, 0))
                marker_state = _path_state(path, relative, "dir")
                if marker_state is not None:
                    path_states.append(marker_state)
                    projection_states.append(marker_state)
        dirnames[:] = [dirname for dirname in dirnames if dirname not in SIGNATURE_MARKER_NAMES]
        for filename in sorted(filenames):
            path = current_dir / filename
            try:
                stat = path.stat()
                relative = path.relative_to(root).as_posix()
            except OSError:
                continue
            if not is_signature_file(path, relative):
                continue
            entries.append((relative, stat.st_mtime_ns, stat.st_size))
            file_state = _path_state(path, relative, "file")
            if file_state is not None:
                path_states.append(file_state)
                projection_states.append(file_state)
    if not path_states:
        path_states.append(_root_fallback_state(root))
    return _SourceSignatureSnapshot(tuple(entries), tuple(projection_states), tuple(path_states))


def _root_fallback_state(root: Path) -> _PathState:
    dir_state = _path_state(root, ".", "dir", allow_symlink=True)
    if dir_state is not None:
        return dir_state
    file_state = _path_state(root, ".", "file", allow_symlink=True)
    if file_state is not None:
        return file_state
    return _PathState(".", "missing", 0, 0, 0, 0, "")


def _current_path_state(root: Path, previous: _PathState) -> _PathState | None:
    path = root if previous.relative_path == "." else root / previous.relative_path
    if previous.kind == "missing":
        if path.exists():
            return _root_fallback_state(path)
        return previous
    return _path_state(
        path,
        previous.relative_path,
        previous.kind,
        allow_symlink=previous.relative_path == ".",
    )


def _path_state(
    path: Path, relative_path: str, kind: Literal["dir", "file"], *, allow_symlink: bool = False
) -> _PathState | None:
    if not allow_symlink and path.is_symlink():
        return None
    if kind == "dir" and not path.is_dir():
        return None
    if kind == "file" and not path.is_file():
        return None
    try:
        stat = path.stat()
        digest = _file_digest(path) if kind == "file" else _directory_digest(path, relative_path)
    except OSError:
        return None
    return _PathState(
        relative_path,
        kind,
        stat.st_dev,
        stat.st_ino,
        stat.st_mtime_ns,
        stat.st_size,
        digest,
    )


def _file_digest(path: Path) -> str:
    digest = hashlib.blake2b(digest_size=16)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_digest(path: Path, relative_path: str) -> str:
    if relative_path in SIGNATURE_MARKER_NAMES:
        return ""
    digest = hashlib.blake2b(digest_size=16)
    for child in sorted(path.iterdir(), key=lambda item: item.name):
        if child.name in IGNORED_SIGNATURE_PARTS or child.is_symlink():
            continue
        child_relative = child.name if relative_path == "." else f"{relative_path}/{child.name}"
        if child.is_dir():
            digest.update(f"dir:{child.name}\0".encode())
        elif is_signature_file(child, child_relative):
            digest.update(f"file:{child.name}\0".encode())
    return digest.hexdigest()


def is_signature_file(path: Path, relative_path: str) -> bool:
    relative_parts = Path(relative_path).parts
    if path.is_symlink() or not path.is_file() or set(relative_parts) & IGNORED_SIGNATURE_PARTS:
        return False
    if ".vscode" in relative_parts and path.suffix.lower() in SIGNATURE_SUFFIXES:
        return False
    if path.suffix.lower() in SIGNATURE_SUFFIXES:
        return True
    if path.name == "graph.json" and path.parent.name == "graph":
        return True
    if relative_path in SIGNATURE_MARKER_NAMES:
        return True
    if relative_path in SIGNATURE_RELATIVE_FILENAMES:
        return True
    return path.name in SIGNATURE_FILENAMES
