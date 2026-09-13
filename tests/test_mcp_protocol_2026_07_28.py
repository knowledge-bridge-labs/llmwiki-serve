from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from llmwiki_serve.api import MCP_STREAM_PATH, create_app

FIXTURE = Path(__file__).parent / "fixtures" / "sample-wiki"
NATIVE_FIXTURE = Path(__file__).parent / "fixtures" / "native-wiki-root"
MCP_PROTOCOL_VERSION = "2026-07-28"
MCP_COMPAT_PROTOCOL_VERSION = "2025-06-18"
EXPECTED_TOOL_NAMES = [
    "llmwiki_context",
    "llmwiki_search",
    "llmwiki_read",
    "llmwiki_graph",
    "llmwiki_graph_neighbors",
    "llmwiki_source_refs",
    "llmwiki_source_bundle",
]
EXPECTED_RESOURCE_NAMES = ["artwork-review", "hot", "index", "requester-return"]
EXPECTED_READ_ONLY_TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "openWorldHint": False,
}
EXPECTED_PAGE_TEMPLATE_ANNOTATIONS = {"audience": ["assistant"], "priority": 0.7}
SOURCE_QUERY_PROMPT_NAME = "llmwiki_source_grounded_query"


def test_mcp_streamable_http_supports_2026_07_28_modern_requests() -> None:
    with TestClient(
        create_app(FIXTURE),
        base_url="http://127.0.0.1:8000",
        follow_redirects=False,
    ) as client:
        discover = modern_rpc(client, 1, "server/discover")
        tools = modern_rpc(client, 2, "tools/list")

        context = modern_tool_call(
            client,
            3,
            "llmwiki_context",
            {"query": "required copy release readiness", "limit": 4},
        )
        search = modern_tool_call(
            client,
            4,
            "llmwiki_search",
            {
                "query": "requester return",
                "mode": "literal",
                "fields": "page_id,route",
                "snippet_chars": 0,
            },
        )
        read = modern_tool_call(
            client,
            5,
            "llmwiki_read",
            {"page_id": "requester-return", "fields": "id,title"},
        )
        graph = modern_tool_call(client, 6, "llmwiki_graph", {"limit": 500})
        source_refs = modern_tool_call(client, 7, "llmwiki_source_refs", {})
        source_bundle = modern_tool_call(client, 8, "llmwiki_source_bundle", {})

    with TestClient(
        create_app(NATIVE_FIXTURE),
        base_url="http://127.0.0.1:8000",
        follow_redirects=False,
    ) as client:
        graph_neighbors = modern_tool_call(
            client,
            9,
            "llmwiki_graph_neighbors",
            {
                "seed": "overview",
                "depth": 1,
                "direction": "out",
                "relation": "supports",
                "limit": 10,
            },
        )

    discover_result = discover["result"]
    assert discover_result["resultType"] == "complete"
    assert discover_result["supportedVersions"] == [MCP_PROTOCOL_VERSION]
    assert discover_result["capabilities"] == {
        "prompts": {"listChanged": False},
        "resources": {"listChanged": False, "subscribe": False},
        "tools": {"listChanged": False},
    }
    assert discover_result["instructions"]
    assert discover_result["ttlMs"] == 0
    assert discover_result["cacheScope"] == "private"
    assert discover_result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == (
        "Sample Packaging LLMWiki - LLMWiki Serve"
    )

    tools_result = tools["result"]
    assert tools_result["resultType"] == "complete"
    assert tools_result["ttlMs"] == 0
    assert tools_result["cacheScope"] == "private"
    tool_names = [tool["name"] for tool in tools_result["tools"]]
    assert tool_names == EXPECTED_TOOL_NAMES
    assert all(tool["inputSchema"]["type"] == "object" for tool in tools_result["tools"])
    assert all("outputSchema" in tool for tool in tools_result["tools"])
    assert all(
        tool["annotations"] == EXPECTED_READ_ONLY_TOOL_ANNOTATIONS for tool in tools_result["tools"]
    )
    assert all(
        tool["_meta"]["io.modelcontextprotocol/serverInfo"]["version"] == "0.2.11"
        for tool in [
            context,
            search,
            read,
            graph,
            source_refs,
            source_bundle,
            graph_neighbors,
        ]
    )
    assert context["structuredContent"]["answerable"] is True
    assert context["structuredContent"]["evidence"]
    assert search["structuredContent"]["results"][0] == {
        "page_id": "requester-return",
        "route": "literal",
    }
    assert read["structuredContent"] == {
        "id": "requester-return",
        "title": "Requester Return",
    }
    assert any(node["id"].startswith("page:") for node in graph["structuredContent"]["nodes"])
    assert source_refs["structuredContent"]["source_refs"]
    assert source_bundle["structuredContent"]["source_id"] == "sample-packaging-llmwiki"
    assert graph_neighbors["structuredContent"]["seeds"] == ["page:overview"]
    assert graph_neighbors["structuredContent"]["edges"][0]["relation"] == "supports"


def test_mcp_streamable_http_resources_and_prompts_are_source_scoped() -> None:
    with TestClient(
        create_app(FIXTURE),
        base_url="http://127.0.0.1:8000",
        follow_redirects=False,
    ) as client:
        resources = modern_rpc(client, 1, "resources/list")["result"]
        templates = modern_rpc(client, 2, "resources/templates/list")["result"]
        prompts = modern_rpc(client, 3, "prompts/list")["result"]
        resource_uri = next(
            resource["uri"]
            for resource in resources["resources"]
            if resource["name"] == "requester-return"
        )
        resource_read = modern_rpc(
            client,
            4,
            "resources/read",
            {"uri": resource_uri},
            mcp_name=resource_uri,
        )["result"]
        prompt = modern_rpc(
            client,
            5,
            "prompts/get",
            {
                "name": SOURCE_QUERY_PROMPT_NAME,
                "arguments": {"query": "release readiness"},
            },
            mcp_name=SOURCE_QUERY_PROMPT_NAME,
        )["result"]

    assert resources["resultType"] == "complete"
    assert [resource["name"] for resource in resources["resources"]] == EXPECTED_RESOURCE_NAMES
    assert all(
        resource["uri"].startswith("llmwiki://sample-packaging-llmwiki/pages/")
        for resource in resources["resources"]
    )
    assert all(resource["mimeType"] == "text/markdown" for resource in resources["resources"])
    resource_annotations = {
        resource["name"]: resource["annotations"] for resource in resources["resources"]
    }
    assert resource_annotations == {
        "artwork-review": {"audience": ["assistant"], "priority": 0.55},
        "hot": {"audience": ["assistant"], "priority": 1.0},
        "index": {"audience": ["assistant"], "priority": 0.95},
        "requester-return": {"audience": ["assistant"], "priority": 0.55},
    }
    assert "draft-note" not in [resource["name"] for resource in resources["resources"]]
    assert templates["resourceTemplates"] == [
        {
            "annotations": EXPECTED_PAGE_TEMPLATE_ANNOTATIONS,
            "description": (
                "Read an approved page from a served LLMWiki source by source id and page id."
            ),
            "mimeType": "text/markdown",
            "name": "llmwiki_page",
            "title": "LLMWiki Page",
            "uriTemplate": "llmwiki://{source_id}/pages/{page_id}",
        }
    ]
    assert resource_read["resultType"] == "complete"
    assert resource_read["contents"] == [
        {
            "mimeType": "text/markdown",
            "text": (
                "# Requester Return\n\n"
                "Missing required text is a blocking exception. The reviewer records the missing\n"
                "field and returns the request to the requester."
            ),
            "uri": "llmwiki://sample-packaging-llmwiki/pages/requester-return",
        }
    ]
    assert prompts["prompts"] == [
        {
            "arguments": [{"name": "query", "required": True}],
            "description": "Prepare a query that must be answered from the served LLMWiki source.",
            "name": SOURCE_QUERY_PROMPT_NAME,
            "title": "Source-Grounded LLMWiki Query",
        }
    ]
    assert prompt["resultType"] == "complete"
    assert prompt["messages"][0]["role"] == "user"
    prompt_text = prompt["messages"][0]["content"]["text"]
    assert "llmwiki://sample-packaging-llmwiki" in prompt_text
    assert "release readiness" in prompt_text
    network_payload = json.dumps(
        {
            "resources": resources,
            "templates": templates,
            "resource_read": resource_read,
            "prompts": prompts,
            "prompt": prompt,
        }
    )
    assert str(FIXTURE.resolve()) not in network_payload


def test_mcp_streamable_http_enforces_2026_07_28_request_metadata_and_header_conflicts() -> None:
    with TestClient(
        create_app(FIXTURE),
        base_url="http://127.0.0.1:8000",
        follow_redirects=False,
    ) as client:
        missing_event_stream = modern_response(
            client,
            1,
            "tools/list",
            headers=modern_headers("tools/list", accept="application/json"),
        )
        missing_protocol_header = modern_response(
            client,
            2,
            "tools/list",
            headers=headers_without(modern_headers("tools/list"), "MCP-Protocol-Version"),
        )
        mismatched_protocol_header = modern_response(
            client,
            3,
            "tools/list",
            headers={
                **modern_headers("tools/list"),
                "MCP-Protocol-Version": "2025-11-25",
            },
        )
        unsupported_protocol_version = modern_response(
            client,
            4,
            "tools/list",
            body=modern_body(4, "tools/list", protocol_version="1900-01-01"),
            headers={
                **modern_headers("tools/list"),
                "MCP-Protocol-Version": "1900-01-01",
            },
        )
        current_client_tool_call = modern_response(
            client,
            5,
            "tools/call",
            body={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "llmwiki_context", "arguments": {"query": "release"}},
            },
            headers=modern_transport_headers(),
        )
        current_client_empty_meta_tool_call = modern_response(
            client,
            6,
            "tools/call",
            body={
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "_meta": {},
                    "name": "llmwiki_context",
                    "arguments": {"query": "release"},
                },
            },
            headers=modern_transport_headers(),
        )
        partial_meta_tools = modern_response(
            client,
            7,
            "tools/list",
            body={
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/list",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                },
            },
            headers=modern_headers("tools/list"),
        )
        missing_name_header = modern_response(
            client,
            8,
            "tools/call",
            params={"name": "llmwiki_context", "arguments": {"query": "release"}},
            headers=modern_headers("tools/call"),
        )
        mismatched_method_header = modern_response(
            client,
            9,
            "tools/list",
            headers={
                **modern_headers("tools/list"),
                "Mcp-Method": "resources/list",
            },
        )
        mismatched_name_header = modern_response(
            client,
            10,
            "tools/call",
            params={"name": "llmwiki_context", "arguments": {"query": "release"}},
            headers={
                **modern_headers("tools/call"),
                "Mcp-Name": "llmwiki_read",
            },
        )
        supplied_meta_protocol_conflict = modern_response(
            client,
            11,
            "tools/call",
            body={
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {
                    "_meta": modern_meta("2025-11-25"),
                    "name": "llmwiki_context",
                    "arguments": {"query": "release"},
                },
            },
            headers=modern_transport_headers(),
        )
        malformed_client_info = modern_response(
            client,
            12,
            "tools/list",
            body={
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/list",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/clientInfo": "not-an-object",
                    }
                },
            },
            headers=modern_transport_headers(),
        )
        current_codex_compat_tool_call = modern_response(
            client,
            13,
            "tools/call",
            body={
                "jsonrpc": "2.0",
                "id": 13,
                "method": "tools/call",
                "params": {
                    "_meta": {},
                    "name": "llmwiki_context",
                    "arguments": {"query": "release"},
                },
            },
            headers=modern_transport_headers(protocol_version=MCP_COMPAT_PROTOCOL_VERSION),
        )

    assert_mcp_error(missing_event_stream, MCP_HEADER_MISMATCH_CODE, "Accept header")
    assert_mcp_error(
        missing_protocol_header,
        MCP_HEADER_MISMATCH_CODE,
        "MCP-Protocol-Version header is required",
    )
    assert_mcp_error(
        mismatched_protocol_header,
        MCP_UNSUPPORTED_PROTOCOL_VERSION_CODE,
        "Unsupported protocol version",
    )
    unsupported_error = assert_mcp_error(
        unsupported_protocol_version,
        MCP_UNSUPPORTED_PROTOCOL_VERSION_CODE,
        "Unsupported protocol version",
    )
    assert unsupported_error["data"] == {
        "supported": [MCP_PROTOCOL_VERSION, MCP_COMPAT_PROTOCOL_VERSION],
        "requested": "1900-01-01",
    }
    current_client_result = cast(
        dict[str, Any],
        assert_mcp_success(current_client_tool_call, 5)["result"],
    )
    assert current_client_result["structuredContent"]["answerable"] is True
    current_client_empty_meta_result = cast(
        dict[str, Any],
        assert_mcp_success(current_client_empty_meta_tool_call, 6)["result"],
    )
    assert current_client_empty_meta_result["structuredContent"]["answerable"] is True
    partial_meta_result = assert_mcp_success(partial_meta_tools, 7)["result"]
    assert [tool["name"] for tool in partial_meta_result["tools"]] == EXPECTED_TOOL_NAMES
    missing_name_result = cast(dict[str, Any], assert_mcp_success(missing_name_header, 8)["result"])
    assert missing_name_result["structuredContent"]["answerable"] is True
    assert_mcp_error(
        mismatched_method_header,
        MCP_HEADER_MISMATCH_CODE,
        "Mcp-Method header does not match",
    )
    assert_mcp_error(
        mismatched_name_header,
        MCP_HEADER_MISMATCH_CODE,
        "Mcp-Name header does not match",
    )
    assert_mcp_error(
        supplied_meta_protocol_conflict,
        MCP_HEADER_MISMATCH_CODE,
        "does not match request params._meta protocolVersion",
    )
    assert_mcp_error(
        malformed_client_info,
        MCP_HEADER_MISMATCH_CODE,
        "clientInfo",
    )
    current_codex_compat_result = cast(
        dict[str, Any],
        assert_mcp_success(current_codex_compat_tool_call, 13)["result"],
    )
    assert current_codex_compat_result["structuredContent"]["answerable"] is True


def test_mcp_streamable_http_legacy_initialize_still_works() -> None:
    with TestClient(
        create_app(FIXTURE),
        base_url="http://127.0.0.1:8000",
        follow_redirects=False,
    ) as client:
        response = client.post(
            MCP_STREAM_PATH,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "legacy-probe", "version": "1.0.0"},
                },
            },
            headers=base_headers(),
        )

    assert response.status_code == 200
    assert "Mcp-Session-Id" not in response.headers
    payload = response.json()
    assert "error" not in payload
    assert payload["result"]["protocolVersion"] == "2025-11-25"
    assert payload["result"]["serverInfo"] == {
        "name": "Sample Packaging LLMWiki - LLMWiki Serve",
        "version": "0.2.11",
    }


def modern_rpc(
    client: TestClient,
    request_id: int,
    method: str,
    params: dict[str, Any] | None = None,
    mcp_name: str | None = None,
) -> dict[str, Any]:
    response = client.post(
        MCP_STREAM_PATH,
        json=modern_body(request_id, method, params=params),
        headers=modern_headers(method, mcp_name=mcp_name),
    )
    assert response.status_code == 200, response.text
    assert "Mcp-Session-Id" not in response.headers
    payload = cast(dict[str, Any], response.json())
    assert "error" not in payload
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == request_id
    return payload


def modern_tool_call(
    client: TestClient,
    request_id: int,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    payload = modern_rpc(
        client,
        request_id,
        "tools/call",
        {"name": name, "arguments": arguments},
        mcp_name=name,
    )
    result = cast(dict[str, Any], payload["result"])
    assert result["resultType"] == "complete"
    assert result["isError"] is False
    return result


def modern_response(
    client: TestClient,
    request_id: int,
    method: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    return client.post(
        MCP_STREAM_PATH,
        json=body if body is not None else modern_body(request_id, method, params=params),
        headers=headers if headers is not None else modern_headers(method),
    )


def modern_body(
    request_id: int,
    method: str,
    *,
    params: dict[str, Any] | None = None,
    protocol_version: str = MCP_PROTOCOL_VERSION,
) -> dict[str, Any]:
    body_params = {"_meta": modern_meta(protocol_version)}
    if params:
        body_params.update(params)
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": body_params}


def modern_headers(
    method: str,
    *,
    mcp_name: str | None = None,
    accept: str = "application/json, text/event-stream",
) -> dict[str, str]:
    headers = {
        **base_headers(accept=accept),
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        "Mcp-Method": method,
    }
    if mcp_name is not None:
        headers["Mcp-Name"] = mcp_name
    return headers


def modern_transport_headers(
    *,
    accept: str = "application/json, text/event-stream",
    protocol_version: str = MCP_PROTOCOL_VERSION,
) -> dict[str, str]:
    return {
        **base_headers(accept=accept),
        "MCP-Protocol-Version": protocol_version,
    }


def base_headers(*, accept: str = "application/json, text/event-stream") -> dict[str, str]:
    return {
        "accept": accept,
        "content-type": "application/json",
    }


def modern_meta(protocol_version: str = MCP_PROTOCOL_VERSION) -> dict[str, Any]:
    return {
        "io.modelcontextprotocol/protocolVersion": protocol_version,
        "io.modelcontextprotocol/clientInfo": {
            "name": "llmwiki-serve-protocol-test",
            "version": "1.0.0",
        },
        "io.modelcontextprotocol/clientCapabilities": {},
    }


def headers_without(headers: dict[str, str], key: str) -> dict[str, str]:
    result = dict(headers)
    result.pop(key)
    return result


MCP_HEADER_MISMATCH_CODE = -32020
MCP_UNSUPPORTED_PROTOCOL_VERSION_CODE = -32022


def assert_mcp_error(response: Any, code: int, message_fragment: str) -> dict[str, Any]:
    assert response.status_code == 400, response.text
    payload = cast(dict[str, Any], response.json())
    assert payload["jsonrpc"] == "2.0"
    error = cast(dict[str, Any], payload["error"])
    assert error["code"] == code
    assert message_fragment in error["message"]
    assert str(FIXTURE.resolve()) not in response.text
    return error


def assert_mcp_success(response: Any, request_id: int) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    assert "Mcp-Session-Id" not in response.headers
    payload = cast(dict[str, Any], response.json())
    assert "error" not in payload
    assert payload["jsonrpc"] == "2.0"
    assert payload["id"] == request_id
    assert str(FIXTURE.resolve()) not in response.text
    return payload
