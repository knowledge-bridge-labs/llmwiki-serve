from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from llmwiki_serve import __version__
from llmwiki_serve.api import create_app


def test_conn040_health_exposes_redacted_source_and_protocol_discovery(
    tmp_path: Path,
) -> None:
    """CONN-040: /health is enough to identify a ready llmwiki-serve source."""
    root = tmp_path / "private" / "connection-wiki"
    root.mkdir(parents=True)
    write_markdown(
        root / "index.md",
        """
---
wiki_title: Connection Wiki
review_state: approved
source_refs: [CONN-SRC-001]
---
# Connection Wiki

Ready source for connection discovery.
""",
    )

    health = TestClient(create_app(root)).get("/health").json()
    encoded = json.dumps(health)

    assert health["status"] == "ok"
    assert health["service"] == "llmwiki-serve"
    assert health["version"] == __version__
    assert health["source"]["source_id"] == "connection-wiki"
    assert health["source"]["bundle_id"].startswith("connection-wiki:sha256:")
    assert health["source"]["public_uri"] == "llmwiki://connection-wiki"
    assert health["source"]["page_count"] == 1
    assert health["source"]["approved_page_count"] == 1
    assert health["source"]["projection"]["signature"].startswith("sha256:")
    assert {
        "llmwiki_source_bundle",
        "llmwiki_context",
        "llmwiki_search",
        "llmwiki_read",
        "llmwiki_graph",
        "llmwiki_graph_neighbors",
        "llmwiki_source_refs",
        "mcp-jsonrpc",
        "mcp-streamable-http",
    } <= set(health["capabilities"])
    assert health["endpoints"]["manifest"] == "/manifest"
    assert health["endpoints"]["source_bundle"] == "/source-bundle"
    assert health["endpoints"]["mcp_jsonrpc"] == "/mcp"
    assert health["endpoints"]["mcp_streamable_http"] == "/mcp/stream"
    assert health["endpoints"]["graph_neighborhood"] == "/graph/neighborhood"
    assert health["endpoints"]["openapi"] == "/openapi.json"
    assert health["endpoints"]["a2a_agent_card"] == ""
    assert health["cors"] == {
        "mode": "local-dev-allowlist",
        "local_dev_origins": True,
        "explicit_origin_count": 0,
    }
    assert str(root) not in encoded
    assert str(tmp_path) not in encoded


def test_conn040_health_reports_a2a_and_explicit_cors_without_origin_values() -> None:
    """CONN-040: /health reports opt-in surfaces and redacted CORS mode."""
    origin = "http://127.0.0.1:19976"
    fixture = Path(__file__).parent / "fixtures" / "sample-wiki"

    health = (
        TestClient(
            create_app(
                fixture,
                cors_origins=[origin],
                enable_a2a_compat=True,
            )
        )
        .get("/health")
        .json()
    )
    encoded = json.dumps(health)

    assert "a2a-message" in health["capabilities"]
    assert health["endpoints"]["a2a_agent_card"] == "/.well-known/agent-card.json"
    assert health["endpoints"]["a2a_message_send"] == "/message:send"
    assert health["cors"] == {
        "mode": "explicit-allowlist",
        "local_dev_origins": False,
        "explicit_origin_count": 1,
    }
    assert origin not in encoded
    assert str(fixture.resolve()) not in encoded


def write_markdown(path: Path, content: str) -> None:
    path.write_text(content.strip() + "\n", encoding="utf-8")
