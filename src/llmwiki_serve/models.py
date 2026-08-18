from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

PageRole = Literal["hot", "index", "overview", "topic"]
ReviewState = Literal[
    "approved", "reviewed", "verified", "draft", "proposed", "needs_review", "unknown"
]
GraphNeighborhoodDirection = Literal["out", "in", "both"]
GraphQueryDirection: TypeAlias = GraphNeighborhoodDirection
GraphQueryOperation = Literal["neighbors", "backlinks", "paths", "by_source_ref", "by_tag"]
SearchMode = Literal["lexical", "literal", "vector", "hybrid"]
RetrievalGuidanceOrientationSource = Literal["authored", "projection_extractive", "none"]
RetrievalGuidanceContentTrust = Literal["untrusted_source_evidence"]
RetrievalGuidanceFallbackMode = Literal["literal", "hybrid", "vector"]

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


class GraphProviderCapabilities(BaseModel):
    backend_kind: str
    query_language: Literal["typed", "cypher", "sql", "sql-pgq"] = "typed"
    persistent: bool = False
    local_only: bool = True
    snapshot_cache: bool = False
    structured_query: bool = True
    raw_query: bool = False
    vector_search: bool = False
    full_text_search: bool = False
    safe_for_default: bool = True
    limitations: list[str] = Field(default_factory=list)


class GraphQueryPath(BaseModel):
    node_ids: list[str] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class GraphQueryRequest(BaseModel):
    operation: GraphQueryOperation = "neighbors"
    start_node_id: str = ""
    target_node_id: str = ""
    source_ref: str = ""
    tag: str = ""
    direction: GraphQueryDirection = "both"
    relation_allowlist: list[str] = Field(default_factory=list)
    max_depth: int = Field(default=1, ge=0, le=8)
    limit: int = Field(default=50, ge=1, le=500)


class GraphQueryResponse(BaseModel):
    provider: GraphProviderCapabilities
    operation: GraphQueryOperation
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    paths: list[GraphQueryPath] = Field(default_factory=list)


class SearchResult(BaseModel):
    page_id: str
    title: str
    path: str
    score: float
    snippet: str
    role: str
    source_refs: list[str] = Field(default_factory=list)
    route: str = ""


class SearchResultProjection(BaseModel):
    page_id: str = ""
    title: str = ""
    path: str = ""
    score: float = 0.0
    snippet: str = ""
    role: str = ""
    source_refs: list[str] = Field(default_factory=list)
    route: str = ""


class WikiPageProjection(BaseModel):
    id: str = ""
    title: str = ""
    path: str = ""
    role: str = ""
    text: str = ""
    summary: str = ""
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    review_state: str = ""
    status: str = ""
    source_refs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    headings: list[str] = Field(default_factory=list)
    updated_at: str = ""


ContextSearchResult: TypeAlias = SearchResult | SearchResultProjection


def _trimmed_unique_strings(
    values: list[str],
    *,
    field_name: str,
    max_item_chars: int,
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"{field_name} entries must be strings")
        text = value.strip()
        if not text:
            raise ValueError(f"{field_name} entries must be non-empty strings")
        if len(text) > max_item_chars:
            raise ValueError(f"{field_name} entries must be at most {max_item_chars} characters")
        key = text.casefold()
        if key in seen:
            raise ValueError(f"{field_name} entries must be unique")
        seen.add(key)
        result.append(text)
    return result


def _safe_relative_path(value: str, *, field_name: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    if len(text) > 1024:
        raise ValueError(f"{field_name} must be at most 1024 characters")
    if "\\" in text or text.startswith("/") or "://" in text:
        raise ValueError(f"{field_name} must be a source-relative path using / separators")
    parts = text.split("/")
    if any(part in {"", ".."} for part in parts):
        raise ValueError(f"{field_name} must not contain empty or .. path segments")
    if len(parts[0]) == 2 and parts[0][1] == ":":
        raise ValueError(f"{field_name} must not be an absolute path")
    return text


class FolderCard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    page_count: int = Field(ge=1)
    terms: list[str] = Field(max_length=8)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _safe_relative_path(value, field_name="path")

    @field_validator("terms")
    @classmethod
    def validate_terms(cls, value: list[str]) -> list[str]:
        return _trimmed_unique_strings(value, field_name="terms", max_item_chars=120)


class PageCard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_id: str = Field(min_length=1, max_length=512)
    title: str = Field(min_length=1, max_length=240)
    path: str
    headings: list[str] = Field(max_length=8)
    terms: list[str] = Field(max_length=12)
    exact_identifiers: list[str] = Field(max_length=8)
    excerpt: str = Field(min_length=1, max_length=240)

    @field_validator("page_id", "title", "excerpt")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("field must be a non-empty string")
        return text

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _safe_relative_path(value, field_name="path")

    @field_validator("headings")
    @classmethod
    def validate_headings(cls, value: list[str]) -> list[str]:
        return _trimmed_unique_strings(value, field_name="headings", max_item_chars=160)

    @field_validator("terms")
    @classmethod
    def validate_terms(cls, value: list[str]) -> list[str]:
        return _trimmed_unique_strings(value, field_name="terms", max_item_chars=120)

    @field_validator("exact_identifiers")
    @classmethod
    def validate_exact_identifiers(cls, value: list[str]) -> list[str]:
        return _trimmed_unique_strings(
            value,
            field_name="exact_identifiers",
            max_item_chars=240,
        )


class RetrievalGuidance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["llmwiki.retrieval_guidance.v1"]
    orientation_source: RetrievalGuidanceOrientationSource
    content_trust: RetrievalGuidanceContentTrust
    max_query_variants: Literal[2]
    character_budget: int = Field(ge=1, le=6000)
    folder_cards: list[FolderCard] = Field(max_length=8)
    page_cards: list[PageCard] = Field(max_length=12)
    suggested_terms: list[str] = Field(max_length=16)
    exact_identifiers: list[str] = Field(max_length=16)
    fallback_modes: list[RetrievalGuidanceFallbackMode]

    @field_validator("suggested_terms")
    @classmethod
    def validate_suggested_terms(cls, value: list[str]) -> list[str]:
        return _trimmed_unique_strings(value, field_name="suggested_terms", max_item_chars=120)

    @field_validator("exact_identifiers")
    @classmethod
    def validate_exact_identifiers(cls, value: list[str]) -> list[str]:
        return _trimmed_unique_strings(
            value,
            field_name="exact_identifiers",
            max_item_chars=240,
        )

    @field_validator("fallback_modes")
    @classmethod
    def validate_fallback_modes(
        cls,
        value: list[RetrievalGuidanceFallbackMode],
    ) -> list[RetrievalGuidanceFallbackMode]:
        ordered = ["literal", "hybrid", "vector"]
        seen: set[str] = set()
        result: list[RetrievalGuidanceFallbackMode] = []
        for mode in value:
            if mode in seen:
                raise ValueError("fallback_modes entries must be unique")
            seen.add(mode)
            result.append(mode)
        if result != [mode for mode in ordered if mode in seen]:
            raise ValueError("fallback_modes must be ordered as literal, hybrid, vector")
        return result


class ContextPack(BaseModel):
    query: str
    wiki_title: str
    description: str = ""
    adapter: str = ""
    implementation: str = ""
    page_count: int = 0
    approved_page_count: int = 0
    answerable: bool
    orientation: list[ContextSearchResult] = Field(default_factory=list)
    evidence: list[ContextSearchResult] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    graph: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    retrieval_guidance: RetrievalGuidance | None = None


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
