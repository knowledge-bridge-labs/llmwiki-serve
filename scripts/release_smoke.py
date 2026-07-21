from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import warnings
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from llmwiki_serve import __version__ as PACKAGE_VERSION  # noqa: E402
from llmwiki_serve.api import (  # noqa: E402
    MCP_INTERNAL_FAILURE_MESSAGE,
    MCP_UNKNOWN_TOOL_MESSAGE,
    MCP_UNSUPPORTED_METHOD_MESSAGE,
    create_app,
)

DEFAULT_DIST_DIR = PROJECT_ROOT / "dist"
DEFAULT_FIXTURE = PROJECT_ROOT / "examples" / "sample-wiki"
SMOKE_QUERY = "required copy release readiness"
WHEEL_API_SMOKE_SCRIPT = r"""
import contextlib
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
import llmwiki_serve
import llmwiki_serve.api as api

HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
}


def response_payload(response):
    return {
        "status_code": response.status_code,
        "json": response.json(),
        "text": response.text,
    }


fixture = Path(sys.argv[1])
with contextlib.redirect_stdout(sys.stderr):
    with TestClient(
        api.create_app(fixture),
        base_url="http://127.0.0.1:8000",
        follow_redirects=False,
    ) as client:
        health = response_payload(client.get("/health"))
        source_refs = response_payload(client.get("/source-refs"))
        source_bundle = response_payload(client.get("/source-bundle"))
        graph_neighbors = response_payload(
            client.get("/graph/neighborhood?seed=hot&depth=1&limit=20")
        )
        mcp_graph_neighbors = response_payload(
            client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "llmwiki_graph_neighbors",
                        "arguments": {"seed": "hot", "depth": 1, "limit": 20},
                    },
                },
            )
        )
        mcp_source_bundle = response_payload(
            client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "llmwiki_source_bundle",
                        "arguments": {},
                    },
                },
            )
        )
        stream_tools = response_payload(
            client.post(
                "/mcp/stream",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                headers=HEADERS,
            )
        )
        stream_source_bundle = response_payload(
            client.post(
                "/mcp/stream",
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "llmwiki_source_bundle",
                        "arguments": {},
                    },
                },
                headers=HEADERS,
            )
        )
        stream_graph_neighbors = response_payload(
            client.post(
                "/mcp/stream",
                json={
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "llmwiki_graph_neighbors",
                        "arguments": {"seed": "hot", "depth": 1, "limit": 20},
                    },
                },
                headers=HEADERS,
            )
        )

    with TestClient(api.create_app(fixture, allow_drafts=True)) as projection_client:
        projection_graph = response_payload(
            projection_client.get("/graph?limit=2000&include_drafts=true")
        )

payload = {
    "api_file": str(Path(api.__file__).resolve()),
    "package_version": llmwiki_serve.__version__,
    "projection_graph": projection_graph,
    "health": health,
    "source_refs": source_refs,
    "source_bundle": source_bundle,
    "graph_neighbors": graph_neighbors,
    "mcp_graph_neighbors": mcp_graph_neighbors,
    "mcp_source_bundle": mcp_source_bundle,
    "stream_tools": stream_tools,
    "stream_graph_neighbors": stream_graph_neighbors,
    "stream_source_bundle": stream_source_bundle,
}
print(json.dumps(payload))
"""
EXPECTED_WHEEL_FILES = frozenset(
    {
        "llmwiki_serve/__init__.py",
        "llmwiki_serve/adapters.py",
        "llmwiki_serve/api.py",
        "llmwiki_serve/cli.py",
        "llmwiki_serve/io_logging.py",
        "llmwiki_serve/models.py",
        "llmwiki_serve/parser.py",
        "llmwiki_serve/projection.py",
        "llmwiki_serve/py.typed",
        "llmwiki_serve/search.py",
        "llmwiki_serve/service.py",
    }
)
FORBIDDEN_WHEEL_PREFIXES = (".github/", "docs/", "scripts/", "tests/")
EXPECTED_SDIST_FILES = frozenset(
    {
        "CHANGELOG.md",
        "CODE_OF_CONDUCT.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "PKG-INFO",
        "README.md",
        "SECURITY.md",
        "SUPPORT.md",
        "THIRD_PARTY_NOTICES.md",
        "docs/openapi.json",
        "docs/release.md",
        "examples/sample-wiki/index.md",
        "pyproject.toml",
        "scripts/check_third_party_notices.py",
        "scripts/export_openapi.py",
        "scripts/release_smoke.py",
        "tests/fixtures/sample-wiki/index.md",
        "tests/test_service.py",
        "uv.lock",
    }
)
EXPECTED_SDIST_SOURCE_FILES = frozenset(
    f"src/{package_file}" for package_file in EXPECTED_WHEEL_FILES
)
FORBIDDEN_SDIST_COMPONENTS = frozenset(
    {
        ".cache",
        ".git",
        ".github",
        ".hg",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".uv-cache",
        ".venv",
        "__pycache__",
        "build",
        "candidate-samples",
        "dist",
        "htmlcov",
        "node_modules",
        "venv",
    }
)
FORBIDDEN_SDIST_FILENAMES = frozenset(
    {
        ".coverage",
        ".ds_store",
        ".env",
        ".gitattributes",
        ".netrc",
        ".pypirc",
        "auth.json",
        "candidate-samples.json",
        "coverage.xml",
        "credentials.json",
        "pip.conf",
        "token.json",
    }
)
FORBIDDEN_SDIST_SUFFIXES = (
    ".crt",
    ".db",
    ".dll",
    ".dylib",
    ".key",
    ".log",
    ".pem",
    ".pyc",
    ".pyd",
    ".pyo",
    ".so",
    ".sqlite",
    ".sqlite3",
)
FORBIDDEN_SDIST_CONTENT_CANARIES = (
    ("legacy OpenAI redaction canary", b"sk" + b"-proj-redactionCanarySecret1234567890"),
    ("legacy GitHub redaction canary", b"ghp" + b"_redactionCanarySecret1234567890"),
    ("legacy bearer redaction canary", b"Bearer " + b"headerSecretToken123"),
    ("local Windows user path canary", b"C:" + rb"\Users\angel\serve-secret.txt"),
    ("local POSIX user path canary", b"/home/" + b"angel/serve-secret.txt"),
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke test the source-tree serving contract, source distribution "
            "contents, then build and install the wheel in a clean venv to smoke "
            "test the packaged CLI."
        )
    )
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=DEFAULT_DIST_DIR,
        help="Directory for built artifacts. Defaults to ./dist.",
    )
    parser.add_argument(
        "--wheel",
        type=Path,
        help="Specific wheel to smoke test instead of building one.",
    )
    parser.add_argument(
        "--sdist",
        type=Path,
        help="Specific source distribution tarball to smoke test instead of building one.",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE,
        help="Fixture wiki folder used for smoke tests.",
    )
    parser.add_argument(
        "--allow-network-install",
        action="store_true",
        help=(
            "Allow the clean wheel-install smoke to fetch runtime dependencies "
            "from the configured package indexes. By default the install runs "
            "offline and requires a warm uv cache."
        ),
    )
    args = parser.parse_args()

    fixture = args.fixture.resolve()
    require(fixture.is_dir(), f"fixture directory not found: {fixture}")

    third_party_notice_smoke()
    openapi_contract_smoke()
    source_boundary_smoke(fixture)

    uv = require_executable("uv")
    dist_dir = args.dist_dir.resolve()
    if args.wheel is None and args.sdist is None:
        wheel, sdist = build_distribution(uv, dist_dir)
    else:
        wheel = args.wheel.resolve() if args.wheel else require_latest_wheel(dist_dir)
        sdist = args.sdist.resolve() if args.sdist else require_latest_sdist(dist_dir)

    require(wheel.is_file(), f"wheel not found: {wheel}")
    require(sdist.is_file(), f"sdist not found: {sdist}")
    assert_sdist_contents(sdist)
    assert_wheel_contents(wheel)
    wheel_cli_smoke(uv, wheel, fixture, allow_network_install=args.allow_network_install)

    print("release smoke passed: source boundary, sdist contents, and wheel CLI/API")
    return 0


class SmokeFailure(RuntimeError):
    pass


def third_party_notice_smoke() -> None:
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "check_third_party_notices.py")],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise SmokeFailure(result.stderr.strip() or result.stdout.strip())


def openapi_contract_smoke() -> None:
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "export_openapi.py"), "--check"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise SmokeFailure(result.stderr.strip() or result.stdout.strip())


def source_boundary_smoke(fixture: Path) -> None:
    before_hash = tree_hash(fixture)

    manifest_cli = run_cli_json("manifest", str(fixture))
    require(manifest_cli["title"] == "Sample Packaging LLMWiki", "CLI manifest title mismatch")
    require(manifest_cli["page_count"] == 5, "CLI manifest page count mismatch")
    require(manifest_cli["approved_page_count"] == 4, "CLI manifest draft count mismatch")
    require(manifest_cli["root"] == str(fixture), "CLI manifest did not expose local root")
    require(
        "mcp-streamable-http" in manifest_cli["capabilities"],
        "CLI manifest did not expose MCP Streamable HTTP capability",
    )
    require(
        "a2a-message" not in manifest_cli["capabilities"],
        "CLI manifest exposed A2A capability without server opt-in",
    )

    query_cli = run_cli_json("query", str(fixture), SMOKE_QUERY, "--limit", "4")
    require(query_cli["query"] == SMOKE_QUERY, "CLI query text mismatch")
    require(query_cli["answerable"] is True, "CLI query did not return answerable context")
    require(query_cli["evidence"], "CLI query returned no evidence")
    require(
        all("draft" not in item["path"] for item in query_cli["evidence"]),
        "CLI query evidence included a draft page",
    )
    cli = llmwiki_cli()
    assert_cli_failure(
        [cli, "query", str(fixture), SMOKE_QUERY, "--limit", "0"],
        cwd=PROJECT_ROOT,
        expected_text="Invalid value",
    )
    with tempfile.TemporaryDirectory(prefix="llmwiki-serve-empty-root-") as temp_dir:
        empty_root = Path(temp_dir) / "empty-wiki"
        empty_root.mkdir()
        assert_cli_failure(
            [cli, "manifest", str(empty_root)],
            cwd=PROJECT_ROOT,
            expected_text="No supported wiki files were found",
        )
        assert_cli_failure(
            [cli, "serve", str(empty_root)],
            cwd=PROJECT_ROOT,
            expected_text="No supported wiki files were found",
        )

    client = TestClient(create_app(fixture))
    a2a_client = TestClient(create_app(fixture, enable_a2a_compat=True))
    health_response = client.get("/health")
    require(health_response.status_code == 200, "HTTP health check failed")
    assert_health_payload(
        health_response.json(),
        fixture,
        "HTTP health",
        manifest_cli,
        enable_a2a_compat=False,
    )
    assert_no_private_root_leak(health_response.text, fixture, "HTTP health")
    assert_local_only_cors(client)

    manifest_http = client.get("/manifest")
    manifest_data = manifest_http.json()
    expected_http_manifest = dict(manifest_cli)
    expected_http_manifest["root"] = ""
    require(manifest_data == expected_http_manifest, "HTTP manifest differs from CLI manifest")
    require(manifest_data["root"] == "", "HTTP manifest exposed root path")
    require(
        "mcp-streamable-http" in manifest_data["capabilities"],
        "HTTP manifest did not expose MCP Streamable HTTP capability",
    )
    require(
        "a2a-message" not in manifest_data["capabilities"],
        "HTTP manifest exposed A2A capability without opt-in",
    )
    require(str(fixture) not in manifest_http.text, "HTTP manifest exposed root path")

    query_http = client.post(
        "/query",
        json={"query": SMOKE_QUERY, "limit": 4},
    ).json()
    require(query_http["answerable"] is True, "HTTP query did not return answerable context")
    require(query_http["evidence"], "HTTP query returned no evidence")
    require(
        query_http["limitations"] == ["1 draft or unapproved page(s) were withheld."],
        "HTTP query did not report draft filtering",
    )

    search_http = client.post(
        "/search",
        json={"query": "requester return", "limit": 5},
    ).json()
    require(search_http["results"], "HTTP search returned no results")

    read_http = client.get("/read/requester-return").json()
    require(read_http["title"] == "Requester Return", "HTTP read returned unexpected page")

    draft_http = client.get("/read/draft-note").json()
    require(
        draft_http == {"found": False, "reason": "not approved for serving"},
        "HTTP read exposed a draft by default",
    )
    draft_override_http = client.get("/read/draft-note?include_drafts=true").json()
    require(
        draft_override_http == {"found": False, "reason": "not approved for serving"},
        "HTTP read exposed a draft without server allow-drafts",
    )

    graph_http = client.get("/graph?limit=2000").json()
    require(graph_http["nodes"], "HTTP graph returned no nodes")
    require(graph_http["edges"], "HTTP graph returned no edges")
    require(
        all("draft" not in node["id"] for node in graph_http["nodes"]),
        "HTTP graph exposed a draft node by default",
    )
    graph_neighbors_http = client.get("/graph/neighborhood?seed=hot&depth=1&limit=20").json()
    require(
        "page:hot" in {node["id"] for node in graph_neighbors_http["nodes"]},
        "HTTP graph neighborhood did not include the seed page",
    )
    require(
        graph_neighbors_http["edges"],
        "HTTP graph neighborhood returned no edges for hot.md",
    )
    expected_projection_graph_counts = projection_graph_counts(fixture)

    source_refs_response = client.get("/source-refs")
    require(source_refs_response.status_code == 200, "HTTP source refs failed")
    source_refs_http = source_refs_response.json()
    assert_source_refs_payload(source_refs_http, fixture, "HTTP source refs")
    assert_no_private_root_leak(source_refs_response.text, fixture, "HTTP source refs")

    source_bundle_response = client.get("/source-bundle")
    require(source_bundle_response.status_code == 200, "HTTP source bundle failed")
    source_bundle_http = source_bundle_response.json()
    assert_source_bundle_payload(
        source_bundle_http,
        fixture,
        "HTTP source bundle",
        expected_projection_graph_counts,
    )
    assert_no_private_root_leak(source_bundle_response.text, fixture, "HTTP source bundle")

    mcp_context = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "llmwiki_context",
                "arguments": {"query": SMOKE_QUERY, "limit": 4},
            },
        },
    ).json()
    require(mcp_context["jsonrpc"] == "2.0", "MCP-style response jsonrpc mismatch")
    require(mcp_context["result"]["answerable"] is True, "MCP-style context was not answerable")
    require(mcp_context["result"]["evidence"], "MCP-style context returned no evidence")

    mcp_graph_neighbors = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "llmwiki_graph_neighbors",
                "arguments": {"seed": "hot", "depth": 1, "limit": 20},
            },
        },
    ).json()
    require("error" not in mcp_graph_neighbors, "MCP-style graph neighbors returned an error")
    require(
        mcp_graph_neighbors["result"] == graph_neighbors_http,
        "MCP-style graph neighbors differed from HTTP graph neighborhood",
    )
    assert_no_private_root_leak(
        mcp_graph_neighbors,
        fixture,
        "MCP-style graph neighbors envelope",
    )

    mcp_source_bundle = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "llmwiki_source_bundle",
                "arguments": {},
            },
        },
    ).json()
    require("error" not in mcp_source_bundle, "MCP-style source bundle returned an error")
    require(
        mcp_source_bundle["result"] == source_bundle_http,
        "MCP-style source bundle differed from HTTP source bundle",
    )
    assert_source_bundle_payload(
        mcp_source_bundle["result"],
        fixture,
        "MCP-style source bundle",
        expected_projection_graph_counts,
    )
    assert_no_private_root_leak(
        mcp_source_bundle,
        fixture,
        "MCP-style source bundle envelope",
    )

    mcp_draft = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "llmwiki_read",
                "arguments": {"page_id": "draft-note", "include_drafts": True},
            },
        },
    ).json()
    require(
        mcp_draft["result"] == {"found": False, "reason": "not approved for serving"},
        "MCP-style read exposed a draft without server allow-drafts",
    )

    assert_mcp_streamable_http(fixture)
    assert_safe_mcp_errors(client, fixture)

    require(
        client.get("/.well-known/agent-card.json").status_code == 404,
        "A2A-style agent card was enabled without opt-in",
    )
    require(
        client.post("/message:send", json={"data": {"query": SMOKE_QUERY}}).status_code == 404,
        "A2A-style message endpoint was enabled without opt-in",
    )
    a2a_manifest = a2a_client.get("/manifest").json()
    require(
        "a2a-message" in a2a_manifest["capabilities"],
        "A2A-style capability missing after opt-in",
    )
    a2a_health_response = a2a_client.get("/health")
    require(a2a_health_response.status_code == 200, "A2A-style health check failed")
    assert_health_payload(
        a2a_health_response.json(),
        fixture,
        "A2A-style health",
        a2a_manifest,
        enable_a2a_compat=True,
    )
    assert_no_private_root_leak(a2a_health_response.text, fixture, "A2A-style health")

    agent_card = a2a_client.get("/.well-known/agent-card.json").json()
    require(agent_card["url"] == "/message:send", "A2A-style agent card URL changed")

    a2a_message = a2a_client.post(
        "/message:send",
        json={
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": SMOKE_QUERY}],
            }
        },
    ).json()
    require(a2a_message["status"] == "completed", "A2A-style message did not complete")
    context_data = context_artifact(a2a_message)
    require(context_data["answerable"] is True, "A2A-style context was not answerable")
    require(context_data["evidence"], "A2A-style context returned no evidence")

    require(tree_hash(fixture) == before_hash, "fixture source files changed during smoke")
    print("source boundary smoke passed")


def wheel_cli_smoke(
    uv: str, wheel: Path, fixture: Path, *, allow_network_install: bool = False
) -> None:
    print(f"Using wheel: {display_path(wheel)}")
    with tempfile.TemporaryDirectory(prefix="llmwiki-serve-wheel-smoke-") as temp_dir:
        venv_dir = Path(temp_dir) / "venv"
        run([sys.executable, "-m", "venv", str(venv_dir)], cwd=PROJECT_ROOT)

        python = venv_python(venv_dir)
        install_command = [uv, "pip", "install", "--python", str(python), str(wheel)]
        if allow_network_install:
            run(install_command, cwd=PROJECT_ROOT)
        else:
            try:
                run(
                    [uv, "pip", "install", "--offline", "--python", str(python), str(wheel)],
                    cwd=PROJECT_ROOT,
                    env={**os.environ, "UV_OFFLINE": "1"},
                )
            except SmokeFailure as exc:
                raise SmokeFailure(
                    "offline wheel dependency install failed. Run `uv sync --extra dev --locked` "
                    "to warm the uv cache, or rerun this smoke with "
                    "`--allow-network-install` on local machines where cache-only validation is "
                    "not required."
                ) from exc

        cli = venv_executable(venv_dir, "llmwiki-serve")
        manifest = run_json([str(cli), "manifest", str(fixture)], cwd=PROJECT_ROOT)
        require(manifest["title"] == "Sample Packaging LLMWiki", "wheel manifest title mismatch")
        require(manifest["page_count"] == 5, "wheel manifest page count mismatch")
        require(
            manifest["approved_page_count"] == 4,
            "wheel manifest approved page count mismatch",
        )
        require(manifest["root"] == str(fixture), "wheel manifest did not expose local root")

        context = run_json(
            [str(cli), "query", str(fixture), SMOKE_QUERY, "--limit", "4"],
            cwd=PROJECT_ROOT,
        )
        require(context["query"] == SMOKE_QUERY, "wheel query text mismatch")
        require(context["answerable"] is True, "wheel query was not answerable")
        require(context["evidence"], "wheel query returned no evidence")
        require(
            all("draft" not in item["path"] for item in context["evidence"]),
            "wheel query evidence included a draft page",
        )
        source_refs = run_json([str(cli), "source-refs", str(fixture)], cwd=PROJECT_ROOT)
        assert_source_refs_payload(source_refs, fixture, "wheel CLI source refs")
        assert_no_private_root_leak(source_refs, fixture, "wheel CLI source refs")

        source_bundle = run_json([str(cli), "source-bundle", str(fixture)], cwd=PROJECT_ROOT)
        assert_source_bundle_payload(
            source_bundle,
            fixture,
            "wheel CLI source bundle",
            projection_graph_counts(fixture),
        )
        assert_no_private_root_leak(source_bundle, fixture, "wheel CLI source bundle")

        assert_cli_failure(
            [str(cli), "query", str(fixture), SMOKE_QUERY, "--limit", "0"],
            cwd=PROJECT_ROOT,
            expected_text="Invalid value",
        )
        empty_root = Path(temp_dir) / "empty-wiki"
        empty_root.mkdir()
        assert_cli_failure(
            [str(cli), "manifest", str(empty_root)],
            cwd=PROJECT_ROOT,
            expected_text="No supported wiki files were found",
        )
        wheel_api_smoke(python, fixture, Path(temp_dir), manifest)

    print("wheel CLI and API smoke passed")


def wheel_api_smoke(
    python: Path,
    fixture: Path,
    temp_dir: Path,
    expected_manifest: dict[str, Any],
) -> None:
    payload = run_json(
        [str(python), "-I", "-c", WHEEL_API_SMOKE_SCRIPT, str(fixture)],
        cwd=temp_dir,
    )
    api_file = Path(str(payload["api_file"])).resolve()
    require(api_file.is_file(), "wheel API smoke could not locate installed API module")
    require(
        payload.get("package_version") == PACKAGE_VERSION,
        "wheel package __version__ changed",
    )
    require(
        not path_is_relative_to(api_file, (PROJECT_ROOT / "src").resolve()),
        "wheel API smoke imported the source checkout instead of installed package",
    )

    projection_graph = response_json(
        payload,
        "projection_graph",
        "wheel projection graph",
    )
    expected_projection_graph_counts = graph_counts(
        projection_graph,
        "wheel projection graph",
    )

    health_http = response_json(payload, "health", "wheel HTTP health")
    assert_health_payload(
        health_http,
        fixture,
        "wheel HTTP health",
        expected_manifest,
        enable_a2a_compat=False,
    )
    assert_no_private_root_leak(
        response_envelope(payload, "health", "wheel HTTP health"),
        fixture,
        "wheel HTTP health envelope",
    )

    source_refs_http = response_json(payload, "source_refs", "wheel HTTP source refs")
    assert_source_refs_payload(source_refs_http, fixture, "wheel HTTP source refs")
    assert_no_private_root_leak(
        response_envelope(payload, "source_refs", "wheel HTTP source refs"),
        fixture,
        "wheel HTTP source refs envelope",
    )

    source_bundle_http = response_json(payload, "source_bundle", "wheel HTTP source bundle")
    assert_source_bundle_payload(
        source_bundle_http,
        fixture,
        "wheel HTTP source bundle",
        expected_projection_graph_counts,
    )
    assert_no_private_root_leak(
        response_envelope(payload, "source_bundle", "wheel HTTP source bundle"),
        fixture,
        "wheel HTTP source bundle envelope",
    )

    graph_neighbors_http = response_json(
        payload,
        "graph_neighbors",
        "wheel HTTP graph neighborhood",
    )
    assert_graph_neighbors_payload(
        graph_neighbors_http,
        fixture,
        "wheel HTTP graph neighborhood",
    )
    assert_no_private_root_leak(
        response_envelope(payload, "graph_neighbors", "wheel HTTP graph neighborhood"),
        fixture,
        "wheel HTTP graph neighborhood envelope",
    )

    mcp_graph_neighbors = response_json(
        payload,
        "mcp_graph_neighbors",
        "wheel MCP-style graph neighbors",
    )
    require(
        "error" not in mcp_graph_neighbors,
        "wheel MCP-style graph neighbors returned an error",
    )
    require(
        mcp_graph_neighbors["result"] == graph_neighbors_http,
        "wheel MCP-style graph neighbors differed from HTTP graph neighborhood",
    )
    assert_no_private_root_leak(
        response_envelope(
            payload,
            "mcp_graph_neighbors",
            "wheel MCP-style graph neighbors",
        ),
        fixture,
        "wheel MCP-style graph neighbors envelope",
    )

    mcp_source_bundle = response_json(
        payload,
        "mcp_source_bundle",
        "wheel MCP-style source bundle",
    )
    require(
        "error" not in mcp_source_bundle,
        "wheel MCP-style source bundle returned an error",
    )
    require(
        mcp_source_bundle["result"] == source_bundle_http,
        "wheel MCP-style source bundle differed from HTTP source bundle",
    )
    assert_source_bundle_payload(
        mcp_source_bundle["result"],
        fixture,
        "wheel MCP-style source bundle",
        expected_projection_graph_counts,
    )
    assert_no_private_root_leak(
        response_envelope(payload, "mcp_source_bundle", "wheel MCP-style source bundle"),
        fixture,
        "wheel MCP-style source bundle envelope",
    )

    stream_tools = response_json(
        payload,
        "stream_tools",
        "wheel MCP Streamable HTTP tools/list",
    )
    tool_names = {tool["name"] for tool in stream_tools["result"]["tools"]}
    require(
        {
            "llmwiki_context",
            "llmwiki_search",
            "llmwiki_read",
            "llmwiki_graph",
            "llmwiki_graph_neighbors",
            "llmwiki_source_refs",
            "llmwiki_source_bundle",
        }
        <= tool_names,
        "wheel MCP Streamable HTTP tools/list missing expected tool(s)",
    )
    assert_no_private_root_leak(
        response_envelope(payload, "stream_tools", "wheel MCP Streamable HTTP tools/list"),
        fixture,
        "wheel MCP Streamable HTTP tools/list envelope",
    )

    stream_graph_neighbors = response_json(
        payload,
        "stream_graph_neighbors",
        "wheel MCP Streamable HTTP graph neighbors",
    )
    require(
        stream_graph_neighbors["result"]["isError"] is False,
        "wheel MCP Streamable HTTP graph neighbors returned an error",
    )
    require(
        stream_graph_neighbors["result"]["structuredContent"] == graph_neighbors_http,
        "wheel MCP Streamable HTTP graph neighbors differed from HTTP graph neighborhood",
    )
    assert_graph_neighbors_payload(
        stream_graph_neighbors["result"]["structuredContent"],
        fixture,
        "wheel MCP Streamable HTTP graph neighbors",
    )
    assert_no_private_root_leak(
        response_envelope(
            payload,
            "stream_graph_neighbors",
            "wheel MCP Streamable HTTP graph neighbors",
        ),
        fixture,
        "wheel MCP Streamable HTTP graph neighbors envelope",
    )

    stream_source_bundle = response_json(
        payload,
        "stream_source_bundle",
        "wheel MCP Streamable HTTP source bundle",
    )
    require(
        stream_source_bundle["result"]["isError"] is False,
        "wheel MCP Streamable HTTP source bundle returned an error",
    )
    require(
        stream_source_bundle["result"]["structuredContent"] == source_bundle_http,
        "wheel MCP Streamable HTTP source bundle differed from HTTP source bundle",
    )
    assert_source_bundle_payload(
        stream_source_bundle["result"]["structuredContent"],
        fixture,
        "wheel MCP Streamable HTTP source bundle",
        expected_projection_graph_counts,
    )
    assert_no_private_root_leak(
        response_envelope(
            payload,
            "stream_source_bundle",
            "wheel MCP Streamable HTTP source bundle",
        ),
        fixture,
        "wheel MCP Streamable HTTP source bundle envelope",
    )

    print("wheel HTTP and MCP smoke passed")


def response_envelope(payload: dict[str, Any], key: str, surface: str) -> dict[str, Any]:
    response = payload.get(key)
    require(isinstance(response, dict), f"{surface} response envelope missing")
    require(
        response.get("status_code") == 200,
        f"{surface} returned status {response.get('status_code')}",
    )
    return response


def response_json(payload: dict[str, Any], key: str, surface: str) -> dict[str, Any]:
    response = response_envelope(payload, key, surface)
    body = response.get("json")
    require(isinstance(body, dict), f"{surface} returned non-object JSON")
    return body


def assert_wheel_contents(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        missing = sorted(EXPECTED_WHEEL_FILES - names)
        forbidden = sorted(name for name in names if name.startswith(FORBIDDEN_WHEEL_PREFIXES))
        entry_points = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        metadata = [name for name in names if name.endswith(".dist-info/METADATA")]
        license_files = [
            name
            for name in names
            if name.endswith(".dist-info/licenses/LICENSE")
            or name.endswith(".dist-info/licenses/THIRD_PARTY_NOTICES.md")
        ]
        require(
            not missing,
            f"wheel missing expected package file(s): {', '.join(missing)}",
        )
        require(
            not forbidden,
            f"wheel included repository-only file(s): {', '.join(forbidden[:5])}",
        )
        require(metadata, "wheel missing dist-info METADATA")
        require(entry_points, "wheel missing console entry point metadata")
        require(
            len(license_files) == 2,
            "wheel missing expected dist-info license file(s)",
        )
        entry_point_text = archive.read(entry_points[0]).decode("utf-8")

    require(
        "llmwiki-serve = llmwiki_serve.cli:app" in entry_point_text,
        "wheel console script metadata changed",
    )
    print("wheel contents smoke passed")


def assert_sdist_contents(sdist: Path) -> None:
    print(f"Using sdist: {display_path(sdist)}")
    with tarfile.open(sdist, "r:gz") as archive:
        root, names = normalized_sdist_file_names(archive)
        expected = EXPECTED_SDIST_FILES | EXPECTED_SDIST_SOURCE_FILES
        missing = sorted(expected - names)
        forbidden = sorted(
            (name, reason)
            for name in names
            if (reason := forbidden_sdist_path_reason(name)) is not None
        )
        forbidden_content = sorted(scan_sdist_content_canaries(archive, root))
        pkg_info = read_sdist_text(archive, root, "PKG-INFO")
        pyproject = read_sdist_text(archive, root, "pyproject.toml")

    require(
        not missing,
        f"sdist missing expected file(s): {', '.join(missing)}",
    )
    require(
        not forbidden,
        "sdist included forbidden file(s): "
        + ", ".join(f"{name} ({reason})" for name, reason in forbidden[:8]),
    )
    require(
        not forbidden_content,
        "sdist included forbidden content canary/canaries: "
        + ", ".join(f"{name} ({reason})" for name, reason in forbidden_content[:8]),
    )
    require("Name: llmwiki-serve\n" in pkg_info, "sdist PKG-INFO package name changed")
    project_version = read_project_version(pyproject)
    require(
        project_version == PACKAGE_VERSION,
        "sdist pyproject.toml version differs from package __version__",
    )
    require(
        f"Version: {project_version}\n" in pkg_info,
        "sdist PKG-INFO package version changed",
    )
    require("[project]\n" in pyproject, "sdist missing project metadata in pyproject.toml")
    require(
        "[tool.hatch.build.targets.sdist]\n" in pyproject,
        "sdist missing explicit hatch sdist policy",
    )
    print("sdist contents smoke passed")


def normalized_sdist_file_names(archive: tarfile.TarFile) -> tuple[str, set[str]]:
    roots: set[str] = set()
    file_names: set[str] = set()

    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        require(not path.is_absolute(), f"sdist contains absolute path: {member.name}")
        parts = path.parts
        require(parts and ".." not in parts, f"sdist contains unsafe path: {member.name}")
        require(
            not member.issym() and not member.islnk(),
            f"sdist contains link entry: {member.name}",
        )
        roots.add(parts[0])
        if member.isfile() and len(parts) > 1:
            file_names.add(PurePosixPath(*parts[1:]).as_posix())

    require(len(roots) == 1, f"sdist should contain one root directory, found: {sorted(roots)}")
    return roots.pop(), file_names


def read_sdist_text(archive: tarfile.TarFile, root: str, relative_path: str) -> str:
    file = archive.extractfile(f"{root}/{relative_path}")
    require(file is not None, f"sdist missing readable file: {relative_path}")
    return file.read().decode("utf-8").replace("\r\n", "\n")


def scan_sdist_content_canaries(archive: tarfile.TarFile, root: str) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for member in archive.getmembers():
        if not member.isfile():
            continue
        path = PurePosixPath(member.name)
        if not path.parts or path.parts[0] != root or len(path.parts) < 2:
            continue
        relative_name = PurePosixPath(*path.parts[1:]).as_posix()
        file = archive.extractfile(member)
        require(file is not None, f"sdist missing readable file: {relative_name}")
        data = file.read()
        for reason, canary in FORBIDDEN_SDIST_CONTENT_CANARIES:
            if canary in data:
                hits.append((relative_name, reason))
    return hits


def read_project_version(pyproject: str) -> str:
    try:
        metadata = tomllib.loads(pyproject)
    except tomllib.TOMLDecodeError as exc:
        raise SmokeFailure("sdist pyproject.toml could not be parsed") from exc

    project = metadata.get("project")
    require(isinstance(project, dict), "sdist pyproject.toml missing [project] table")
    version = project.get("version")
    require(isinstance(version, str) and version, "sdist pyproject.toml missing project.version")
    return version


def forbidden_sdist_path_reason(name: str) -> str | None:
    parts = PurePosixPath(name.lower()).parts
    filename = parts[-1]
    if any(component in FORBIDDEN_SDIST_COMPONENTS for component in parts):
        return "cache, VCS, build, or generated artifact path"
    if any(component.endswith(".egg-info") for component in parts):
        return "egg-info build metadata"
    if any(component == ".env" or component.startswith(".env.") for component in parts):
        return "local environment file"
    if filename in FORBIDDEN_SDIST_FILENAMES:
        return "local credential, cache, coverage, or generated artifact file"
    if filename.startswith(("secret", "secrets", "credential", "credentials")):
        return "credential-like filename"
    if filename.endswith(FORBIDDEN_SDIST_SUFFIXES):
        return "compiled, credential, database, or runtime log artifact"
    return None


def assert_local_only_cors(client: TestClient) -> None:
    preflight_headers = {"access-control-request-method": "GET"}
    localhost = client.options(
        "/health",
        headers={"origin": "http://localhost:5173", **preflight_headers},
    )
    loopback = client.options(
        "/health",
        headers={"origin": "http://127.0.0.1:3000", **preflight_headers},
    )
    ipv6 = client.options(
        "/health",
        headers={"origin": "http://[::1]:5173", **preflight_headers},
    )
    foreign = client.options(
        "/health",
        headers={"origin": "https://example.com", **preflight_headers},
    )

    require(
        localhost.headers["access-control-allow-origin"] == "http://localhost:5173",
        "CORS rejected localhost",
    )
    require(
        loopback.headers["access-control-allow-origin"] == "http://127.0.0.1:3000",
        "CORS rejected IPv4 loopback",
    )
    require(
        ipv6.headers["access-control-allow-origin"] == "http://[::1]:5173",
        "CORS rejected IPv6 loopback",
    )
    require(localhost.headers["access-control-allow-origin"] != "*", "CORS used wildcard origin")
    require(
        foreign.headers.get("access-control-allow-origin") is None,
        "CORS allowed public origin",
    )


def assert_safe_mcp_errors(client: TestClient, fixture: Path) -> None:
    unsupported = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 3, "method": f"method:{fixture}"},
    ).json()
    unknown = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": str(fixture), "arguments": {}},
        },
    ).json()

    with tempfile.TemporaryDirectory(prefix="llmwiki-serve-missing-root-") as temp_dir:
        missing_root = Path(temp_dir) / "private" / "missing-wiki"
        internal = (
            TestClient(create_app(missing_root))
            .post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {"name": "llmwiki_context", "arguments": {"query": "release"}},
                },
            )
            .json()
        )

        encoded_internal = json.dumps(internal)
        require(str(missing_root) not in encoded_internal, "MCP internal error exposed path")
        require("LLMWiki root" not in encoded_internal, "MCP internal error exposed exception")

    require(
        unsupported["error"] == {"code": -32601, "message": MCP_UNSUPPORTED_METHOD_MESSAGE},
        "MCP unsupported method error changed",
    )
    require(
        unknown["error"] == {"code": -32602, "message": MCP_UNKNOWN_TOOL_MESSAGE},
        "MCP unknown tool error changed",
    )
    require(
        internal["error"] == {"code": -32000, "message": MCP_INTERNAL_FAILURE_MESSAGE},
        "MCP internal error changed",
    )

    encoded_contract = json.dumps({"unsupported": unsupported, "unknown": unknown})
    require(str(fixture) not in encoded_contract, "MCP contract error exposed path")


def assert_health_payload(
    payload: dict[str, Any],
    fixture: Path,
    surface: str,
    expected_manifest: dict[str, Any],
    *,
    enable_a2a_compat: bool,
) -> None:
    require(isinstance(payload, dict), f"{surface} returned non-object payload")
    require(payload["status"] == "ok", f"{surface} status changed")
    require(payload["service"] == "llmwiki-serve", f"{surface} service changed")
    require(payload["version"] == PACKAGE_VERSION, f"{surface} version changed")
    require(
        payload["source"]
        == {
            "source_id": expected_manifest["source_id"],
            "bundle_id": expected_manifest["bundle_id"],
            "public_uri": expected_manifest["public_uri"],
            "title": expected_manifest["title"],
            "adapter": expected_manifest["adapter"],
            "implementation": expected_manifest["implementation"],
            "page_count": expected_manifest["page_count"],
            "approved_page_count": expected_manifest["approved_page_count"],
            "projection": expected_manifest["projection"],
        },
        f"{surface} source metadata differed from manifest",
    )

    capabilities = set(payload["capabilities"])
    require(
        capabilities == set(expected_manifest["capabilities"]),
        f"{surface} capabilities differed from manifest",
    )
    require(
        {
            "llmwiki_source_bundle",
            "llmwiki_context",
            "llmwiki_search",
            "llmwiki_read",
            "llmwiki_graph",
            "llmwiki_graph_neighbors",
            "llmwiki_source_refs",
            "mcp-jsonrpc",
            "mcp-streamable-http",
        }
        <= capabilities,
        f"{surface} missing required capabilities",
    )
    require(
        ("a2a-message" in capabilities) is enable_a2a_compat,
        f"{surface} A2A capability did not match opt-in state",
    )
    require(
        payload["endpoints"] == expected_health_endpoints(enable_a2a_compat),
        f"{surface} endpoints changed",
    )
    require(
        payload["cors"]
        == {
            "mode": "local-dev-allowlist",
            "local_dev_origins": True,
            "explicit_origin_count": 0,
        },
        f"{surface} CORS discovery changed",
    )
    assert_no_private_root_leak(payload, fixture, surface)


def expected_health_endpoints(enable_a2a_compat: bool) -> dict[str, str]:
    return {
        "health": "/health",
        "manifest": "/manifest",
        "source_bundle": "/source-bundle",
        "source_refs": "/source-refs",
        "query": "/query",
        "search": "/search",
        "read": "/read/{page_id}",
        "graph": "/graph",
        "graph_neighborhood": "/graph/neighborhood",
        "mcp_jsonrpc": "/mcp",
        "mcp_streamable_http": "/mcp/stream",
        "openapi": "/openapi.json",
        "docs": "/docs",
        "a2a_agent_card": "/.well-known/agent-card.json" if enable_a2a_compat else "",
        "a2a_message_send": "/message:send" if enable_a2a_compat else "",
    }


def assert_source_bundle_payload(
    payload: dict[str, Any],
    fixture: Path,
    surface: str,
    expected_graph_counts: tuple[int, int],
) -> None:
    require(isinstance(payload, dict), f"{surface} returned non-object payload")
    require(payload["source_id"] == "sample-packaging-llmwiki", f"{surface} source id changed")
    require(
        payload["bundle_id"].startswith("sample-packaging-llmwiki:sha256:"),
        f"{surface} bundle id changed",
    )
    require(
        payload["public_uri"] == "llmwiki://sample-packaging-llmwiki",
        f"{surface} public URI changed",
    )

    projection = payload["projection"]
    require(projection["signature"].startswith("sha256:"), f"{surface} signature missing")
    require(projection["page_count"] == 5, f"{surface} projection page count changed")
    require(
        projection["approved_page_count"] == 4,
        f"{surface} projection approved page count changed",
    )
    expected_node_count, expected_edge_count = expected_graph_counts
    require(
        projection["graph_node_count"] == expected_node_count,
        f"{surface} projection graph node count changed",
    )
    require(
        projection["graph_edge_count"] == expected_edge_count,
        f"{surface} projection graph edge count changed",
    )

    raw_origins = payload["raw_origins"]
    require(raw_origins["enabled"] is False, f"{surface} raw origins were enabled")
    require(
        raw_origins["metadata_only"] is True,
        f"{surface} raw origins were not metadata-only",
    )
    require(
        isinstance(raw_origins["public_root_labels"], list),
        f"{surface} raw origin labels were not a list",
    )

    require(
        "llmwiki_source_bundle" in payload["capabilities"],
        f"{surface} missing source-bundle capability",
    )
    require(
        "llmwiki_source_refs" in payload["capabilities"],
        f"{surface} missing source-refs capability",
    )
    require(
        "llmwiki_graph_neighbors" in payload["capabilities"],
        f"{surface} missing graph-neighborhood capability",
    )
    assert_source_refs_payload(
        {
            "source_id": payload["source_id"],
            "bundle_id": payload["bundle_id"],
            "source_refs": payload["source_refs"],
        },
        fixture,
        f"{surface} embedded source refs",
    )
    assert_no_private_root_leak(payload, fixture, surface)


def assert_graph_neighbors_payload(payload: dict[str, Any], fixture: Path, surface: str) -> None:
    require(isinstance(payload, dict), f"{surface} returned non-object payload")
    require(payload["seeds"] == ["page:hot"], f"{surface} did not resolve hot seed")
    require(payload["unmatched"] == [], f"{surface} unexpectedly reported unmatched seed")
    require(payload["depth"] == 1, f"{surface} depth changed")
    require(payload["direction"] == "both", f"{surface} direction changed")
    require(payload["relations"] == [], f"{surface} relation filters changed")

    nodes = payload["nodes"]
    edges = payload["edges"]
    require(isinstance(nodes, list), f"{surface} nodes were not a list")
    require(isinstance(edges, list), f"{surface} edges were not a list")
    require("page:hot" in {node.get("id") for node in nodes}, f"{surface} omitted seed page")
    require(edges, f"{surface} returned no edges for hot.md")

    for node in nodes:
        require(isinstance(node, dict), f"{surface} node was not an object")
        node_id = node.get("id", "")
        path = node.get("path", "")
        require(isinstance(node_id, str), f"{surface} node id was not text")
        require("draft" not in node_id.lower(), f"{surface} exposed a draft node")
        if path:
            require(isinstance(path, str), f"{surface} node path was not text")
            require(
                not Path(path).is_absolute() and not PurePosixPath(path).is_absolute(),
                f"{surface} node path exposed an absolute path",
            )
            require("draft" not in path.lower(), f"{surface} node path exposed a draft")

    for edge in edges:
        require(isinstance(edge, dict), f"{surface} edge was not an object")
        for key in ("source", "target", "relation"):
            value = edge.get(key, "")
            require(isinstance(value, str) and value, f"{surface} edge {key} missing")
            require("draft" not in value.lower(), f"{surface} edge exposed a draft")

    assert_no_private_root_leak(payload, fixture, surface)


def projection_graph_counts(fixture: Path) -> tuple[int, int]:
    with TestClient(create_app(fixture, allow_drafts=True)) as client:
        graph = client.get("/graph?limit=2000&include_drafts=true").json()
    return graph_counts(graph, "projection graph")


def graph_counts(graph: dict[str, Any], surface: str) -> tuple[int, int]:
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    require(isinstance(nodes, list), f"{surface} nodes were not a list")
    require(isinstance(edges, list), f"{surface} edges were not a list")
    require(nodes, f"{surface} returned no nodes")
    require(edges, f"{surface} returned no edges")
    return len(nodes), len(edges)


def assert_source_refs_payload(payload: dict[str, Any], fixture: Path, surface: str) -> None:
    require(isinstance(payload, dict), f"{surface} returned non-object payload")
    require(payload["source_id"] == "sample-packaging-llmwiki", f"{surface} source id changed")
    require(
        payload["bundle_id"].startswith("sample-packaging-llmwiki:sha256:"),
        f"{surface} bundle id changed",
    )
    source_refs = payload["source_refs"]
    require(isinstance(source_refs, list), f"{surface} source_refs was not a list")
    require(source_refs, f"{surface} returned no source refs")

    labels = {item.get("label") for item in source_refs if isinstance(item, dict)}
    expected_labels = {"SRC-ART-001", "SRC-HOT", "SRC-INDEX", "SRC-RETURN-001"}
    require(expected_labels <= labels, f"{surface} missing approved source refs")
    require("SRC-DRAFT" not in labels, f"{surface} exposed draft source refs")

    for item in source_refs:
        require(isinstance(item, dict), f"{surface} source ref was not an object")
        require(item["kind"] == "source_ref", f"{surface} source ref kind changed")
        require(item["id"], f"{surface} source ref id missing")
        require(item["label"], f"{surface} source ref label missing")
        require(
            item["uri"].startswith("llmwiki://sample-packaging-llmwiki/source-refs/"),
            f"{surface} source ref URI was not opaque",
        )
        require(item["uri"].endswith(f"/{item['id']}"), f"{surface} source ref URI id mismatch")
        require(item["linked_pages"], f"{surface} source ref had no linked pages")
        require(item["linked_page_ids"], f"{surface} source ref had no linked page ids")
        for page in item["linked_pages"]:
            require(isinstance(page, str), f"{surface} linked page was not text")
            require(
                not Path(page).is_absolute() and not PurePosixPath(page).is_absolute(),
                f"{surface} linked page exposed an absolute path",
            )
            require("draft" not in page.lower(), f"{surface} linked page exposed a draft")
        assert_no_private_root_leak(item, fixture, surface)


def assert_no_private_root_leak(payload: Any, fixture: Path, surface: str) -> None:
    markers = private_root_markers(fixture)
    for value in payload_strings(payload):
        for marker in markers:
            require(marker not in value, f"{surface} leaked private root path")
    encoded = json.dumps(payload, sort_keys=True)
    for marker in markers:
        require(marker not in encoded, f"{surface} leaked private root path")


def private_root_markers(fixture: Path) -> frozenset[str]:
    candidates = {str(fixture), str(fixture.parent), fixture.as_posix(), fixture.parent.as_posix()}
    escaped = {json.dumps(marker)[1:-1] for marker in candidates}
    return frozenset(marker for marker in candidates | escaped if marker)


def payload_strings(payload: Any) -> list[str]:
    if isinstance(payload, str):
        return [payload]
    if isinstance(payload, dict):
        values: list[str] = []
        for key, value in payload.items():
            values.extend(payload_strings(key))
            values.extend(payload_strings(value))
        return values
    if isinstance(payload, list):
        values = []
        for item in payload:
            values.extend(payload_strings(item))
        return values
    return []


def assert_mcp_streamable_http(fixture: Path) -> None:
    headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }
    expected_projection_graph_counts = projection_graph_counts(fixture)
    with TestClient(
        create_app(fixture),
        base_url="http://127.0.0.1:8000",
        follow_redirects=False,
    ) as client:
        tools = client.post(
            "/mcp/stream",
            json={"jsonrpc": "2.0", "id": 6, "method": "tools/list"},
            headers=headers,
        ).json()
        context = client.post(
            "/mcp/stream",
            json={
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "llmwiki_context",
                    "arguments": {"query": SMOKE_QUERY, "limit": 4},
                },
            },
            headers=headers,
        ).json()
        source_bundle = client.post(
            "/mcp/stream",
            json={
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {
                    "name": "llmwiki_source_bundle",
                    "arguments": {},
                },
            },
            headers=headers,
        ).json()
        graph_neighbors = client.post(
            "/mcp/stream",
            json={
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": "llmwiki_graph_neighbors",
                    "arguments": {"seed": "hot", "depth": 1, "limit": 20},
                },
            },
            headers=headers,
        ).json()

    tool_names = {tool["name"] for tool in tools["result"]["tools"]}
    require("llmwiki_context" in tool_names, "MCP Streamable HTTP tools/list missing context")
    require(
        "llmwiki_graph_neighbors" in tool_names,
        "MCP Streamable HTTP tools/list missing graph neighbors",
    )
    require(
        "llmwiki_source_bundle" in tool_names,
        "MCP Streamable HTTP tools/list missing source bundle",
    )
    require(
        context["result"]["structuredContent"]["answerable"] is True,
        "MCP Streamable HTTP context was not answerable",
    )
    require(
        context["result"]["structuredContent"]["evidence"],
        "MCP Streamable HTTP context returned no evidence",
    )
    require(
        source_bundle["result"]["isError"] is False,
        "MCP Streamable HTTP source bundle returned an error",
    )
    require(
        graph_neighbors["result"]["isError"] is False,
        "MCP Streamable HTTP graph neighbors returned an error",
    )
    require(
        "page:hot"
        in {node["id"] for node in graph_neighbors["result"]["structuredContent"]["nodes"]},
        "MCP Streamable HTTP graph neighbors omitted seed page",
    )
    assert_source_bundle_payload(
        source_bundle["result"]["structuredContent"],
        fixture,
        "MCP Streamable HTTP source bundle",
        expected_projection_graph_counts,
    )
    assert_no_private_root_leak(
        source_bundle,
        fixture,
        "MCP Streamable HTTP source bundle envelope",
    )
    print("MCP Streamable HTTP smoke passed")


def build_distribution(uv: str, dist_dir: Path) -> tuple[Path, Path]:
    dist_dir.mkdir(parents=True, exist_ok=True)
    run(
        [uv, "build", "--offline", "--out-dir", str(dist_dir)],
        cwd=PROJECT_ROOT,
        env={**os.environ, "UV_OFFLINE": "1"},
    )
    return require_latest_wheel(dist_dir), require_latest_sdist(dist_dir)


def run_cli_json(*args: str) -> dict[str, Any]:
    return run_json([llmwiki_cli(), *args], cwd=PROJECT_ROOT)


def llmwiki_cli() -> str:
    cli = shutil.which("llmwiki-serve")
    require(
        cli is not None,
        "llmwiki-serve command is not on PATH; run `uv run python scripts/release_smoke.py`",
    )
    return cli


def require_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise SmokeFailure(f"required executable not found on PATH: {name}")
    return executable


def latest_wheel(dist_dir: Path) -> Path | None:
    wheels = sorted(dist_dir.glob("*.whl"), key=lambda path: path.stat().st_mtime)
    return wheels[-1] if wheels else None


def latest_sdist(dist_dir: Path) -> Path | None:
    sdists = sorted(dist_dir.glob("*.tar.gz"), key=lambda path: path.stat().st_mtime)
    return sdists[-1] if sdists else None


def require_latest_wheel(dist_dir: Path) -> Path:
    wheel = latest_wheel(dist_dir)
    if wheel is None:
        raise SmokeFailure(f"no wheel found in {dist_dir}; run `uv build` or pass --wheel")
    return wheel


def require_latest_sdist(dist_dir: Path) -> Path:
    sdist = latest_sdist(dist_dir)
    if sdist is None:
        raise SmokeFailure(f"no sdist found in {dist_dir}; run `uv build` or pass --sdist")
    return sdist


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stdout, end="", file=sys.stdout)
        print(result.stderr, end="", file=sys.stderr)
        raise SmokeFailure(f"command failed with exit code {result.returncode}: {command}")
    return result


def run_json(command: list[str], *, cwd: Path) -> dict[str, Any]:
    result = run(command, cwd=cwd)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(result.stdout, end="", file=sys.stdout)
        raise SmokeFailure(f"command did not emit JSON: {command}") from exc
    if not isinstance(data, dict):
        raise SmokeFailure(f"command emitted non-object JSON: {command}")
    return data


def assert_cli_failure(command: list[str], *, cwd: Path, expected_text: str) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    combined_output = result.stdout + result.stderr
    require(result.returncode != 0, f"CLI command unexpectedly succeeded: {command}")
    require(expected_text in combined_output, f"CLI failure did not mention {expected_text!r}")
    require("Traceback" not in combined_output, "CLI failure exposed a traceback")


def context_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    for artifact in payload.get("artifacts", []):
        if artifact.get("name") != "llmwiki_context":
            continue
        for part in artifact.get("parts", []):
            data = part.get("data")
            if part.get("kind") == "data" and isinstance(data, dict):
                return data
    raise SmokeFailure("missing llmwiki_context artifact data")


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def venv_python(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def venv_executable(venv_dir: Path, name: str) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / f"{name}.exe"
    return venv_dir / "bin" / name


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as exc:
        print(f"release smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
