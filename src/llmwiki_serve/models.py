from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PageRole = Literal["hot", "index", "overview", "topic"]
ReviewState = Literal[
    "approved", "reviewed", "verified", "draft", "proposed", "needs_review", "unknown"
]
GraphNeighborhoodDirection = Literal["out", "in", "both"]

NON_SERVING_STATUSES = {
    "draft",
    "proposed",
    "needs_review",
    "blocked",
    "unpublished",
    "private",
    "hidden",
    "embargoed",
    "confidential",
    "internal",
    "withheld",
}


class WikiPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    path: str
    role: PageRole
    text: str
    summary: str = ""
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    review_state: ReviewState = "unknown"
    status: str = ""
    source_refs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    headings: list[str] = Field(default_factory=list)
    updated_at: str = ""

    @property
    def approved_for_serving(self) -> bool:
        if frontmatter_bool(self.frontmatter.get("draft")) is True:
            return False
        if frontmatter_bool(self.frontmatter.get("published")) is False:
            return False
        if frontmatter_bool(self.frontmatter.get("publish")) is False:
            return False
        if self.review_state in {"draft", "proposed", "needs_review"}:
            return False
        status = visibility_status(self.status)
        return status not in NON_SERVING_STATUSES


class GraphNode(BaseModel):
    id: str
    label: str
    kind: str
    path: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: str
    target: str
    relation: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    page_id: str
    title: str
    path: str
    score: float
    snippet: str
    role: str
    source_refs: list[str] = Field(default_factory=list)
    route: str = ""


class ContextPack(BaseModel):
    query: str
    wiki_title: str
    description: str = ""
    adapter: str = ""
    implementation: str = ""
    page_count: int = 0
    approved_page_count: int = 0
    answerable: bool
    orientation: list[SearchResult] = Field(default_factory=list)
    evidence: list[SearchResult] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    graph: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


class GraphNeighborhoodResponse(BaseModel):
    seeds: list[str] = Field(default_factory=list)
    unmatched: list[str] = Field(default_factory=list)
    depth: int = 1
    direction: GraphNeighborhoodDirection = "both"
    relations: list[str] = Field(default_factory=list)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class ProjectionMetadata(BaseModel):
    signature: str = ""
    page_count: int = 0
    approved_page_count: int = 0
    graph_node_count: int = 0
    graph_edge_count: int = 0


class RawOriginsMetadata(BaseModel):
    enabled: bool = False
    metadata_only: bool = True
    public_root_labels: list[str] = Field(default_factory=list)


class WikiManifest(BaseModel):
    title: str
    description: str
    root: str
    source_id: str = ""
    bundle_id: str = ""
    public_uri: str = ""
    adapter: str = ""
    implementation: str = ""
    page_count: int
    approved_page_count: int
    hot_page: str = ""
    index_page: str = ""
    overview_page: str = ""
    projection: ProjectionMetadata = Field(default_factory=ProjectionMetadata)
    raw_origins: RawOriginsMetadata = Field(default_factory=RawOriginsMetadata)
    capabilities: list[str] = Field(default_factory=list)


class SourceRef(BaseModel):
    id: str
    label: str
    kind: str = "source_ref"
    uri: str = ""
    linked_pages: list[str] = Field(default_factory=list)
    linked_page_ids: list[str] = Field(default_factory=list)
    locator: dict[str, Any] = Field(default_factory=dict)


class SourceRefsResponse(BaseModel):
    source_id: str
    bundle_id: str
    source_refs: list[SourceRef] = Field(default_factory=list)


class SourceBundleManifest(BaseModel):
    source_id: str
    bundle_id: str
    public_uri: str = ""
    title: str
    description: str = ""
    adapter: str = ""
    implementation: str = ""
    projection: ProjectionMetadata = Field(default_factory=ProjectionMetadata)
    raw_origins: RawOriginsMetadata = Field(default_factory=RawOriginsMetadata)
    capabilities: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)


class WikiIndex(BaseModel):
    root: Path
    title: str
    description: str = ""
    adapter: str = ""
    implementation: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    pages: list[WikiPage]
    nodes: list[GraphNode]
    edges: list[GraphEdge]

    model_config = ConfigDict(arbitrary_types_allowed=True)


def frontmatter_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return None


def visibility_status(value: str) -> str:
    return "_".join(value.strip().lower().replace("-", " ").split())
