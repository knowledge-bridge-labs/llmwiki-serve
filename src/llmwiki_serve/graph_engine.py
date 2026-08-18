from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from typing import Protocol

from .models import (
    GraphEdge,
    GraphNode,
    GraphProviderCapabilities,
    GraphQueryDirection,
    GraphQueryPath,
    GraphQueryRequest,
    GraphQueryResponse,
)


class GraphEngineProvider(Protocol):
    """Backend-neutral typed graph retrieval over a normalized LLMWiki graph."""

    def capabilities(self) -> GraphProviderCapabilities: ...

    def query(
        self,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
        request: GraphQueryRequest,
    ) -> GraphQueryResponse: ...


class InMemoryGraphEngineProvider:
    """Reference graph query engine used before optional native providers load."""

    def capabilities(self) -> GraphProviderCapabilities:
        return GraphProviderCapabilities(
            backend_kind="in-memory",
            persistent=False,
            local_only=True,
            snapshot_cache=False,
            structured_query=True,
            raw_query=False,
            vector_search=False,
            full_text_search=False,
            safe_for_default=True,
            limitations=["Traversal runs over the hydrated projection in process memory."],
        )

    def query(
        self,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
        request: GraphQueryRequest,
    ) -> GraphQueryResponse:
        graph = _GraphView(nodes, edges, request.relation_allowlist)
        if request.operation == "neighbors":
            return self._neighbors(graph, request, direction=request.direction)
        if request.operation == "backlinks":
            return self._neighbors(graph, request, direction="in")
        if request.operation == "paths":
            return self._paths(graph, request)
        if request.operation == "by_source_ref":
            return self._by_marker_node(
                graph,
                request,
                marker_value=request.source_ref,
                marker_kind="source_ref",
                marker_prefix="source:",
                relation="cites",
            )
        if request.operation == "by_tag":
            return self._by_marker_node(
                graph,
                request,
                marker_value=request.tag,
                marker_kind="tag",
                marker_prefix="tag:",
                relation="tagged",
            )
        raise ValueError(f"unsupported graph query operation: {request.operation}")

    def _neighbors(
        self,
        graph: _GraphView,
        request: GraphQueryRequest,
        *,
        direction: GraphQueryDirection,
    ) -> GraphQueryResponse:
        if not request.start_node_id:
            raise ValueError(f"{request.operation} requires start_node_id")
        graph.require_node(request.start_node_id)

        selected_node_ids: list[str] = [request.start_node_id]
        selected_edge_indexes: list[int] = []
        selected_edge_set: set[int] = set()
        visited = {request.start_node_id}
        queue: deque[tuple[str, int]] = deque([(request.start_node_id, 0)])

        while queue and len(selected_node_ids) < request.limit:
            node_id, depth = queue.popleft()
            if depth >= request.max_depth:
                continue
            for edge_index, next_node_id in graph.adjacent(node_id, direction):
                if edge_index not in selected_edge_set:
                    selected_edge_set.add(edge_index)
                    selected_edge_indexes.append(edge_index)
                if next_node_id in visited:
                    continue
                visited.add(next_node_id)
                selected_node_ids.append(next_node_id)
                if len(selected_node_ids) >= request.limit:
                    break
                queue.append((next_node_id, depth + 1))

        return graph.response(
            request,
            selected_node_ids,
            selected_edge_indexes,
        )

    def _paths(self, graph: _GraphView, request: GraphQueryRequest) -> GraphQueryResponse:
        if not request.start_node_id or not request.target_node_id:
            raise ValueError("paths requires start_node_id and target_node_id")
        graph.require_node(request.start_node_id)
        graph.require_node(request.target_node_id)
        if request.start_node_id == request.target_node_id:
            return graph.response(
                request,
                [request.start_node_id],
                [],
                paths=[GraphQueryPath(node_ids=[request.start_node_id], edges=[])],
            )

        paths: list[GraphQueryPath] = []
        selected_node_ids: list[str] = []
        selected_edge_indexes: list[int] = []
        selected_edges: set[int] = set()
        queue: deque[tuple[str, list[str], list[int]]] = deque(
            [(request.start_node_id, [request.start_node_id], [])]
        )

        while queue and len(paths) < request.limit:
            node_id, path_nodes, path_edges = queue.popleft()
            if len(path_edges) >= request.max_depth:
                continue
            for edge_index, next_node_id in graph.adjacent(node_id, request.direction):
                if next_node_id in path_nodes:
                    continue
                next_path_nodes = [*path_nodes, next_node_id]
                next_path_edges = [*path_edges, edge_index]
                if next_node_id == request.target_node_id:
                    path = GraphQueryPath(
                        node_ids=next_path_nodes,
                        edges=[graph.edges[index] for index in next_path_edges],
                    )
                    paths.append(path)
                    for selected_node_id in next_path_nodes:
                        if selected_node_id not in selected_node_ids:
                            selected_node_ids.append(selected_node_id)
                    for selected_edge_index in next_path_edges:
                        if selected_edge_index not in selected_edges:
                            selected_edges.add(selected_edge_index)
                            selected_edge_indexes.append(selected_edge_index)
                    if len(paths) >= request.limit:
                        break
                    continue
                queue.append((next_node_id, next_path_nodes, next_path_edges))

        return graph.response(
            request,
            selected_node_ids,
            selected_edge_indexes,
            paths=paths,
        )

    def _by_marker_node(
        self,
        graph: _GraphView,
        request: GraphQueryRequest,
        *,
        marker_value: str,
        marker_kind: str,
        marker_prefix: str,
        relation: str,
    ) -> GraphQueryResponse:
        if not marker_value:
            raise ValueError(f"{request.operation} requires {marker_kind}")
        marker_ids = graph.match_marker_ids(
            marker_value,
            marker_kind=marker_kind,
            marker_prefix=marker_prefix,
        )
        selected_node_ids: list[str] = []
        selected_edge_indexes: list[int] = []
        for marker_id in marker_ids:
            if marker_id not in selected_node_ids:
                selected_node_ids.append(marker_id)
            for edge_index, edge in graph.iter_edges(relation_allowlist=[relation]):
                if edge.target != marker_id:
                    continue
                if edge.source not in selected_node_ids:
                    selected_node_ids.append(edge.source)
                selected_edge_indexes.append(edge_index)
                if len(selected_node_ids) >= request.limit:
                    break
            if len(selected_node_ids) >= request.limit:
                break
        return graph.response(request, selected_node_ids, selected_edge_indexes)


class _GraphView:
    def __init__(
        self,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
        relation_allowlist: list[str],
    ) -> None:
        self.nodes = nodes
        self.edges = edges
        self.nodes_by_id = {node.id: node for node in nodes}
        self._allowed_relations = set(relation_allowlist)

    def require_node(self, node_id: str) -> GraphNode:
        node = self.nodes_by_id.get(node_id)
        if node is None:
            raise ValueError("graph query referenced an unknown node")
        return node

    def adjacent(self, node_id: str, direction: GraphQueryDirection) -> Iterable[tuple[int, str]]:
        for edge_index, edge in self.iter_edges():
            if (
                direction in {"out", "both"}
                and edge.source == node_id
                and edge.target in self.nodes_by_id
            ):
                yield edge_index, edge.target
            if (
                direction in {"in", "both"}
                and edge.target == node_id
                and edge.source in self.nodes_by_id
            ):
                yield edge_index, edge.source

    def iter_edges(
        self, *, relation_allowlist: list[str] | None = None
    ) -> Iterable[tuple[int, GraphEdge]]:
        allowed_relations = set(relation_allowlist or []) or self._allowed_relations
        for edge_index, edge in enumerate(self.edges):
            if allowed_relations and edge.relation not in allowed_relations:
                continue
            if edge.source not in self.nodes_by_id or edge.target not in self.nodes_by_id:
                continue
            yield edge_index, edge

    def match_marker_ids(
        self,
        value: str,
        *,
        marker_kind: str,
        marker_prefix: str,
    ) -> list[str]:
        candidates = {value}
        if not value.startswith(marker_prefix):
            candidates.add(f"{marker_prefix}{value}")
        return [
            node.id
            for node in self.nodes
            if node.kind == marker_kind and (node.id in candidates or node.label == value)
        ]

    def response(
        self,
        request: GraphQueryRequest,
        node_ids: list[str],
        edge_indexes: list[int],
        *,
        paths: list[GraphQueryPath] | None = None,
    ) -> GraphQueryResponse:
        node_id_set = set(node_ids)
        closed_edges: list[GraphEdge] = []
        seen_edge_indexes: set[int] = set()
        for edge_index in edge_indexes:
            if edge_index in seen_edge_indexes:
                continue
            seen_edge_indexes.add(edge_index)
            edge = self.edges[edge_index]
            if edge.source in node_id_set and edge.target in node_id_set:
                closed_edges.append(edge)
        return GraphQueryResponse(
            provider=InMemoryGraphEngineProvider().capabilities(),
            operation=request.operation,
            nodes=[
                self.nodes_by_id[node_id] for node_id in node_ids if node_id in self.nodes_by_id
            ],
            edges=closed_edges[: request.limit],
            paths=paths or [],
        )
