# Release Checklist

This repository is in early development. Use this checklist before publishing a
versioned release or public release candidate.

1. Confirm `CHANGELOG.md` describes notable CLI, HTTP, MCP-style JSON-RPC,
   MCP Streamable HTTP, opt-in A2A-style compatibility, adapter, security, and
   documentation changes.
2. Run the public validation gates:

   ```bash
   uv run ruff format --check .
   uv run ruff check .
   uv run mypy src
   PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider
   uv build
   uv run python scripts/release_smoke.py --wheel dist/*.whl --sdist dist/*.tar.gz
   uvx twine check dist/*
   ```

   On Windows PowerShell, set `PYTHONDONTWRITEBYTECODE` before the pytest gate:

   ```powershell
   $env:PYTHONDONTWRITEBYTECODE = "1"
   uv run pytest -p no:cacheprovider
   Remove-Item Env:\PYTHONDONTWRITEBYTECODE
   ```

   PowerShell does not expand `dist/*.whl` and `dist/*.tar.gz` for Python
   scripts. Select the artifacts explicitly:

   ```powershell
   uv build
   $wheel = Get-ChildItem dist -Filter *.whl | Sort-Object LastWriteTime | Select-Object -Last 1
   $sdist = Get-ChildItem dist -Filter *.tar.gz | Sort-Object LastWriteTime | Select-Object -Last 1
   uv run python scripts\release_smoke.py --wheel $wheel.FullName --sdist $sdist.FullName
   uvx twine check $wheel.FullName $sdist.FullName
   ```

   The release smoke verifies the bundled fixture from the source checkout
   through CLI, in-process HTTP, MCP-style JSON-RPC, MCP Streamable HTTP,
   opt-in A2A-style message shapes, draft filtering, local-only CORS, MCP error
   redaction, source immutability, generated OpenAPI contract freshness, sdist
   metadata and source contents, wheel package contents, and console script
   metadata. With `--wheel` and
   `--sdist`, it uses the exact artifacts from `uv build`, installs the wheel
   into a clean temporary venv with
   `uv pip install --offline`, and repeats fixture `manifest` and `query` CLI
   checks from the installed wheel. If a local machine has not cached all
   runtime dependency wheels, rerun with `--allow-network-install` and note that
   the wheel smoke used network-backed dependency installation. The sdist is
   intended to contain project
   source, tests, release documentation, and notices without CI configuration,
   credentials, caches, generated candidate samples, private runtime output, or
   build artifacts.

   For releases that change the optional Redis/Valkey projection store, also
   run the Redis gate against a non-sensitive fixture and an isolated namespace.
   This gate is optional for unrelated releases and must not publish Redis URLs,
   credentials, raw keys, cached values, local paths, or private wiki snippets:

   ```bash
   uv sync --extra dev --extra redis
   uv run pytest -q tests/test_service.py -k "projection_store or redis"
   LLMWIKI_REDIS_URL=redis://127.0.0.1:6379/0 \
     uv run pytest -q tests/test_redis_projection_store_integration.py
   ```

   Manual Redis smoke, if used, should start the server with explicit
   `--cache-namespace`, `--source-id`, and `--redis-failure-policy fail-fast`,
   then verify `/diagnostics/projection-store` redacts the Redis URL,
   credentials, and local root path. Treat Redis as sensitive derived storage:
   cached projections may include page text, front matter, source refs, graph
   metadata, and draft pages even when network responses withhold drafts.

   On Windows, stop any `llmwiki-serve` process that is running from this
   checkout before invoking `uv run` release gates. A running console script can
   hold `.venv\Scripts\llmwiki-serve.exe` open, which prevents uv from
   refreshing the environment. If the environment is already synced and the
   active server must stay up, run the smoke with the existing venv on `PATH`:

   ```powershell
   $env:PATH = "$(Get-Location)\.venv\Scripts;$env:PATH"
   .\.venv\Scripts\python.exe scripts\release_smoke.py
   ```

3. Run sample wiki smoke tests through a real local server:

   ```bash
   uv run llmwiki-serve manifest ./examples/sample-wiki
   uv run llmwiki-serve query ./examples/sample-wiki "what is in this wiki?"
   uv run llmwiki-serve serve ./examples/sample-wiki --host 127.0.0.1 --port 8765
   ```

   If port `8765` is already in use, rerun `serve` with another port and update
   the curl URLs below.

4. In another terminal, verify the sample HTTP, MCP-style, MCP Streamable HTTP,
   and opt-in A2A-style surfaces:

   ```bash
   curl -s http://127.0.0.1:8765/manifest

   curl -s http://127.0.0.1:8765/query \
     -H 'content-type: application/json' \
     -d '{"query":"required copy release readiness","limit":4}'

   curl -s http://127.0.0.1:8765/search \
     -H 'content-type: application/json' \
     -d '{"query":"requester return","limit":5}'

   curl -s http://127.0.0.1:8765/read/requester-return

   curl -s 'http://127.0.0.1:8765/graph?limit=120'

   curl -s 'http://127.0.0.1:8765/graph/neighborhood?seed=hot&depth=1&limit=20'

   curl -s http://127.0.0.1:8765/mcp \
     -H 'content-type: application/json' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"llmwiki_context","arguments":{"query":"required copy release readiness","limit":4}}}'

   curl -s http://127.0.0.1:8765/mcp/stream \
     -H 'accept: application/json, text/event-stream' \
     -H 'content-type: application/json' \
     -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
   ```

   A2A-style compatibility endpoints are disabled by default. To verify them,
   start the server with `--enable-a2a-compat` and run:

   ```bash
   curl -s http://127.0.0.1:8765/message:send \
     -H 'content-type: application/json' \
     -d '{"message":{"role":"user","parts":[{"kind":"text","text":"required copy release readiness"}]}}'
   ```

   `scripts/release_smoke.py` uses an in-process ASGI client for the same
   request bodies. The manual `serve` and `curl` checks verify those bodies
   through a real local HTTP listener.

5. Optionally run the network-dependent pinned public sample snapshot smoke:

   ```bash
   uv run python scripts/upstream_candidate_smoke.py --list-cases
   uv run python scripts/upstream_candidate_smoke.py --case atomic-compiler-basic
   uv run python scripts/upstream_candidate_smoke.py --timeout 300
   ```

   This gate fetches pinned public commits into a temporary directory outside
   the repository and checks static sample/template Markdown folders without
   mutating them. It is not default CI and is not upstream release
   certification. For the full run, record the PASS line for each case,
   including repo URL, pinned ref, source path, source file count, page counts,
   and projected graph size. Do not add cases that require credentials, desktop
   runtimes, LLM provider calls, or heavy application builds. Record whether any
   failure was a network fetch failure or a projection/service failure.

6. Smoke test at least one real non-sensitive wiki folder separately from the
   fixture:

   ```bash
   WIKI=/path/to/non-sensitive/wiki-folder

   uv run llmwiki-serve manifest "$WIKI"
   uv run llmwiki-serve query "$WIKI" "what is in this wiki?"
   uv run llmwiki-serve serve "$WIKI" --host 127.0.0.1 --port 8765
   ```

   Confirm `/query`, `/search`, `/read`, `/graph`, `/graph/neighborhood`,
   `/mcp`, and `/mcp/stream` return expected data for that real wiki without
   exposing private content in release notes, issue comments, logs, or
   generated artifacts. Confirm
   `/message:send` returns 404 by default and works only when the server is
   started with `--enable-a2a-compat`. Keep draft-serving disabled unless
   explicitly testing `--allow-drafts`, and confirm HTTP `/manifest` does not
   expose the local wiki root path.

   Treat fixture, generated 11-candidate suite, upstream smoke, and real-wiki
   results separately. Fixtures prove the checked-in local examples, projection
   layer, draft filtering, and read-only-source behavior still work. The
   generated 11-candidate suite proves compatible local output shapes only,
   including the DeepAgents `raw/`/`wiki/`/`log.md` workspace-layout variant.
   The optional upstream candidate smoke currently covers 10 pinned public
   sample snapshots included in the script. A real exported wiki smoke checks
   the caller's actual producer output, plugin settings, and content
   conventions; it is not covered by the bundled fixtures and should not
   publish private data.

   If testing `--producer-manifest`, verify it only with a non-sensitive local
   generated wiki whose producer reliably updates the marker after every
   ingest/compile run. Keep those results separate from the default strict
   source-scan smoke because producer manifest freshness intentionally changes
   the operator trust model.

7. Confirm README, CONTRIBUTING, architecture docs, and issue/PR templates
   reflect new setup steps, validation expectations, compatibility limits, or
   source-folder support.
   If the API surface changed, run `uv run python scripts/export_openapi.py`
   and commit the refreshed `docs/openapi.json`.
8. Confirm the release contains no credentials, token caches, private endpoint
   URLs, private paths, raw sensitive wiki content, Redis/Valkey cached
   projection payloads, local environment files, or generated artifacts that are
   not meant to ship. Confirm fixture and smoke inputs do not depend on
   symlinked Markdown/Org files or `graph/graph.json` sidecars; the server
   ignores those by default to keep serving inside the wiki root.
9. Confirm package metadata still lists the repository, issue tracker, homepage,
   Python baseline, runtime dependencies, and optional extras accurately. Redis
   release notes should say that `llmwiki-serve[redis]` is optional, the default
   install remains memory-only/no external service, Redis is a derived
   projection cache only, and Redis may contain sensitive derived wiki content
   including drafts. If the public deployment guide needs broader operator
   guidance, file or make the follow-up in `llmwiki-docs` without blocking this
   repository release checklist.
10. Treat publishing as a maintainer-owner gate. The repository includes a
    Trusted Publishing workflow at `.github/workflows/publish.yml`, but do not
    run it until the repository owner and PyPI project owner have configured the
    `llmwiki-serve` PyPI project or pending publisher, release permissions, and
    the GitHub `pypi` environment. Keep PyPI tokens and release credentials out
    of CI, logs, commits, and generated artifacts.

    The PyPI Trusted Publisher configuration should match:

    ```text
    PyPI project name: llmwiki-serve
    Owner: knowledge-bridge-labs
    Repository: llmwiki-serve
    Workflow file: publish.yml
    Environment: pypi
    ```

Before publishing to PyPI, run the central package-publication gate documented
in the sibling `llmwiki-docs` repository and confirm the toolchain release
status is at least `public-unpublished`.

Security support remains defined in `SECURITY.md`.
