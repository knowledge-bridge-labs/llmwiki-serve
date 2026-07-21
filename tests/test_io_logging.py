from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from llmwiki_serve.api import create_app
from llmwiki_serve.cli import app as cli_app
from llmwiki_serve.io_logging import DEFAULT_IO_LOG_PATH, LOCAL_ROOT_REDACTION, REDACTION


def test_default_io_log_captures_http_mcp_and_a2a_canaries(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = write_io_wiki(tmp_path / "wiki", "zzioresponsecanary")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.chdir(runtime)
    client = TestClient(create_app(root, enable_a2a_compat=True))

    query_response = client.post(
        "/query",
        json={"query": "zzioresponsecanary", "limit": 2},
    )
    mcp_response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "llmwiki_context",
                "arguments": {"query": "zzioresponsecanary", "limit": 2},
            },
        },
    )
    a2a_response = client.post(
        "/message:send",
        json={"data": {"query": "zzioresponsecanary"}},
    )

    assert query_response.status_code == 200
    assert mcp_response.status_code == 200
    assert a2a_response.status_code == 200

    events = read_events(runtime / DEFAULT_IO_LOG_PATH)
    events_by_path = {event["path"]: event for event in events}

    for path in ("/query", "/mcp", "/message:send"):
        event = events_by_path[path]
        encoded = json.dumps(event)
        assert event["method"] == "POST"
        assert event["status"] == 200
        assert event["duration_ms"] >= 0
        assert event["request"]["body"]["captured"] is True
        assert event["response"]["body"]["captured"] is True
        assert "zzioresponsecanary" in encoded

    assert events_by_path["/query"]["request"]["body"]["json"]["query"] == "zzioresponsecanary"
    assert events_by_path["/query"]["response"]["body"]["json"]["answerable"] is True
    assert events_by_path["/mcp"]["request"]["body"]["json"]["params"]["name"] == (
        "llmwiki_context"
    )


def test_io_log_off_suppresses_file_and_events(tmp_path: Path, monkeypatch: Any) -> None:
    root = write_io_wiki(tmp_path / "wiki", "zziooffcanary")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.chdir(runtime)
    client = TestClient(create_app(root, io_log="off"))

    response = client.post("/query", json={"query": "zziooffcanary"})

    assert response.status_code == 200
    assert not (runtime / DEFAULT_IO_LOG_PATH).exists()


def test_env_io_log_path_and_auth_token_redaction(tmp_path: Path, monkeypatch: Any) -> None:
    root = write_io_wiki(tmp_path / "private-wiki", "zzredactioncanary")
    log_path = tmp_path / "logs" / "serve-io.jsonl"
    monkeypatch.setenv("LLMWIKI_SERVE_IO_LOG", str(log_path))
    client = TestClient(create_app(root))
    openai_key = "sk" + "-proj-redactionCanarySecret1234567890"
    github_token = "ghp" + "_redactionCanarySecret1234567890"
    basic_secret = "c2VydmUtaW8tYmFzaWMtc2VjcmV0"
    windows_secret_path = "C:" + r"\Users\example-user\serve-secret.txt"
    unc_secret_path = "\\" + r"\server\share\serve-secret.txt"
    posix_home_secret_path = "/home/" + "example-user/serve-secret.txt"
    posix_tmp_secret_path = "/var/tmp/serve-secret.txt"
    extra_secret_canaries = [
        "serve-cookie-secret",
        "serve-set-cookie-secret",
        basic_secret,
        "serve-client-secret",
        "serve-code-secret",
        "serve-sig-secret",
        "serve-signature-secret",
        windows_secret_path,
        unc_secret_path,
        posix_home_secret_path,
        posix_tmp_secret_path,
    ]

    response = client.post(
        "/mcp?code=serve-first-code-secret&sig=serve-first-sig-secret&signature=serve-first-signature-secret&ok=1",
        headers={
            "Authorization": "Bearer " + "headerSecretToken123",
            "Cookie": "session=serve-cookie-secret",
            "X-Api-Key": "headerApiKeySecret123",
        },
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "llmwiki_context",
                "arguments": {
                    "query": (
                        f"zzredactioncanary {root.resolve()} "
                        f"token=bodySecretToken123 {openai_key} {github_token} "
                        f"Basic {basic_secret} Cookie: session=serve-cookie-secret "
                        "Set-Cookie: session=serve-set-cookie-secret "
                        "https://wiki.example.test/context?"
                        "client_secret=serve-client-secret&code=serve-code-secret&"
                        "sig=serve-sig-secret&signature=serve-signature-secret "
                        f"{windows_secret_path} "
                        f"{unc_secret_path} "
                        f"{posix_home_secret_path} {posix_tmp_secret_path}"
                    ),
                    "credential": "bodyCredentialSecret123",
                },
            },
        },
    )

    assert response.status_code == 200
    encoded = json.dumps(read_events(log_path))
    assert "headerSecretToken123" not in encoded
    assert "headerApiKeySecret123" not in encoded
    assert "bodySecretToken123" not in encoded
    assert "bodyCredentialSecret123" not in encoded
    assert openai_key not in encoded
    assert github_token not in encoded
    assert str(root.resolve()) not in encoded
    assert "https://wiki.example.test/context" not in encoded
    assert "wiki.example.test" not in encoded
    assert "serve-first-code-secret" not in encoded
    assert "serve-first-sig-secret" not in encoded
    assert "serve-first-signature-secret" not in encoded
    for canary in extra_secret_canaries:
        assert canary not in encoded
    assert REDACTION in encoded
    assert LOCAL_ROOT_REDACTION in encoded


def test_cli_io_log_off_is_accepted_without_starting_uvicorn(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = write_io_wiki(tmp_path / "wiki", "zzclioffcanary")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.chdir(runtime)

    captured: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        captured["app"] = app
        captured["kwargs"] = kwargs

    monkeypatch.setattr("uvicorn.run", fake_run)

    result = CliRunner().invoke(cli_app, ["serve", str(root), "--io-log", "off"])

    assert result.exit_code == 0, result.output
    assert captured["kwargs"]["host"] == "127.0.0.1"
    assert captured["kwargs"]["port"] == 8765

    response = TestClient(captured["app"]).post("/query", json={"query": "zzclioffcanary"})

    assert response.status_code == 200
    assert not (runtime / DEFAULT_IO_LOG_PATH).exists()


def write_io_wiki(root: Path, canary: str) -> Path:
    root.mkdir()
    (root / "index.md").write_text(
        f"""---
wiki_title: I/O Logging Fixture
review_state: approved
---
# I/O Logging Fixture

{canary} appears in approved evidence for serve I/O logging.
""",
        encoding="utf-8",
    )
    return root


def read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
