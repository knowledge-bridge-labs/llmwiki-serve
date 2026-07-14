from __future__ import annotations

from pathlib import Path

import llmwiki_serve
from llmwiki_serve import LlmWikiService, create_app

FIXTURE = Path(__file__).parent / "fixtures" / "sample-wiki"


def test_package_root_exports_public_api_boundary() -> None:
    assert llmwiki_serve.__all__ == ["LlmWikiService", "create_app"]
    assert llmwiki_serve.LlmWikiService is LlmWikiService
    assert llmwiki_serve.create_app is create_app


def test_openapi_contract_covers_core_http_response_models() -> None:
    schema = create_app(FIXTURE).openapi()
    a2a_schema = create_app(FIXTURE, enable_a2a_compat=True).openapi()

    assert schema["openapi"] == "3.1.0"
    assert {
        "/health",
        "/manifest",
        "/query",
        "/search",
        "/read/{page_id}",
        "/graph",
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

    assert query_response["$ref"] == "#/components/schemas/ContextPack"
    assert graph_response["$ref"] == "#/components/schemas/GraphResponse"
    assert (
        schema["paths"]["/read/{page_id}"]["get"]["responses"]["404"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        == "#/components/schemas/HttpDetailResponse"
    )
