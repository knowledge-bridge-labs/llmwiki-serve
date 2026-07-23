from __future__ import annotations

import contextlib
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from . import __version__
from .adapters import WikiRootError
from .io_logging import IoLoggingMiddleware, JsonlIoLogSink, resolve_io_log_path
from .models import (
    ContextPack,
    GraphEdge,
    GraphNeighborhoodDirection,
    GraphNeighborhoodResponse,
    GraphNode,
    ProjectionMetadata,
    SearchResult,
    SourceBundleManifest,
    SourceRefsResponse,
    WikiManifest,
    WikiPage,
)
from .projection_store import ProjectionStore
from .service import DEFAULT_GRAPH_LIMIT, LlmWikiService

QUERY_LIMIT_MIN = 1
QUERY_LIMIT_MAX = 30
DEFAULT_CONTEXT_LIMIT = 8
GRAPH_LIMIT_MIN = 1
GRAPH_LIMIT_MAX = 2_000
GRAPH_NEIGHBOR_DEPTH_MAX = 4
GRAPH_NEIGHBOR_LIMIT_DEFAULT = 50
GRAPH_NEIGHBOR_LIMIT_MAX = 500
GRAPH_NEIGHBOR_SEED_QUERY = Query(default=None)
GRAPH_NEIGHBOR_RELATION_QUERY = Query(default=None)
LOCAL_CORS_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$"
NETWORK_MANIFEST_ROOT = ""
API_VERSION = __version__
MCP_UNSUPPORTED_METHOD_MESSAGE = "Unsupported MCP-style method."
MCP_UNKNOWN_TOOL_MESSAGE = "Unknown MCP-style tool."
MCP_INTERNAL_FAILURE_MESSAGE = "Internal MCP-style error."
MCP_STREAM_PATH = "/mcp/stream"
MCP_STREAM_MOUNT_PATH = "/mcp"
DEFAULT_MCP_SERVER_NAME = "LLMWiki Serve"
DEFAULT_MCP_INSTRUCTIONS = (
    "Read approved LLMWiki context packs, search results, pages, and graph data."
)
MCP_TOOL_BASE_DESCRIPTIONS = {
    "llmwiki_context": (
        "Build a context pack with wiki metadata, hot/index/overview or OpenWiki "
        "quickstart orientation first, then query-ranked citation evidence."
    ),
    "llmwiki_search": "Search approved LLMWiki pages.",
    "llmwiki_read": "Read a page by id or path.",
    "llmwiki_graph": "Return page/link/source graph.",
    "llmwiki_graph_neighbors": (
        "Return a bounded graph neighborhood around page, source, tag, or sidecar "
        "graph seed nodes for dependency and lineage inspection."
    ),
    "llmwiki_source_refs": "Return typed source-reference handles linked from approved pages.",
    "llmwiki_source_bundle": (
        "Return the source bundle manifest with typed source-reference handles."
    ),
}


@dataclass(frozen=True)
class McpSurfaceMetadata:
    server_name: str
    instructions: str
    tool_descriptions: dict[str, str]


class UnsupportedMcpMethodError(Exception):
    pass


class UnknownMcpToolError(Exception):
    pass


class QueryRequest(BaseModel):
    query: str = ""
    limit: int | None = Field(default=None, ge=1, le=30)
    include_drafts: bool = False


class ReadRequest(BaseModel):
    page_id: str
    include_drafts: bool = False


class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str
    params: dict[str, Any] | None = Field(default_factory=dict)


class HealthSourceResponse(BaseModel):
    source_id: str = ""
    bundle_id: str = ""
    public_uri: str = ""
    title: str = ""
    adapter: str = ""
    implementation: str = ""
    page_count: int = 0
    approved_page_count: int = 0
    projection: ProjectionMetadata = Field(default_factory=ProjectionMetadata)


class HealthEndpointsResponse(BaseModel):
    health: str
    manifest: str
    source_bundle: str
    source_refs: str
    query: str
    search: str
    read: str
    graph: str
    graph_neighborhood: str
    mcp_jsonrpc: str
    mcp_streamable_http: str
    openapi: str
    docs: str
    a2a_agent_card: str
    a2a_message_send: str


class HealthCorsResponse(BaseModel):
    mode: Literal["local-dev-allowlist", "explicit-allowlist"]
    local_dev_origins: bool
    explicit_origin_count: int = 0


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: Literal["llmwiki-serve"]
    version: str
    source: HealthSourceResponse
    capabilities: list[str]
    endpoints: HealthEndpointsResponse
    cors: HealthCorsResponse


class ProjectionStoreDiagnosticsResponse(BaseModel):
    backend: str
    backend_kind: Literal["memory", "redis"]
    endpoint: str | None
    namespace: str
    cache_source_id: str
    available: bool
    last_error: str = ""


class SearchResponse(BaseModel):
    results: list[SearchResult] = Field(default_factory=list)


class GraphResponse(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class ReadNotFoundResponse(BaseModel):
    found: Literal[False]
    reason: str = ""


class HttpDetailResponse(BaseModel):
    detail: str


class AgentCardCapabilities(BaseModel):
    streaming: bool
    pushNotifications: bool


class AgentCardResponse(BaseModel):
    name: str
    description: str
    url: str
    version: str
    capabilities: AgentCardCapabilities


class JsonRpcErrorPayload(BaseModel):
    code: int
    message: str


class JsonRpcResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    result: Any | None = None
    error: JsonRpcErrorPayload | None = None


class A2APart(BaseModel):
    kind: str
    text: str | None = None
    data: dict[str, Any] | None = None


class A2AMessage(BaseModel):
    role: str
    parts: list[A2APart] = Field(default_factory=list)


class A2AArtifact(BaseModel):
    name: str
    parts: list[A2APart] = Field(default_factory=list)


class A2AResponse(BaseModel):
    status: str
    message: A2AMessage
    artifacts: list[A2AArtifact] = Field(default_factory=list)


def create_app(
    root: Path | str,
    *,
    allow_drafts: bool = False,
    cors_origins: Sequence[str] | None = None,
    enable_a2a_compat: bool = False,
    refresh_interval_seconds: float = 0.0,
    producer_manifest_path: Path | str | None = None,
    io_log: Path | str | bool | None = None,
    projection_store: ProjectionStore | None = None,
    cache_namespace: str = "default",
    source_id: str | None = None,
    graph_default_limit: int | None = None,
    context_default_limit: int | None = None,
    mcp_server_name: str | None = None,
    mcp_instructions: str | None = None,
    mcp_tool_description_prefix: str | None = None,
) -> FastAPI:
    resolved_graph_default_limit = validate_default_limit(
        graph_default_limit,
        name="graph_default_limit",
        default=DEFAULT_GRAPH_LIMIT,
        minimum=GRAPH_LIMIT_MIN,
        maximum=GRAPH_LIMIT_MAX,
    )
    resolved_context_default_limit = validate_default_limit(
        context_default_limit,
        name="context_default_limit",
        default=DEFAULT_CONTEXT_LIMIT,
        minimum=QUERY_LIMIT_MIN,
        maximum=QUERY_LIMIT_MAX,
    )
    service = LlmWikiService(
        root,
        refresh_interval_seconds=refresh_interval_seconds,
        producer_manifest_path=producer_manifest_path,
        projection_store=projection_store,
        cache_namespace=cache_namespace,
        source_id=source_id,
    )
    mcp_stream = create_mcp_stream_server(
        service,
        allow_drafts=allow_drafts,
        enable_a2a_compat=enable_a2a_compat,
        graph_default_limit=resolved_graph_default_limit,
        context_default_limit=resolved_context_default_limit,
        mcp_server_name=mcp_server_name,
        mcp_instructions=mcp_instructions,
        mcp_tool_description_prefix=mcp_tool_description_prefix,
    )
    mcp_stream_app = mcp_stream.streamable_http_app()
    explicit_origins = set(cors_origins or [])

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with mcp_stream.session_manager.run():
            yield

    app = FastAPI(
        title="LLMWiki Serve",
        version=API_VERSION,
        description=(
            "Read-only HTTP, MCP-style JSON-RPC, MCP Streamable HTTP, and optional "
            "A2A-style message surface "
            "for LLMWiki Markdown folders."
        ),
        lifespan=lifespan,
    )
    cors_kwargs: dict[str, Any] = {
        "allow_origins": list(explicit_origins),
        "allow_methods": ["GET", "POST", "DELETE", "OPTIONS"],
        "allow_headers": ["*"],
        "expose_headers": ["Mcp-Session-Id"],
    }
    if not explicit_origins:
        cors_kwargs["allow_origin_regex"] = LOCAL_CORS_ORIGIN_REGEX
    app.add_middleware(
        CORSMiddleware,
        **cors_kwargs,
    )

    @app.middleware("http")
    async def validate_origin_header(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        origin = request.headers.get("origin")
        if origin and not is_allowed_origin(origin, explicit_origins):
            return JSONResponse(
                status_code=403,
                content={"detail": "origin not allowed"},
            )
        return await call_next(request)

    @app.exception_handler(WikiRootError)
    async def wiki_root_error_handler(_request: Request, exc: WikiRootError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.safe_message}},
        )

    @app.get("/health", response_model=HealthResponse)
    def health() -> dict[str, Any]:
        manifest_data = service.manifest(enable_a2a_compat=enable_a2a_compat)
        return {
            "status": "ok",
            "service": "llmwiki-serve",
            "version": API_VERSION,
            "source": {
                "source_id": manifest_data.source_id,
                "bundle_id": manifest_data.bundle_id,
                "public_uri": manifest_data.public_uri,
                "title": manifest_data.title,
                "adapter": manifest_data.adapter,
                "implementation": manifest_data.implementation,
                "page_count": manifest_data.page_count,
                "approved_page_count": manifest_data.approved_page_count,
                "projection": manifest_data.projection.model_dump(),
            },
            "capabilities": manifest_data.capabilities,
            "endpoints": health_endpoints(enable_a2a_compat).model_dump(),
            "cors": health_cors(explicit_origins).model_dump(),
        }

    @app.get(
        "/diagnostics/projection-store",
        response_model=ProjectionStoreDiagnosticsResponse,
    )
    def projection_store_diagnostics() -> dict[str, Any]:
        return service.projection_store_diagnostics()

    @app.get("/manifest", response_model=WikiManifest)
    def manifest() -> dict[str, Any]:
        manifest_data = service.manifest(enable_a2a_compat=enable_a2a_compat).model_dump()
        manifest_data["root"] = NETWORK_MANIFEST_ROOT
        return manifest_data

    @app.get("/source-refs", response_model=SourceRefsResponse)
    def source_refs(include_drafts: bool = False) -> dict[str, Any]:
        return service.source_refs(
            include_drafts=network_include_drafts(allow_drafts, include_drafts),
        ).model_dump()

    @app.get("/source-bundle", response_model=SourceBundleManifest)
    def source_bundle(include_drafts: bool = False) -> dict[str, Any]:
        return service.source_bundle(
            include_drafts=network_include_drafts(allow_drafts, include_drafts),
            enable_a2a_compat=enable_a2a_compat,
        ).model_dump()

    @app.post("/query", response_model=ContextPack)
    def query(request: QueryRequest) -> dict[str, Any]:
        return service.context(
            request.query,
            limit=clamp_int(
                request.limit,
                default=resolved_context_default_limit,
                minimum=QUERY_LIMIT_MIN,
                maximum=QUERY_LIMIT_MAX,
            ),
            include_drafts=network_include_drafts(allow_drafts, request.include_drafts),
        ).model_dump()

    @app.post("/search", response_model=SearchResponse)
    def search(request: QueryRequest) -> dict[str, Any]:
        return {
            "results": service.search(
                request.query,
                limit=clamp_int(
                    request.limit,
                    default=resolved_context_default_limit,
                    minimum=QUERY_LIMIT_MIN,
                    maximum=QUERY_LIMIT_MAX,
                ),
                include_drafts=network_include_drafts(allow_drafts, request.include_drafts),
            )
        }

    @app.get(
        "/read/{page_id:path}",
        response_model=WikiPage | ReadNotFoundResponse,
        responses={404: {"model": HttpDetailResponse}},
    )
    def read(page_id: str, include_drafts: bool = False) -> dict[str, Any]:
        result = service.read(
            page_id,
            include_drafts=network_include_drafts(allow_drafts, include_drafts),
        )
        if not result.get("found", True) and result.get("reason") != "not approved for serving":
            raise HTTPException(status_code=404, detail="page not found")
        return result

    @app.get("/graph", response_model=GraphResponse)
    def graph(
        limit: int | None = Query(
            default=None,
            description=(
                "Maximum graph nodes. Omitting this value uses the server-configured "
                f"default of {resolved_graph_default_limit}. Explicit numeric values are "
                f"clamped to {GRAPH_LIMIT_MIN}..{GRAPH_LIMIT_MAX}."
            ),
        ),
        include_drafts: bool = False,
    ) -> dict[str, Any]:
        return service.graph(
            limit=clamp_int(
                limit,
                default=resolved_graph_default_limit,
                minimum=GRAPH_LIMIT_MIN,
                maximum=GRAPH_LIMIT_MAX,
            ),
            include_drafts=network_include_drafts(allow_drafts, include_drafts),
        )

    @app.get("/graph/neighborhood", response_model=GraphNeighborhoodResponse)
    def graph_neighborhood(
        seed: list[str] | None = GRAPH_NEIGHBOR_SEED_QUERY,
        depth: int = 1,
        direction: GraphNeighborhoodDirection = "both",
        relation: list[str] | None = GRAPH_NEIGHBOR_RELATION_QUERY,
        limit: int = GRAPH_NEIGHBOR_LIMIT_DEFAULT,
        include_drafts: bool = False,
    ) -> dict[str, Any]:
        return service.graph_neighbors(
            seeds=seed or [],
            depth=clamp_int(depth, default=1, minimum=0, maximum=GRAPH_NEIGHBOR_DEPTH_MAX),
            direction=direction,
            relations=relation or [],
            limit=clamp_int(
                limit,
                default=GRAPH_NEIGHBOR_LIMIT_DEFAULT,
                minimum=GRAPH_LIMIT_MIN,
                maximum=GRAPH_NEIGHBOR_LIMIT_MAX,
            ),
            include_drafts=network_include_drafts(allow_drafts, include_drafts),
        ).model_dump()

    @app.post("/mcp", response_model=JsonRpcResponse, response_model_exclude_none=True)
    def mcp(request: JsonRpcRequest) -> dict[str, Any]:
        try:
            result = handle_mcp(
                service,
                request.method,
                request.params,
                allow_drafts=allow_drafts,
                enable_a2a_compat=enable_a2a_compat,
                graph_default_limit=resolved_graph_default_limit,
                context_default_limit=resolved_context_default_limit,
                mcp_server_name=mcp_server_name,
                mcp_instructions=mcp_instructions,
                mcp_tool_description_prefix=mcp_tool_description_prefix,
            )
            return {"jsonrpc": "2.0", "id": request.id, "result": result}
        except UnsupportedMcpMethodError:
            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "error": {"code": -32601, "message": MCP_UNSUPPORTED_METHOD_MESSAGE},
            }
        except UnknownMcpToolError:
            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "error": {"code": -32602, "message": MCP_UNKNOWN_TOOL_MESSAGE},
            }
        except Exception:
            return {
                "jsonrpc": "2.0",
                "id": request.id,
                "error": {"code": -32000, "message": MCP_INTERNAL_FAILURE_MESSAGE},
            }

    if enable_a2a_compat:

        @app.get("/.well-known/agent-card.json", response_model=AgentCardResponse)
        def agent_card() -> dict[str, Any]:
            manifest_data = service.manifest(enable_a2a_compat=True)
            return {
                "name": manifest_data.title,
                "description": manifest_data.description or "LLMWiki Serve A2A endpoint",
                "url": "/message:send",
                "version": API_VERSION,
                "capabilities": {"streaming": False, "pushNotifications": False},
            }

        @app.post("/message:send", response_model=A2AResponse, response_model_exclude_none=True)
        def message_send(payload: dict[str, Any]) -> dict[str, Any]:
            query_text = extract_a2a_query(payload)
            context = service.context(query_text, limit=resolved_context_default_limit)
            return {
                "status": "completed",
                "message": {
                    "role": "agent",
                    "parts": [{"kind": "text", "text": render_a2a_text(context.model_dump())}],
                },
                "artifacts": [
                    {
                        "name": "llmwiki_context",
                        "parts": [{"kind": "data", "data": context.model_dump()}],
                    }
                ],
            }

    app.mount(MCP_STREAM_MOUNT_PATH, mcp_stream_app)

    io_log_path = resolve_io_log_path(io_log)
    if io_log_path is not None:
        app.add_middleware(
            IoLoggingMiddleware,
            sink=JsonlIoLogSink(io_log_path, local_roots=[service.root]),
        )

    return app


def health_endpoints(enable_a2a_compat: bool) -> HealthEndpointsResponse:
    return HealthEndpointsResponse(
        health="/health",
        manifest="/manifest",
        source_bundle="/source-bundle",
        source_refs="/source-refs",
        query="/query",
        search="/search",
        read="/read/{page_id}",
        graph="/graph",
        graph_neighborhood="/graph/neighborhood",
        mcp_jsonrpc="/mcp",
        mcp_streamable_http=MCP_STREAM_PATH,
        openapi="/openapi.json",
        docs="/docs",
        a2a_agent_card="/.well-known/agent-card.json" if enable_a2a_compat else "",
        a2a_message_send="/message:send" if enable_a2a_compat else "",
    )


def health_cors(explicit_origins: set[str]) -> HealthCorsResponse:
    return HealthCorsResponse(
        mode="explicit-allowlist" if explicit_origins else "local-dev-allowlist",
        local_dev_origins=not explicit_origins,
        explicit_origin_count=len(explicit_origins),
    )


def mcp_surface_metadata(
    service: LlmWikiService,
    *,
    enable_a2a_compat: bool = False,
    graph_default_limit: int | None = None,
    context_default_limit: int | None = None,
    mcp_server_name: str | None = None,
    mcp_instructions: str | None = None,
    mcp_tool_description_prefix: str | None = None,
) -> McpSurfaceMetadata:
    resolved_graph_default_limit = validate_default_limit(
        graph_default_limit,
        name="graph_default_limit",
        default=DEFAULT_GRAPH_LIMIT,
        minimum=GRAPH_LIMIT_MIN,
        maximum=GRAPH_LIMIT_MAX,
    )
    resolved_context_default_limit = validate_default_limit(
        context_default_limit,
        name="context_default_limit",
        default=DEFAULT_CONTEXT_LIMIT,
        minimum=QUERY_LIMIT_MIN,
        maximum=QUERY_LIMIT_MAX,
    )
    try:
        manifest = service.manifest(enable_a2a_compat=enable_a2a_compat)
    except Exception:
        manifest = None
    server_name = resolved_mcp_server_name(manifest, mcp_server_name)
    instructions = resolved_mcp_instructions(manifest, mcp_instructions)
    tool_prefix = resolved_mcp_tool_description_prefix(
        manifest,
        server_name=server_name,
        server_name_override=mcp_server_name,
        override=mcp_tool_description_prefix,
    )
    base_descriptions = mcp_tool_descriptions(
        context_default_limit=resolved_context_default_limit,
        graph_default_limit=resolved_graph_default_limit,
    )
    tool_descriptions = {
        name: f"{tool_prefix}{description}" for name, description in base_descriptions.items()
    }
    return McpSurfaceMetadata(
        server_name=server_name,
        instructions=instructions,
        tool_descriptions=tool_descriptions,
    )


def resolved_mcp_server_name(
    manifest: WikiManifest | None,
    override: str | None,
) -> str:
    override_text = normalized_nonempty_text(override)
    if override_text:
        return override_text
    if manifest is None:
        return DEFAULT_MCP_SERVER_NAME
    scope_title = normalized_nonempty_text(manifest.title)
    if scope_title:
        return f"{scope_title} - LLMWiki Serve"
    source_id = normalized_nonempty_text(manifest.source_id)
    if source_id:
        return f"{source_id} - LLMWiki Serve"
    return DEFAULT_MCP_SERVER_NAME


def resolved_mcp_instructions(
    manifest: WikiManifest | None,
    override: str | None,
) -> str:
    override_text = normalized_nonempty_text(override)
    if override_text:
        return override_text
    if manifest is None:
        return DEFAULT_MCP_INSTRUCTIONS

    title = normalized_nonempty_text(manifest.title) or "this LLMWiki source"
    pieces = [
        (
            f'Use this MCP server only for the served wiki "{title}". '
            "It provides read-only approved context packs, search, page reads, graph data, "
            "source references, and source-bundle metadata."
        )
    ]
    description = normalized_nonempty_text(manifest.description)
    if description:
        pieces.append(f"Wiki description: {description}.")
    identity = mcp_source_identity(manifest)
    if identity:
        pieces.append(f"Source identity: {identity}.")
    pieces.append("For unrelated questions, use another source instead of these scoped tools.")
    return " ".join(pieces)


def resolved_mcp_tool_description_prefix(
    manifest: WikiManifest | None,
    *,
    server_name: str,
    server_name_override: str | None,
    override: str | None,
) -> str:
    if override is not None:
        override_text = normalized_inline_text(override)
        if override_text and not override_text.endswith(" "):
            return f"{override_text} "
        return override_text

    label = mcp_tool_scope_label(
        manifest,
        server_name=server_name,
        server_name_override=server_name_override,
    )
    return f"[{label}] " if label else ""


def mcp_tool_scope_label(
    manifest: WikiManifest | None,
    *,
    server_name: str,
    server_name_override: str | None,
) -> str:
    override_title = normalized_nonempty_text(server_name_override)
    if manifest is None:
        if override_title:
            return override_title
        if server_name != DEFAULT_MCP_SERVER_NAME:
            return server_name
        return ""

    title = (
        override_title
        or normalized_nonempty_text(manifest.title)
        or normalized_nonempty_text(server_name)
    )
    source_id = normalized_nonempty_text(manifest.source_id)
    if title and source_id and source_id not in title:
        return f"{title} | source_id: {source_id}"
    return title or source_id or ""


def mcp_source_identity(manifest: WikiManifest) -> str:
    parts = []
    for label, value in (
        ("source_id", manifest.source_id),
        ("public_uri", manifest.public_uri),
        ("adapter", manifest.adapter),
        ("implementation", manifest.implementation),
    ):
        text = normalized_nonempty_text(value)
        if text:
            parts.append(f"{label}={text}")
    return ", ".join(parts)


def normalized_nonempty_text(value: str | None) -> str:
    return normalized_inline_text(value or "")


def normalized_inline_text(value: str) -> str:
    return " ".join(value.strip().split())


def mcp_tool_descriptions(
    *,
    context_default_limit: int,
    graph_default_limit: int,
) -> dict[str, str]:
    descriptions = dict(MCP_TOOL_BASE_DESCRIPTIONS)
    descriptions["llmwiki_context"] = (
        f"{MCP_TOOL_BASE_DESCRIPTIONS['llmwiki_context']} "
        f"Default limit: {context_default_limit} evidence item(s); maximum {QUERY_LIMIT_MAX}."
    )
    descriptions["llmwiki_search"] = (
        f"{MCP_TOOL_BASE_DESCRIPTIONS['llmwiki_search']} "
        f"Default limit: {context_default_limit} result(s); maximum {QUERY_LIMIT_MAX}."
    )
    descriptions["llmwiki_graph"] = (
        f"{MCP_TOOL_BASE_DESCRIPTIONS['llmwiki_graph']} "
        f"Default limit: {graph_default_limit} node(s); explicit limit maximum "
        f"{GRAPH_LIMIT_MAX}. Large full-graph payloads can be sizable; prefer "
        "llmwiki_graph_neighbors for focused inspection."
    )
    return descriptions


def create_mcp_stream_server(
    service: LlmWikiService,
    *,
    allow_drafts: bool = False,
    enable_a2a_compat: bool = False,
    graph_default_limit: int | None = None,
    context_default_limit: int | None = None,
    mcp_server_name: str | None = None,
    mcp_instructions: str | None = None,
    mcp_tool_description_prefix: str | None = None,
) -> FastMCP:
    resolved_graph_default_limit = validate_default_limit(
        graph_default_limit,
        name="graph_default_limit",
        default=DEFAULT_GRAPH_LIMIT,
        minimum=GRAPH_LIMIT_MIN,
        maximum=GRAPH_LIMIT_MAX,
    )
    resolved_context_default_limit = validate_default_limit(
        context_default_limit,
        name="context_default_limit",
        default=DEFAULT_CONTEXT_LIMIT,
        minimum=QUERY_LIMIT_MIN,
        maximum=QUERY_LIMIT_MAX,
    )
    metadata = mcp_surface_metadata(
        service,
        enable_a2a_compat=enable_a2a_compat,
        graph_default_limit=resolved_graph_default_limit,
        context_default_limit=resolved_context_default_limit,
        mcp_server_name=mcp_server_name,
        mcp_instructions=mcp_instructions,
        mcp_tool_description_prefix=mcp_tool_description_prefix,
    )
    mcp_stream = FastMCP(
        metadata.server_name,
        instructions=metadata.instructions,
        stateless_http=True,
        json_response=True,
        streamable_http_path="/stream",
    )

    @mcp_stream.tool(
        name="llmwiki_context",
        description=metadata.tool_descriptions["llmwiki_context"],
    )
    def llmwiki_context(
        query: str = "",
        limit: int = resolved_context_default_limit,
        include_drafts: bool = False,
    ) -> dict[str, Any]:
        try:
            return service.context(
                query,
                limit=clamp_int(
                    limit,
                    default=resolved_context_default_limit,
                    minimum=QUERY_LIMIT_MIN,
                    maximum=QUERY_LIMIT_MAX,
                ),
                include_drafts=network_include_drafts(allow_drafts, include_drafts),
            ).model_dump()
        except Exception as exc:
            raise ToolError(MCP_INTERNAL_FAILURE_MESSAGE) from exc

    @mcp_stream.tool(
        name="llmwiki_search",
        description=metadata.tool_descriptions["llmwiki_search"],
    )
    def llmwiki_search(
        query: str = "",
        limit: int = resolved_context_default_limit,
        include_drafts: bool = False,
    ) -> dict[str, Any]:
        try:
            return {
                "results": service.search(
                    query,
                    limit=clamp_int(
                        limit,
                        default=resolved_context_default_limit,
                        minimum=QUERY_LIMIT_MIN,
                        maximum=QUERY_LIMIT_MAX,
                    ),
                    include_drafts=network_include_drafts(allow_drafts, include_drafts),
                )
            }
        except Exception as exc:
            raise ToolError(MCP_INTERNAL_FAILURE_MESSAGE) from exc

    @mcp_stream.tool(
        name="llmwiki_read",
        description=metadata.tool_descriptions["llmwiki_read"],
    )
    def llmwiki_read(page_id: str, include_drafts: bool = False) -> dict[str, Any]:
        try:
            return service.read(
                page_id,
                include_drafts=network_include_drafts(allow_drafts, include_drafts),
            )
        except Exception as exc:
            raise ToolError(MCP_INTERNAL_FAILURE_MESSAGE) from exc

    @mcp_stream.tool(
        name="llmwiki_graph",
        description=metadata.tool_descriptions["llmwiki_graph"],
    )
    def llmwiki_graph(
        limit: int = resolved_graph_default_limit,
        include_drafts: bool = False,
    ) -> dict[str, Any]:
        try:
            return service.graph(
                limit=clamp_int(
                    limit,
                    default=resolved_graph_default_limit,
                    minimum=GRAPH_LIMIT_MIN,
                    maximum=GRAPH_LIMIT_MAX,
                ),
                include_drafts=network_include_drafts(allow_drafts, include_drafts),
            )
        except Exception as exc:
            raise ToolError(MCP_INTERNAL_FAILURE_MESSAGE) from exc

    @mcp_stream.tool(
        name="llmwiki_graph_neighbors",
        description=metadata.tool_descriptions["llmwiki_graph_neighbors"],
    )
    def llmwiki_graph_neighbors(
        seed: str = "",
        seeds: list[str] | None = None,
        depth: int = 1,
        direction: GraphNeighborhoodDirection = "both",
        relation: str = "",
        relations: list[str] | None = None,
        limit: int = GRAPH_NEIGHBOR_LIMIT_DEFAULT,
        include_drafts: bool = False,
    ) -> dict[str, Any]:
        try:
            return service.graph_neighbors(
                seeds=collect_string_args(seed, seeds),
                depth=clamp_int(depth, default=1, minimum=0, maximum=GRAPH_NEIGHBOR_DEPTH_MAX),
                direction=direction,
                relations=collect_string_args(relation, relations),
                limit=clamp_int(
                    limit,
                    default=GRAPH_NEIGHBOR_LIMIT_DEFAULT,
                    minimum=GRAPH_LIMIT_MIN,
                    maximum=GRAPH_NEIGHBOR_LIMIT_MAX,
                ),
                include_drafts=network_include_drafts(allow_drafts, include_drafts),
            ).model_dump()
        except Exception as exc:
            raise ToolError(MCP_INTERNAL_FAILURE_MESSAGE) from exc

    @mcp_stream.tool(
        name="llmwiki_source_refs",
        description=metadata.tool_descriptions["llmwiki_source_refs"],
    )
    def llmwiki_source_refs(include_drafts: bool = False) -> dict[str, Any]:
        try:
            return service.source_refs(
                include_drafts=network_include_drafts(allow_drafts, include_drafts),
            ).model_dump()
        except Exception as exc:
            raise ToolError(MCP_INTERNAL_FAILURE_MESSAGE) from exc

    @mcp_stream.tool(
        name="llmwiki_source_bundle",
        description=metadata.tool_descriptions["llmwiki_source_bundle"],
    )
    def llmwiki_source_bundle(include_drafts: bool = False) -> dict[str, Any]:
        try:
            return service.source_bundle(
                include_drafts=network_include_drafts(allow_drafts, include_drafts),
                enable_a2a_compat=enable_a2a_compat,
            ).model_dump()
        except Exception as exc:
            raise ToolError(MCP_INTERNAL_FAILURE_MESSAGE) from exc

    return mcp_stream


def is_allowed_origin(origin: str, explicit_origins: set[str]) -> bool:
    if explicit_origins:
        return origin in explicit_origins
    return re.fullmatch(LOCAL_CORS_ORIGIN_REGEX, origin) is not None


def handle_mcp(
    service: LlmWikiService,
    method: str,
    params: dict[str, Any] | None,
    *,
    allow_drafts: bool = False,
    enable_a2a_compat: bool = False,
    graph_default_limit: int | None = None,
    context_default_limit: int | None = None,
    mcp_server_name: str | None = None,
    mcp_instructions: str | None = None,
    mcp_tool_description_prefix: str | None = None,
) -> Any:
    params = params or {}
    resolved_graph_default_limit = validate_default_limit(
        graph_default_limit,
        name="graph_default_limit",
        default=DEFAULT_GRAPH_LIMIT,
        minimum=GRAPH_LIMIT_MIN,
        maximum=GRAPH_LIMIT_MAX,
    )
    resolved_context_default_limit = validate_default_limit(
        context_default_limit,
        name="context_default_limit",
        default=DEFAULT_CONTEXT_LIMIT,
        minimum=QUERY_LIMIT_MIN,
        maximum=QUERY_LIMIT_MAX,
    )
    if method == "tools/list":
        metadata = mcp_surface_metadata(
            service,
            enable_a2a_compat=enable_a2a_compat,
            graph_default_limit=resolved_graph_default_limit,
            context_default_limit=resolved_context_default_limit,
            mcp_server_name=mcp_server_name,
            mcp_instructions=mcp_instructions,
            mcp_tool_description_prefix=mcp_tool_description_prefix,
        )
        return {
            "tools": [
                {"name": name, "description": description}
                for name, description in metadata.tool_descriptions.items()
            ]
        }
    if method != "tools/call":
        raise UnsupportedMcpMethodError
    name = str(params.get("name") or "")
    raw_args = params.get("arguments")
    args = raw_args if isinstance(raw_args, dict) else {}
    if name == "llmwiki_context":
        return service.context(
            str(args.get("query") or ""),
            limit=clamp_int(
                args.get("limit"),
                default=resolved_context_default_limit,
                minimum=QUERY_LIMIT_MIN,
                maximum=QUERY_LIMIT_MAX,
            ),
            include_drafts=network_include_drafts(allow_drafts, args.get("include_drafts")),
        ).model_dump()
    if name == "llmwiki_search":
        return {
            "results": service.search(
                str(args.get("query") or ""),
                limit=clamp_int(
                    args.get("limit"),
                    default=resolved_context_default_limit,
                    minimum=QUERY_LIMIT_MIN,
                    maximum=QUERY_LIMIT_MAX,
                ),
                include_drafts=network_include_drafts(allow_drafts, args.get("include_drafts")),
            )
        }
    if name == "llmwiki_read":
        return service.read(
            str(args.get("page_id") or args.get("id") or ""),
            include_drafts=network_include_drafts(allow_drafts, args.get("include_drafts")),
        )
    if name == "llmwiki_graph":
        return service.graph(
            limit=clamp_int(
                args.get("limit"),
                default=resolved_graph_default_limit,
                minimum=GRAPH_LIMIT_MIN,
                maximum=GRAPH_LIMIT_MAX,
            ),
            include_drafts=network_include_drafts(allow_drafts, args.get("include_drafts")),
        )
    if name == "llmwiki_graph_neighbors":
        return service.graph_neighbors(
            seeds=collect_string_args(args.get("seed"), args.get("seeds")),
            depth=clamp_int(
                args.get("depth"), default=1, minimum=0, maximum=GRAPH_NEIGHBOR_DEPTH_MAX
            ),
            direction=graph_direction_arg(args.get("direction")),
            relations=collect_string_args(args.get("relation"), args.get("relations")),
            limit=clamp_int(
                args.get("limit"),
                default=GRAPH_NEIGHBOR_LIMIT_DEFAULT,
                minimum=GRAPH_LIMIT_MIN,
                maximum=GRAPH_NEIGHBOR_LIMIT_MAX,
            ),
            include_drafts=network_include_drafts(allow_drafts, args.get("include_drafts")),
        ).model_dump()
    if name == "llmwiki_source_refs":
        return service.source_refs(
            include_drafts=network_include_drafts(allow_drafts, args.get("include_drafts")),
        ).model_dump()
    if name == "llmwiki_source_bundle":
        return service.source_bundle(
            include_drafts=network_include_drafts(allow_drafts, args.get("include_drafts")),
            enable_a2a_compat=enable_a2a_compat,
        ).model_dump()
    raise UnknownMcpToolError


def clamp_int(value: Any, *, default: int = 0, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value if value is not None else default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def validate_default_limit(
    value: Any,
    *,
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def bool_arg(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def collect_string_args(*values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        if isinstance(value, str):
            candidates = [item.strip() for item in value.split(",")]
        elif isinstance(value, list):
            candidates = [str(item).strip() for item in value]
        else:
            candidates = []
        for candidate in candidates:
            if candidate and candidate not in result:
                result.append(candidate)
    return result


def graph_direction_arg(value: Any) -> GraphNeighborhoodDirection:
    normalized = str(value or "both").strip().lower()
    if normalized in {"out", "in", "both"}:
        return cast(GraphNeighborhoodDirection, normalized)
    return "both"


def network_include_drafts(allow_drafts: bool, requested: Any) -> bool:
    return allow_drafts and bool_arg(requested)


def extract_a2a_query(payload: dict[str, Any]) -> str:
    data = payload.get("data")
    if isinstance(data, dict) and data.get("query"):
        return str(data["query"])
    if payload.get("text"):
        return str(payload["text"])
    message = payload.get("message")
    if isinstance(message, dict):
        parts = message.get("parts")
        if isinstance(parts, list):
            return " ".join(
                str(part.get("text") or "") for part in parts if isinstance(part, dict)
            ).strip()
    return ""


def render_a2a_text(context: dict[str, Any]) -> str:
    orientation = context.get("orientation") or []
    evidence = context.get("evidence") or []
    if not orientation and not evidence:
        return "No approved LLMWiki evidence matched the request."
    lines = [f"{context.get('wiki_title', 'LLMWiki')} context:"]
    if orientation:
        lines.append("Orientation:")
        for index, item in enumerate(orientation[:3], start=1):
            lines.append(
                f"[{index}] {item.get('title')} ({item.get('role', 'orientation')}) - "
                f"{item.get('snippet')}"
            )
    if evidence:
        lines.append("Evidence:")
    for index, item in enumerate(evidence[:5], start=1):
        lines.append(f"[{index}] {item.get('title')} - {item.get('snippet')}")
    return "\n".join(lines)
