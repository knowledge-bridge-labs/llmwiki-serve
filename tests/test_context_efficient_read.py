from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from llmwiki_serve.api import (
    MCP_SDK_HANDSHAKE_PROTOCOL_VERSIONS,
    MCP_SDK_MODERN_PROTOCOL_VERSIONS,
    MCP_STREAM_PATH,
    MCP_SUPPORTED_PROTOCOL_VERSIONS,
    create_app,
    resolve_mcp_tool_selection,
)
from llmwiki_serve.cli import app as cli_app
from llmwiki_serve.read_focus import (
    focused_read_payload,
    plain_read_payload,
    query_terms,
    split_markdown_sections,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample-wiki"
STREAM_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
KOREAN_PAGE = """Intro line before headings.

# 파이프라인

개요 문단.

## 블록 매칭

블록 매칭은 bbox IoU 0.7 이상과 텍스트 100% 일치를 기준으로 한다.

### 공간 N:M 매칭

높이 1.2배, 인접 5px.

## 심볼 비교

심볼 비교는 D-FINE 검출 후 IoU 기반 페어링을 쓴다.

```python
# not a heading
```

## 회귀 게이트

회귀 게이트는 baseline 앵커를 쓴다.
"""


def stream_post(client: TestClient, body: dict[str, Any], version: str | None) -> Any:
    headers = dict(STREAM_HEADERS)
    if version:
        headers["MCP-Protocol-Version"] = version
    return client.post(MCP_STREAM_PATH, json=body, headers=headers)


def legacy_session(client: TestClient, version: str) -> None:
    response = stream_post(
        client,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": version,
                "capabilities": {},
                "clientInfo": {"name": "regression-client", "version": "1.0"},
            },
        },
        None,
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["protocolVersion"] == version


# --- 1. protocol version negotiation -------------------------------------------------


def test_supported_protocol_versions_cover_sdk_negotiable_versions() -> None:
    assert "2025-11-25" in MCP_SUPPORTED_PROTOCOL_VERSIONS
    assert "2025-06-18" in MCP_SUPPORTED_PROTOCOL_VERSIONS
    for version in (*MCP_SDK_HANDSHAKE_PROTOCOL_VERSIONS, *MCP_SDK_MODERN_PROTOCOL_VERSIONS):
        assert version in MCP_SUPPORTED_PROTOCOL_VERSIONS


@pytest.mark.parametrize("version", ["2025-11-25", "2025-06-18"])
def test_latest_ts_and_python_client_header_is_accepted(version: str) -> None:
    with TestClient(create_app(FIXTURE), base_url="http://127.0.0.1:8000") as client:
        legacy_session(client, version)
        tools = stream_post(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, version)
        assert tools.status_code == 200, tools.text
        assert "llmwiki_read" in tools.text
        call = stream_post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "llmwiki_read", "arguments": {"page_id": "hot"}},
            },
            version,
        )
        assert call.status_code == 200, call.text
        assert call.json()["result"].get("isError") is not True


def test_unknown_protocol_version_is_still_rejected() -> None:
    with TestClient(create_app(FIXTURE), base_url="http://127.0.0.1:8000") as client:
        response = stream_post(
            client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, "1999-01-01"
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == -32022


# --- 2. MCP tool selection -----------------------------------------------------------


def test_tool_selection_validates_names() -> None:
    assert resolve_mcp_tool_selection(None, None) is None
    assert resolve_mcp_tool_selection(["llmwiki_read,llmwiki_search"], None) == (
        "llmwiki_search",
        "llmwiki_read",
    )
    assert "llmwiki_graph" not in (resolve_mcp_tool_selection(None, ["llmwiki_graph"]) or ())
    with pytest.raises(ValueError, match="Unknown MCP tool"):
        resolve_mcp_tool_selection(["llmwiki_nope"], None)
    with pytest.raises(ValueError, match="no tools enabled"):
        resolve_mcp_tool_selection(["llmwiki_read"], ["llmwiki_read"])


def test_tool_selection_filters_list_and_blocks_calls_on_both_mcp_surfaces() -> None:
    app = create_app(
        FIXTURE,
        mcp_tools=["llmwiki_search", "llmwiki_read", "llmwiki_graph"],
        mcp_exclude_tools=["llmwiki_graph"],
    )
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = [tool["name"] for tool in listed.json()["result"]["tools"]]
        assert names == ["llmwiki_search", "llmwiki_read"]
        blocked = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "llmwiki_graph", "arguments": {}},
            },
        )
        assert blocked.json()["error"]["code"] == -32602
        assert "disabled" in blocked.json()["error"]["message"]

        legacy_session(client, "2025-11-25")
        stream_tools = stream_post(
            client, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, "2025-11-25"
        )
        assert [t["name"] for t in stream_tools.json()["result"]["tools"]] == names
        stream_blocked = stream_post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "llmwiki_context", "arguments": {"query": "x"}},
            },
            "2025-11-25",
        )
        assert "llmwiki_context" in stream_blocked.text
        assert "error" in stream_blocked.json() or stream_blocked.json()["result"]["isError"]


# --- 3. Host allowlist ---------------------------------------------------------------


def initialize_with_host(host: str, **options: Any) -> int:
    with TestClient(create_app(FIXTURE, **options), base_url=f"http://{host}") as client:
        response = stream_post(
            client,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "host-check", "version": "1"},
                },
            },
            None,
        )
        return int(response.status_code)


def test_default_host_protection_is_unchanged() -> None:
    assert initialize_with_host("127.0.0.1:8000") == 200
    assert initialize_with_host("host.docker.internal:8000") == 421


def test_allowed_hosts_extend_dns_rebinding_allowlist() -> None:
    allowed = {"mcp_allowed_hosts": ["host.docker.internal:*"]}
    assert initialize_with_host("host.docker.internal:10788", **allowed) == 200
    assert initialize_with_host("127.0.0.1:8000", **allowed) == 200
    assert initialize_with_host("evil.example:8000", **allowed) == 421
    assert initialize_with_host("evil.example:8000", mcp_allowed_hosts=["*"]) == 200


# --- 4. focused / budgeted reads -----------------------------------------------------


def test_section_split_numbers_hierarchy_and_ignores_fences() -> None:
    sections = split_markdown_sections(KOREAN_PAGE)
    assert [(s.index, s.heading) for s in sections] == [
        ("0", "(preamble)"),
        ("1", "파이프라인"),
        ("1.1", "블록 매칭"),
        ("1.1.1", "공간 N:M 매칭"),
        ("1.2", "심볼 비교"),
        ("1.3", "회귀 게이트"),
    ]
    assert "".join(s.text for s in sections) == KOREAN_PAGE


def test_query_terms_strip_korean_particles() -> None:
    assert query_terms("블록 매칭을 IoU 기준으로") == ["블록", "매칭", "iou", "기준"]


def page(text: str = KOREAN_PAGE) -> dict[str, Any]:
    return {
        "id": "p",
        "title": "P",
        "path": "p.md",
        "role": "topic",
        "summary": "s",
        "text": text,
        "links": ["a", "b"],
    }


def test_focused_query_selects_relevant_sections_within_budget() -> None:
    result = focused_read_payload(
        page(), query="블록 매칭 IoU 기준", sections=None, max_chars=None, include_links=None
    )
    headings = [s["heading"] for s in result["selected_sections"]]
    assert headings[0] == "블록 매칭"
    assert "회귀 게이트" not in headings
    assert "IoU 0.7" in result["text"]
    assert "links" not in result
    assert (
        result["outline"][2]
        == f"1.1 블록 매칭 ({len(split_markdown_sections(KOREAN_PAGE)[2].text)})"
    )
    assert result["truncated"] is False


def test_focused_sections_include_descendants_and_report_truncation() -> None:
    result = focused_read_payload(
        page(),
        query=None,
        sections=["블록 매칭", "1.3", "없는 절"],
        max_chars=200,
        include_links=True,
    )
    assert [s["index"] for s in result["selected_sections"]][0] == "1.1"
    assert result["unmatched_sections"] == ["없는 절"]
    assert result["returned_chars"] <= 200
    assert result["links"] == ["a", "b"]
    assert result["truncated"] is (bool(result["remaining_sections"]) or result["truncated"])


def test_unmatched_query_falls_back_without_error() -> None:
    result = focused_read_payload(
        page(), query="zzzz", sections=None, max_chars=None, include_links=None
    )
    assert result["matched"] is False
    assert result["selected_sections"][0]["index"] == "0"


def test_plain_budget_clips_and_lists_remaining_sections() -> None:
    result = plain_read_payload(page(), max_chars=200, include_links=False)
    assert "links" not in result
    assert result["truncated"] is True
    assert len(result["text"]) <= 200
    assert result["total_chars"] == len(KOREAN_PAGE)
    assert result["remaining_sections"][-1].startswith("1.3 회귀 게이트")
    untouched = plain_read_payload(page(), max_chars=None, include_links=None)
    assert untouched == page()


def test_read_defaults_unchanged_and_focus_available_on_http_mcp_and_cli() -> None:
    with TestClient(create_app(FIXTURE), base_url="http://127.0.0.1:8000") as client:
        plain = client.get("/read/artwork-review").json()
        assert "read_mode" not in plain and "truncated" not in plain and "links" in plain
        focused = client.get(
            "/read/artwork-review", params={"query": "review", "max_chars": 300}
        ).json()
        assert focused["read_mode"] == "focused"
        assert focused["returned_chars"] <= 300
        rpc = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "llmwiki_read",
                    "arguments": {"page_id": "artwork-review", "sections": "1", "max_chars": 300},
                },
            },
        ).json()["result"]
        assert rpc["read_mode"] == "focused"

    budgeted = create_app(FIXTURE, read_default_max_chars=200)
    with TestClient(budgeted, base_url="http://127.0.0.1:8000") as client:
        clipped = client.get("/read/artwork-review").json()
        assert len(clipped["text"]) <= 200

    result = CliRunner().invoke(
        cli_app, ["read", str(FIXTURE), "artwork-review", "--query", "review", "--max-chars", "300"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["read_mode"] == "focused"


def test_search_description_omits_context_hint_when_context_tool_is_disabled() -> None:
    def descriptions(**options: Any) -> dict[str, str]:
        app = create_app(FIXTURE, **options)
        with TestClient(app, base_url="http://127.0.0.1:8000") as client:
            listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            jsonrpc = {t["name"]: t["description"] for t in listed.json()["result"]["tools"]}
            legacy_session(client, "2025-11-25")
            streamed = stream_post(
                client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, "2025-11-25"
            )
            stream = {t["name"]: t["description"] for t in streamed.json()["result"]["tools"]}
            assert jsonrpc == stream
            return jsonrpc

    assert "call llmwiki_context first" in descriptions()["llmwiki_search"]
    narrowed = descriptions(mcp_tools=["llmwiki_search", "llmwiki_read"])
    assert "llmwiki_context" not in narrowed["llmwiki_search"]
    assert "query_variants" in narrowed["llmwiki_search"]
    with_context = descriptions(mcp_tools=["llmwiki_context", "llmwiki_search"])
    assert "call llmwiki_context first" in with_context["llmwiki_search"]
