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
        "RetrievalGuidance",
        "FolderCard",
        "PageCard",
        "WikiManifest",
        "SearchResponse",
        "SearchResultProjection",
        "GraphResponse",
        "GraphNeighborhoodResponse",
        "WikiPage",
        "WikiPageProjection",
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
    query_request_schema = schema["components"]["schemas"]["QueryRequest"]

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
    assert {
        "mode",
        "fields",
        "snippet_chars",
        "min_score",
        "exclude_page_ids",
        "query_variants",
    } <= set(query_request_schema["properties"])
    assert "analyzer_profile" not in query_request_schema["properties"]
    assert query_request_schema["properties"]["mode"]["enum"] == [
        "lexical",
        "literal",
        "vector",
        "hybrid",
    ]
    assert query_request_schema["properties"]["query_variants"]["maxItems"] == 2
    assert query_request_schema["properties"]["query_variants"]["type"] == "array"
    assert "retrieval_guidance" in schema["components"]["schemas"]["ContextPack"]["properties"]
    guidance_schema = schema["components"]["schemas"]["RetrievalGuidance"]
    assert guidance_schema["additionalProperties"] is False
    assert guidance_schema["required"] == [
        "schema_version",
        "orientation_source",
        "content_trust",
        "max_query_variants",
        "character_budget",
        "folder_cards",
        "page_cards",
        "suggested_terms",
        "exact_identifiers",
        "fallback_modes",
    ]
    assert {
        "schema_version",
        "orientation_source",
        "content_trust",
        "max_query_variants",
        "character_budget",
        "folder_cards",
        "page_cards",
        "suggested_terms",
        "exact_identifiers",
        "fallback_modes",
    } <= set(guidance_schema["properties"])
    assert schema["components"]["schemas"]["FolderCard"]["required"] == [
        "path",
        "page_count",
        "terms",
    ]
    assert schema["components"]["schemas"]["PageCard"]["required"] == [
        "page_id",
        "title",
        "path",
        "headings",
        "terms",
        "exact_identifiers",
        "excerpt",
    ]
    assert (
        schema["paths"]["/read/{page_id}"]["get"]["responses"]["404"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        == "#/components/schemas/HttpDetailResponse"
    )
