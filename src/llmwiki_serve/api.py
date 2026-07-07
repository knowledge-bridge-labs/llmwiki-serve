from __future__ import annotations

import contextlib
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from .adapters import WikiRootError
from .models import (
    ContextPack,
    GraphEdge,
    GraphNode,
    SearchResult,
    SourceBundleManifest,
    SourceRefsResponse,
    WikiManifest,
    WikiPage,
)
from .service import LlmWikiService

QUERY_LIMIT_MIN = 1
QUERY_LIMIT_MAX = 30
GRAPH_LIMIT_MIN = 1
GRAPH_LIMIT_MAX = 2_000
LOCAL_CORS_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$"
NETWORK_MANIFEST_ROOT = ""
MCP_UNSUPPORTED_METHOD_MESSAGE = "Unsupported MCP-style method."
MCP_UNKNOWN_TOOL_MESSAGE = "Unknown MCP-style tool."
MCP_INTERNAL_FAILURE_MESSAGE = "Internal MCP-style error."
MCP_STREAM_PATH = "/mcp/stream"
MCP_STREAM_MOUNT_PATH = "/mcp"


class UnsupportedMcpMethodError(Exception):
    pass


class UnknownMcpToolError(Exception):
    pass


class QueryRequest(BaseModel):
    query: str = ""
    limit: int = Field(default=8, ge=1, le=30)
    include_drafts: bool = False


class ReadRequest(BaseModel):
    page_id: str
    include_drafts: bool = False


class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str
    params: dict[str, Any] | None = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: Literal["ok"]


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
) -> FastAPI:
    service = LlmWikiService(root)
    mcp_stream = create_mcp_stream_server(
        service,
        allow_drafts=allow_drafts,
        enable_a2a_compat=enable_a2a_compat,
    )
    mcp_stream_app = mcp_stream.streamable_http_app()
    explicit_origins = set(cors_origins or [])

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with mcp_stream.session_manager.run():
            yield

    app = FastAPI(
        title="LLMWiki Serve",
        version="0.1.0",
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
    def health() -> dict[str, str]:
        service.index()
        return {"status": "ok"}

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
            limit=request.limit,
            include_drafts=network_include_drafts(allow_drafts, request.include_drafts),
        ).model_dump()

    @app.post("/search", response_model=SearchResponse)
    def search(request: QueryRequest) -> dict[str, Any]:
        return {
            "results": service.search(
                request.query,
                limit=request.limit,
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
    def graph(limit: int = 500, include_drafts: bool = False) -> dict[str, Any]:
        return service.graph(
            limit=clamp_int(limit, minimum=GRAPH_LIMIT_MIN, maximum=GRAPH_LIMIT_MAX),
            include_drafts=network_include_drafts(allow_drafts, include_drafts),
        )

    @app.post("/mcp", response_model=JsonRpcResponse, response_model_exclude_none=True)
    def mcp(request: JsonRpcRequest) -> dict[str, Any]:
        try:
            result = handle_mcp(
                service,
                request.method,
                request.params,
                allow_drafts=allow_drafts,
                enable_a2a_compat=enable_a2a_compat,
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
                "version": "0.1.0",
                "capabilities": {"streaming": False, "pushNotifications": False},
            }

        @app.post("/message:send", response_model=A2AResponse, response_model_exclude_none=True)
        def message_send(payload: dict[str, Any]) -> dict[str, Any]:
            query_text = extract_a2a_query(payload)
            context = service.context(query_text, limit=8)
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

    return app


def create_mcp_stream_server(
    service: LlmWikiService,
    *,
    allow_drafts: bool = False,
    enable_a2a_compat: bool = False,
) -> FastMCP:
    mcp_stream = FastMCP(
        "LLMWiki Serve",
        instructions=(
            "Read approved LLMWiki context packs, search results, pages, and graph data."
        ),
        stateless_http=True,
        json_response=True,
        streamable_http_path="/stream",
    )

    @mcp_stream.tool(
        name="llmwiki_context",
        description=(
            "Build a context pack with wiki metadata, hot/index/overview orientation first, "
            "then query-ranked citation evidence."
        ),
    )
    def llmwiki_context(
        query: str = "",
        limit: int = 8,
        include_drafts: bool = False,
    ) -> dict[str, Any]:
        try:
            return service.context(
                query,
                limit=clamp_int(limit, default=8, minimum=QUERY_LIMIT_MIN, maximum=QUERY_LIMIT_MAX),
                include_drafts=network_include_drafts(allow_drafts, include_drafts),
            ).model_dump()
        except Exception as exc:
            raise ToolError(MCP_INTERNAL_FAILURE_MESSAGE) from exc

    @mcp_stream.tool(name="llmwiki_search", description="Search approved LLMWiki pages.")
    def llmwiki_search(
        query: str = "",
        limit: int = 8,
        include_drafts: bool = False,
    ) -> dict[str, Any]:
        try:
            return {
                "results": service.search(
                    query,
                    limit=clamp_int(
                        limit, default=8, minimum=QUERY_LIMIT_MIN, maximum=QUERY_LIMIT_MAX
                    ),
                    include_drafts=network_include_drafts(allow_drafts, include_drafts),
                )
            }
        except Exception as exc:
            raise ToolError(MCP_INTERNAL_FAILURE_MESSAGE) from exc

    @mcp_stream.tool(name="llmwiki_read", description="Read a page by id or path.")
    def llmwiki_read(page_id: str, include_drafts: bool = False) -> dict[str, Any]:
        try:
            return service.read(
                page_id,
                include_drafts=network_include_drafts(allow_drafts, include_drafts),
            )
        except Exception as exc:
            raise ToolError(MCP_INTERNAL_FAILURE_MESSAGE) from exc

    @mcp_stream.tool(name="llmwiki_graph", description="Return page/link/source graph.")
    def llmwiki_graph(limit: int = 500, include_drafts: bool = False) -> dict[str, Any]:
        try:
            return service.graph(
                limit=clamp_int(
                    limit, default=500, minimum=GRAPH_LIMIT_MIN, maximum=GRAPH_LIMIT_MAX
                ),
                include_drafts=network_include_drafts(allow_drafts, include_drafts),
            )
        except Exception as exc:
            raise ToolError(MCP_INTERNAL_FAILURE_MESSAGE) from exc

    @mcp_stream.tool(
        name="llmwiki_source_refs",
        description="Return typed source-reference handles linked from approved pages.",
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
        description="Return the source bundle manifest with typed source-reference handles.",
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
) -> Any:
    params = params or {}
    if method == "tools/list":
        return {
            "tools": [
                {
                    "name": "llmwiki_context",
                    "description": (
                        "Build a context pack with wiki metadata, hot/index/overview "
                        "orientation first, then query-ranked citation evidence."
                    ),
                },
                {"name": "llmwiki_search", "description": "Search approved LLMWiki pages."},
                {"name": "llmwiki_read", "description": "Read a page by id or path."},
                {"name": "llmwiki_graph", "description": "Return page/link/source graph."},
                {
                    "name": "llmwiki_source_refs",
                    "description": (
                        "Return typed source-reference handles linked from approved pages."
                    ),
                },
                {
                    "name": "llmwiki_source_bundle",
                    "description": (
                        "Return the source bundle manifest with typed source-reference handles."
                    ),
                },
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
                args.get("limit"), default=8, minimum=QUERY_LIMIT_MIN, maximum=QUERY_LIMIT_MAX
            ),
            include_drafts=network_include_drafts(allow_drafts, args.get("include_drafts")),
        ).model_dump()
    if name == "llmwiki_search":
        return {
            "results": service.search(
                str(args.get("query") or ""),
                limit=clamp_int(
                    args.get("limit"), default=8, minimum=QUERY_LIMIT_MIN, maximum=QUERY_LIMIT_MAX
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
                args.get("limit"), default=500, minimum=GRAPH_LIMIT_MIN, maximum=GRAPH_LIMIT_MAX
            ),
            include_drafts=network_include_drafts(allow_drafts, args.get("include_drafts")),
        )
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


def bool_arg(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


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
