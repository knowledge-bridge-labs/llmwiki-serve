from __future__ import annotations

from pathlib import Path

import llmwiki_serve
from llmwiki_serve import LlmWikiService, __version__, create_app

FIXTURE = Path(__file__).parent / "fixtures" / "sample-wiki"


def test_package_root_exports_public_api_boundary() -> None:
    assert llmwiki_serve.__all__ == ["__version__", "LlmWikiService", "create_app"]
    assert llmwiki_serve.__version__ == __version__
    assert llmwiki_serve.LlmWikiService is LlmWikiService
    assert llmwiki_serve.create_app is create_app


def test_openapi_contract_covers_core_http_response_models() -> None:
    schema = create_app(FIXTURE).openapi()
    a2a_schema = create_app(FIXTURE, enable_a2a_compat=True).openapi()

    assert schema["openapi"] == "3.1.0"
    assert schema["info"]["version"] == __version__
    assert a2a_schema["info"]["version"] == __version__
    assert {
        "/health",
        "/manifest",
        "/query",
        "/search",
        "/read/{page_id}",
        "/graph",
        "/graph/neighborhood",
        "/diagnostics/projection-store",
        "/mcp",
    } <= set(schema["paths"])
    assert "/.well-known/agent-card.json" not in schema["paths"]
    assert "/message:send" not in schema["paths"]
    assert {"/.well-known/agent-card.json", "/message:send"} <= set(a2a_schema["paths"])
    assert {
        "ContextPack",
        "WikiManifest",
        "SearchResponse",
        "GraphResponse",
        "GraphNeighborhoodResponse",
        "WikiPage",
        "ReadNotFoundResponse",
        "HttpDetailResponse",
        "JsonRpcResponse",
        "ProjectionStoreDiagnosticsResponse",
    } <= set(schema["components"]["schemas"])
    assert "A2AResponse" not in schema["components"]["schemas"]
    assert "A2AResponse" in a2a_schema["components"]["schemas"]

    query_response = schema["paths"]["/query"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    graph_response = schema["paths"]["/graph"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    graph_neighborhood_response = schema["paths"]["/graph/neighborhood"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    health_schema = schema["components"]["schemas"]["HealthResponse"]
    health_endpoints_schema = schema["components"]["schemas"]["HealthEndpointsResponse"]
    projection_store_schema = schema["components"]["schemas"]["ProjectionStoreDiagnosticsResponse"]

    assert query_response["$ref"] == "#/components/schemas/ContextPack"
    assert graph_response["$ref"] == "#/components/schemas/GraphResponse"
    assert graph_neighborhood_response["$ref"] == "#/components/schemas/GraphNeighborhoodResponse"
    assert {"capabilities", "endpoints"} <= set(health_schema["required"])
    assert {
        "health",
        "manifest",
        "source_bundle",
        "source_refs",
        "query",
        "search",
        "read",
        "graph",
        "graph_neighborhood",
        "mcp_jsonrpc",
        "mcp_streamable_http",
        "openapi",
        "docs",
        "a2a_agent_card",
        "a2a_message_send",
    } <= set(health_endpoints_schema["required"])
    assert {"backend_kind", "endpoint"} <= set(projection_store_schema["properties"])
    assert {
        "backend",
        "backend_kind",
        "endpoint",
        "namespace",
        "cache_source_id",
        "available",
    } <= set(projection_store_schema["required"])
    assert projection_store_schema["properties"]["backend_kind"]["enum"] == ["memory", "redis"]
    assert {"type": "string"} in projection_store_schema["properties"]["endpoint"]["anyOf"]
    assert {"type": "null"} in projection_store_schema["properties"]["endpoint"]["anyOf"]
    assert (
        schema["paths"]["/read/{page_id}"]["get"]["responses"]["404"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        == "#/components/schemas/HttpDetailResponse"
    )
