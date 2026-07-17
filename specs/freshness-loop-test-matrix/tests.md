# Tests: Freshness Loop Matrix

## Purpose

This matrix is the standard checklist for freshness behavior in long-running
`LlmWikiService` and `llmwiki-serve serve` processes. New unit, integration,
e2e, smoke, and probe tests should cite these `FL-*` IDs when they add coverage.
Loop 0 `PA-010` must run first when the work selects a new library, provider,
cache boundary, or custom freshness mechanism.

## Loop 0 Prior-Art Gate

| ID | Gate | Required evidence |
| --- | --- | --- |
| `PA-010` | Prior-art/library fit | Cite trusted sources such as official docs, upstream repositories, published specs, maintained package metadata, release/issue notes, or local probes; consider watcher providers, producer/build manifests, projection caches, graph/search/traversal libraries when relevant, and contract/e2e test tooling; record adopt/wrap/defer/custom rationale before implementation. |

## Strategy IDs

| ID | Strategy | Status | Freshness authority | Expected posture |
| --- | --- | --- | --- | --- |
| `FS-STRICT` | Strict source scan | Current default | Source path state plus projection-affecting file digests. | Detect source changes before each request. |
| `FS-INTERVAL` | Refresh interval | Current opt-in | Strict source scan only after the configured interval or explicit refresh. | Allow bounded staleness for local performance. |
| `FS-PRODUCER` | Producer manifest marker | Current opt-in | Non-symlink marker file inside the served root, with strict fallback when missing or unsafe. | Trust producer-owned generation updates. |
| `FS-WATCHER` | Watcher dirty flag | Future | Watcher event state is a dirty hint only; strict scan or producer validation remains the authority. | Avoid scans while clean; recover conservatively when uncertain. |
| `FS-REDIS` | Redis projection cache | Future | Validated source signature, producer generation, or dirty-state check. | Reuse derived projections only after freshness is proven elsewhere. |

## Common Loop Fixture

Each loop should start from a small wiki root with:

- `index.md` and `topic.md` approved pages with stable search tokens.
- Optional draft or unapproved page for visibility refresh checks.
- Optional `graph/graph.json` sidecar edge.
- Optional adapter marker/config fixture for adapter refresh checks.
- Stable same-size rewrite helpers for same-size and preserved-mtime cases.

The repeatable loop is:

| ID | Phase | Mutation | Surfaces to sample |
| --- | --- | --- | --- |
| `FL-000` | Baseline build | Start service and read the initial projection. | `manifest`, `read`, `search`, `context`, `graph`, `graph_neighbors`. |
| `FL-010` | No-change reuse | Make a second request without mutating files. | Unit call counts plus HTTP/MCP smoke where relevant. |
| `FL-020` | Markdown rewrite | Rewrite a page, including same-size and preserved-mtime variants. | `read`, `search`, `context`, `manifest.approved_page_count`. |
| `FL-030` | Sidecar graph rewrite | Add, replace, and delete `graph/graph.json`. | `graph`, `graph_neighbors`, context graph hints. |
| `FL-040` | Add/delete source page | Add a linked page, then delete it. | `manifest.page_count`, `read`, `search`, `graph`. |
| `FL-050` | Visibility change | Flip approved content to draft/unapproved and back. | Default filtered reads/search/context/graph plus draft-enabled variants. |
| `FL-060` | Adapter marker/config change | Add or modify projection-affecting adapter markers/config. | `manifest.adapter`, `manifest.implementation`, reads. |
| `FL-070` | Explicit refresh | Call `index(refresh=True)` or equivalent owner-controlled refresh. | Unit-level service APIs first; CLI process commands already rebuild. |
| `FL-080` | Restart | Start a new service instance over the same root. | Initial projection reflects disk state regardless of prior in-memory cache. |
| `FL-090` | Unsafe authority | Missing/unsafe producer marker, unhealthy watcher, or unavailable Redis. | Fallback path preserves correctness and avoids private path exposure. |

## Unit Expectations

| Scenario | `FS-STRICT` | `FS-INTERVAL` | `FS-PRODUCER` | `FS-WATCHER` future | `FS-REDIS` future |
| --- | --- | --- | --- | --- | --- |
| `FL-010` no-change reuse | May stat/check watched paths, then reuse projection. | Reuse projection until interval expires. | Check marker only, then reuse projection. | If clean, reuse projection without a full scan. | Cache hit is allowed only after the authority check succeeds. |
| `FL-020` Markdown rewrite | Detect before next request, including same-size and preserved-mtime rewrites. | Keep stale result until interval expires or explicit refresh runs. | Keep stale result until the marker changes or explicit refresh runs. | Dirty event must bypass interval and trigger authoritative validation. | Old cached projection must not be served after a new validated signature/generation. |
| `FL-030` sidecar graph rewrite | Detect add, replace, stat-preserved replace, and delete. | Same bounded-staleness rule as Markdown. | Same producer-marker rule as Markdown. | Dirty event covers sidecar paths and validation updates graph views. | Cache key includes graph-affecting projection signature/generation. |
| `FL-040` add/delete page | Detect page count, search, read, and graph changes. | Same bounded-staleness rule as Markdown. | Same producer-marker rule as Markdown. | Dirty event covers directory and file create/delete cases. | Cache miss or new key after validation; no cross-generation reuse. |
| `FL-050` visibility change | Refresh approved counts and filtered views. | Same bounded-staleness rule as Markdown. | Same producer-marker rule as Markdown. | Dirty validation refreshes filtered and draft-enabled views. | Cache key or value boundary includes draft/visibility-sensitive projection data. |
| `FL-060` adapter marker/config change | Detect adapter marker/config changes. | Same bounded-staleness rule as Markdown. | Same producer-marker rule if operator has opted into that contract. | Dirty event covers marker/config paths. | Cache key includes adapter/implementation-affecting signature or generation. |
| `FL-070` explicit refresh | Rebuild from disk immediately. | Bypass interval and rebuild from disk immediately. | Rebuild from disk immediately, regardless of marker staleness. | Rebuild from disk immediately and clear dirty state after validation. | Force authority validation and either fetch matching cache or rebuild. |
| `FL-080` restart | New instance reflects disk state on first request. | New instance reflects disk state on first request. | New instance reflects disk state on first request, then follows marker contract. | New instance starts conservative until watcher health is known. | Cache may warm start only after validating source identity and freshness key. |
| `FL-090` unsafe authority | Not applicable. | Interval still validates strictly when due. | Missing, outside-root, symlink, or unreadable marker falls back to strict scan. | Watcher overflow, backend exit, missed-event uncertainty, or permission errors fall back to strict scan. | Redis outage, schema mismatch, source mismatch, or corrupt payload bypasses cache and rebuilds locally. |

## E2E Accumulation

Add end-to-end coverage in this order so failures stay diagnosable:

1. Complete `PA-010` when the work chooses a new freshness library, provider,
   cache boundary, or custom mechanism.
2. Service-level loop helper for `read`, `search`, `context`, `graph`, and
   `graph_neighbors`.
3. HTTP loop for `/manifest`, `/query`, `/read/{page_id}`, `/graph`, and
   `/graph/neighborhood`.
4. MCP-style JSON-RPC loop for `llmwiki_context`, `llmwiki_read`,
   `llmwiki_graph`, and `llmwiki_graph_neighbors`.
5. MCP Streamable HTTP smoke for the same tool names once helper support exists.
6. CLI smoke only for process-level invariants, because one-shot CLI
   `manifest` and `query` commands build fresh service instances.
7. Optional A2A-style compatibility smoke only when the app is created with
   A2A compatibility enabled.

## Current Coverage Map

| Matrix area | Current coverage | Gaps |
| --- | --- | --- |
| `PA-010` Loop 0 prior-art/library fit | `docs/research/2026-07-17-freshness-invalidation-core-libraries.md` captures trusted-source review and local probes for watcher providers, producer/build markers, advanced watcher daemons, and projection-cache boundaries. | Refresh before selecting a provider or custom path when dependency, platform, or source-boundary assumptions change. |
| `FS-STRICT` `FL-020`, `FL-030`, `FL-040`, `FL-050`, `FL-060`, `FL-070`, `FL-080` | `tests/test_freshness_loop_matrix.py` runs the shared service-level fixture across Markdown rewrite, sidecar graph rewrite, add/delete page, visibility change, adapter config change, explicit refresh, and restart behavior. The new shared cells sample `read`, `search`, `context`, `graph`, `graph_neighbors`, and adapter manifest/source-bundle state where relevant. Existing `tests/test_service.py` keeps deeper strict edge cases for preserved stats, adapter marker variants, and missing roots. | Needs shared HTTP/MCP loop coverage. |
| `FS-INTERVAL` `FL-020`, `FL-030`, `FL-040`, `FL-050`, `FL-060`, `FL-070`, `FL-080` | `tests/test_freshness_loop_matrix.py` asserts bounded staleness until interval expiry and explicit refresh bypass behavior for Markdown, sidecar graph, add/delete page, visibility, and adapter config changes. | Needs shared HTTP/MCP loop coverage. |
| `FS-PRODUCER` `FL-020`, `FL-030`, `FL-040`, `FL-050`, `FL-060`, `FL-070`, `FL-080`, `FL-090` | `tests/test_freshness_loop_matrix.py` asserts stale-until-marker-change behavior for Markdown, sidecar graph, add/delete page, visibility, and adapter config changes, plus explicit refresh, restart behavior, and outside-root marker fallback. Existing producer tests cover missing marker and symlink marker fallback. | Add formal generation-manifest cases if this opt-in marker contract gains a schema. |
| `FS-WATCHER` | Research/probe notes only. | Await watcher provider interface. |
| `FS-REDIS` | Architecture/research notes only. | Await cache key and projection storage design. |

## Lightweight Checks

For docs-only matrix updates:

```bash
git diff --check -- specs/ax-knowledge-warehouse-rubrics specs/freshness-loop-test-matrix docs/research/2026-07-17-freshness-invalidation-core-libraries.md
uv run pytest -q tests/test_freshness_loop_matrix.py
uv run pytest -q tests/test_service.py -k "refresh or producer_manifest"
```

When production code changes:

```bash
uv run pytest -q tests/test_service.py tests/test_public_api.py
uv run python scripts/export_openapi.py --check
uv run python scripts/release_smoke.py
```
