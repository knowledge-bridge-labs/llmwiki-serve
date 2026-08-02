# Release Checklist

This repository is in early development. Use this checklist before publishing a
versioned release or public release candidate.

1. Confirm `CHANGELOG.md` describes notable CLI, HTTP, MCP-style JSON-RPC,
   MCP Streamable HTTP, opt-in A2A-style compatibility, adapter, security, and
   documentation changes.
2. Run the public validation gates:

   ```bash
   uv sync --locked
   uv run python -c "import importlib.util; assert importlib.util.find_spec('fastembed') is None; assert importlib.util.find_spec('numpy') is None"
   uv run llmwiki-serve query ./examples/sample-wiki "release readiness"
   uv sync --extra dev --extra vector --locked
   uv run ruff format --check .
   uv run ruff check .
   uv run mypy src
   uv run mypy scripts/benchmark_adapters/scifact_runner.py
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
   intended to contain project source, tests, release documentation, notices,
   and public-safe benchmark fixtures/schemas/runners needed by release tests
   without CI configuration, credentials, caches, generated report outputs,
   generated candidate samples, private runtime output, or build artifacts.

   For releases that change the optional Redis/Valkey projection store, also
   run the Redis gate against a non-sensitive fixture and an isolated namespace.
   This gate is optional for unrelated releases and must not publish raw Redis
   URLs, credentials, raw keys, cached values, local paths, or private wiki
   snippets:

   ```bash
   uv sync --extra dev --extra redis
   uv run pytest -q tests/test_service.py -k "projection_store or redis"
   LLMWIKI_REDIS_URL=redis://127.0.0.1:6379/0 \
     uv run pytest -q tests/test_redis_projection_store_integration.py
   ```

   Manual Redis smoke, if used, should start the server with explicit
   `--cache-namespace`, `--source-id`, and `--redis-failure-policy fail-fast`,
   then verify `/diagnostics/projection-store` reports `backend_kind: "redis"`
   and only a sanitized endpoint label with userinfo, passwords, query
   parameters, and fragments removed. Treat Redis as sensitive derived storage:
   cached projections may include page text, front matter, source refs, graph
   metadata, and draft pages even when network responses withhold drafts. Also
   confirm the operator has a retention plan: Redis keys are projection
   signature based and are not automatically expired by `llmwiki-serve`, so
   deployments should use Redis/Valkey eviction or TTL policy, rotate
   `--cache-namespace`, or delete a deployment namespace during maintenance.
   Record only sanitized pass/fail evidence in release notes or specs.

   Redis projection-store release-candidate validation recorded on 2026-07-22
   used a non-sensitive sample wiki, a loopback Docker Redis container, an
   isolated namespace, the gated live integration test
   `tests/test_redis_projection_store_integration.py`, and a manual smoke for
   `/manifest`, `/query`, and `/diagnostics/projection-store`. Diagnostics
   redaction passed, the manual namespace keys were cleaned up, and the
   container was stopped after the check. No raw Redis URL, credential, query
   parameter, raw key, cached payload, private path, or wiki snippet was
   recorded.

   For releases that change optional semantic retrieval preview behavior, also
   run the vector gates without publishing raw cache artifacts, local roots,
   model local paths, raw vectors, snippets, or private queries:

   ```bash
   uv sync --locked
   uv run python -c "import importlib.util; assert importlib.util.find_spec('fastembed') is None; assert importlib.util.find_spec('numpy') is None"
   uv run llmwiki-serve query ./examples/sample-wiki "release readiness"
   uv sync --extra dev --extra vector --locked
   uv run pytest -q tests/test_vector_retrieval.py
   uv run python -c "from llmwiki_serve.vector import FastEmbedProvider; print('vector extra import ok')"
   ```

   The default runtime smoke must run before installing `llmwiki-serve[vector]`
   and prove lexical/literal behavior still works without importing FastEmbed
   or NumPy. Lexical remains the default release behavior.
   Any real FastEmbed smoke should use a non-sensitive wiki, an external
   `--vector-cache-dir`, and the explicit
   `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` model. Default
   model access is local-files-only; first-use downloads require the operator to
   pass `--vector-model-download allow` or set
   `LLMWIKI_VECTOR_MODEL_DOWNLOAD=allow`. Record resolved FastEmbed, NumPy,
   model source revision, dimension, platform, and whether the model was already
   cached or downloaded. Do not claim Korean retrieval quality from the
   multilingual model label.

   Before any vector/hybrid release, confirm the gate covers the expert-review
   blockers in the semantic retrieval spec: cache-hit NumPy-failure fallback,
   immutable refresh/search retrieval snapshots, cross-process cold-build retry
   behavior, exclude-one and near-full filtered exact scoring memory paths,
   adversarial orientation inputs, negative/unanswerable diagnostics, draft
   isolation, and Korean/English/Unicode identifiers. Cross-process cold-build
   lock contention may produce a retryable build-in-progress failure after the
   lock timeout; it must not expose partial cache artifacts or silently switch
   retrieval modes.

   Hybrid release notes must describe the mode as lexical plus dense retrieval
   with weighted RRF and bounded optional read-only orientation hints. Do not
   describe it as universal orientation-first retrieval, GraphRAG, automatic
   hot/index/overview rewriting, or a general quality improvement. Retrieval
   must not create, replace, or modify source-owned `hot.md`, `index.md`, or
   `overview.md` files.

   Public vector/hybrid quality or performance claims require clean commit SHA
   reports on Windows and Ubuntu/DGX tied to the release revision. Existing
   Windows/DGX small/team dirty-snapshot runs are engineering evidence only.
   Supported-size wording must list the exact recorded corpus counts, chunk
   counts, vector dimensions, memory envelope, and platforms. Larger corpus
   claims are experimental until 10k, 50k, 100k, and 500k gates exist and pass.

   Negative-query reports may include false-positive, top-k, score-separation,
   and citation-precision diagnostics, but they must not claim calibrated
   abstention or a reliable no-evidence threshold. Do not claim poisoning
   safety, broad multilingual quality, SOTA quality, or vector-database-scale
   performance. New embedding models, rerankers, ANN indexes, Redis vector
   search, hosted vector databases, and remote embedding providers belong in
   future-experiment notes unless they are implemented and separately gated.

   For releases that change agent-guided lexical guidance, query variants, or
   the benchmark harness, also run the focused contract and harness gates:

   ```bash
   uv run pytest -q tests/test_agent_guided_lexical.py tests/test_agent_guided_lexical_runner.py tests/test_public_api.py
   uv run python scripts/benchmark_adapters/agent_guided_lexical_runner.py --fixture-dir benchmarks/agent_guided_lexical/fixture --output-report .runtime-logs/agent-guided-lexical-smoke.json
   uv run python scripts/export_openapi.py --check
   ```

   Release notes should describe agent-guided lexical as the direct-agent
   context/search/read workflow over the default lexical mode, not as a new
   `SearchMode`. The server returns guidance and accepts caller-supplied
   lexical variants, but it does not call an LLM, download a model, build
   embeddings, synthesize final answers, or write source files for this path.
   The tiny benchmark fixture and dirty-worktree reports are engineering
   evidence only. Public claims require clean-commit evidence and public-safe
   reports with no private paths, private endpoints, credentials, raw source
   content, or generated local artifacts.

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
   uv run python scripts/upstream_candidate_smoke.py \
     --timeout 300 \
     --report benchmarks/verified_sources/reports/upstream-candidate-smoke-windows-2026-07-30.json
   ```

   This gate fetches pinned public commits into a temporary directory outside
   the repository and checks static sample/template Markdown folders without
   mutating them. It is not default CI, quality certification, upstream release
   certification, or upstream producer certification. The current central
   [Evidence](https://knowledge-bridge-labs.github.io/llmwiki-docs/evidence)
   page records the 12-case actual-pinned Windows compatibility-smoke report;
   keep future release notes concise and link there rather than duplicating the
   full table. The current tracked public-safe report is
   [`benchmarks/verified_sources/reports/upstream-candidate-smoke-windows-2026-07-30.json`](../benchmarks/verified_sources/reports/upstream-candidate-smoke-windows-2026-07-30.json).

   For the full run, record the PASS line for each case, including product URL,
   pinned commit, license evidence, adapter, source files/pages/approved pages,
   projected graph nodes/edges, mutation status, platform, and a public or
   repo-relative report path. Do not record private local paths, credentials,
   raw sensitive logs, private endpoint URLs, or non-public wiki content. Do not
   add cases that require credentials, desktop runtimes, LLM provider calls, or
   heavy application builds. Record whether any failure was a network fetch
   failure or a projection/service failure.

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

   Treat fixture, generated candidate-suite, upstream compatibility-smoke, and
   real-wiki results separately. Fixtures prove the checked-in local examples,
   projection layer, draft filtering, and read-only-source behavior still work.
   The generated candidate suite proves compatible local output shapes only,
   including the DeepAgents `raw/`/`wiki/`/`log.md` workspace-layout variant.
   The optional upstream candidate smoke checks pinned public static snapshots;
   the current central Evidence page records a 12-case actual-pinned Windows
   report. A real exported wiki smoke checks the caller's actual producer
   output, plugin settings, and content conventions; it is not covered by the
   bundled fixtures and should not publish private data.

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
   projection payloads, vector sidecar artifacts, raw vectors, model local
   paths, local environment files, or generated artifacts that are not meant to
   ship. Confirm fixture and smoke inputs do not depend on
   symlinked Markdown/Org files or `graph/graph.json` sidecars; the server
   ignores those by default to keep serving inside the wiki root.
   Network-facing HTTP, MCP, and A2A-style responses must keep local root paths
   redacted. Local CLI output, local registry state, and internal manifests may
   include roots for operator diagnostics; do not copy those local-only values
   into release notes, public reports, issues, screenshots, or docs.
9. Confirm package metadata still lists the repository, issue tracker, homepage,
   Python baseline, runtime dependencies, and optional extras accurately. Redis
   release notes should say that `llmwiki-serve[redis]` is optional, the default
   install remains memory-only/no external service, Redis is a derived
   projection cache only, and Redis may contain sensitive derived wiki content
   including drafts. Vector release notes should say that
   `llmwiki-serve[vector]` is an optional semantic retrieval preview,
   FastEmbed is local-only by default, semantic modes require operator
   configuration, vector sidecars are sensitive derived local state outside the
   served root, lexical remains default, hybrid is lexical+dense RRF with
   bounded optional read-only orientation hints, and vector/hybrid scores are
   mode-specific. They should not imply automatic Redis TTL, vector cache
   cleanup, remote vector storage, automatic hot/index/overview rewriting,
   calibrated abstention, poisoning safety, broad multilingual quality, SOTA
   quality, vector-database-scale performance, or Korean retrieval quality
   unless those behaviors are implemented and benchmarked. If the public
   deployment guide needs broader operator guidance, file or make the follow-up
   in `llmwiki-docs`
   without blocking this repository release checklist.
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
