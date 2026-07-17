# Tests: AX Knowledge Warehouse Rubrics

## Matrix IDs

| Prefix | Meaning |
| --- | --- |
| `FL-*` | Freshness loop behavior. |
| `GT-*` | Graph traversal and agent utility behavior. |
| `CONN-*` | Chat, bridge, and serve connection setup/interoperability behavior. |
| `GOV-*` | Governance, visibility, and redaction behavior. |
| `OBS-*` | Diagnostics, traceability, and evalability behavior. |
| `PA-*` | Loop 0 prior-art and library-fit gates. |

## Required Coverage

- Prior-art/library-fit gates must cite trusted sources, list candidate tool
  categories considered, and record the adopt/wrap/defer/custom decision before
  implementation tasks begin.
- Freshness tests must exercise read/search/context/graph/neighborhood after
  content and sidecar graph changes.
- Traversal tests must prove context-first and graph-neighborhood paths do not
  require full graph payloads.
- Connection tests must cover direct chat-to-serve source setup, chat-to-bridge
  runtime setup, bridge-managed source discovery, selected-vs-ready gating, and
  actionable diagnostics for unavailable local endpoints.
- Governance tests must use synthetic fixtures and assert that network-facing
  outputs do not reveal local roots, credentials, raw upstream bodies, or draft
  content by default.
- Observability tests must assert stable trace/diagnostic structures instead of
  only checking human-readable messages.

## Current Prior-Art Coverage

| ID | File | Coverage |
| --- | --- | --- |
| `PA-010` | `specs/ax-knowledge-warehouse-rubrics/plan.md`; `docs/research/2026-07-17-freshness-invalidation-core-libraries.md` | Requires Loop 0 before feature/contract/architecture implementation and records freshness candidate review for watchers, producer/build manifests, advanced watcher daemons, projection caches, and related test tooling. |

## Current Governance Coverage

| ID | File | Coverage |
| --- | --- | --- |
| `GOV-010` | `tests/test_governance_loop_matrix.py` | Asserts HTTP, MCP JSON-RPC, and MCP Streamable HTTP public outputs for manifest/source bundle/source refs/context/search/read/graph-neighborhood do not expose synthetic tmp wiki absolute roots. |
| `GOV-020` | `tests/test_governance_loop_matrix.py` | Asserts graph-neighborhood draft nodes stay hidden by default and appear only when both server `allow_drafts` and request `include_drafts` are enabled. |
| `GOV-030` | `tests/test_governance_loop_matrix.py` | Asserts missing roots, unsupported roots, invalid HTTP requests, and MCP error cases return controlled redacted errors without tracebacks. |

## Current Connection Coverage

| ID | File | Coverage |
| --- | --- | --- |
| `CONN-010` | `knowledge-bridge-labs/llmwiki-chat/src/App.test.tsx` | Chat direct-source connection flow: bridge-managed duplicate direct sources can replace an unavailable default direct endpoint, direct source edits restore direct selection for custom runtimes, and active bridge runs survive no-op source selection plus duplicate discovery refreshes. |
| `CONN-020` | `knowledge-bridge-labs/llmwiki-chat/src/App.test.tsx` | Chat bridge-managed source discovery/display: bridge sources discovered from the local bridge render as managed sources without being persisted as manual direct sources; ready-source section selection remains stable; duplicate sources are deduplicated across A2A and MCP bridge runtimes. |
| `CONN-030` | `llmwiki-agent-bridge/test/agent-bridge.test.mjs`; `llmwiki-agent-bridge/docs/message-send-contract.md` | Bridge source registry and readiness coverage: registered sources are persisted and reused when `knowledgeSources` is omitted; source descriptors require `status: ready`, skip `selected: false`, and enforce source URL policy; HTTP/MCP/A2A source calls gather safe source-bundle metadata; selected-source failures produce redacted diagnostic steps. |
| `CONN-040` | `tests/test_connection_loop_matrix.py`; `tests/test_public_api.py` | Serve `/health` exposes redacted service/source identity, projection counts, required protocol endpoints, required capabilities, A2A opt-in state, and CORS mode for bridge and chat connection tests. |

## Validation Commands

Run focused commands for the loop first. Before PR, run each touched repo's
standard check command when feasible.

```bash
uv run pytest -q tests/test_connection_loop_matrix.py
uv run pytest -q tests/test_public_api.py::test_openapi_contract_covers_core_http_response_models
uv run pytest -q tests/test_governance_loop_matrix.py
uv run ruff check tests/test_governance_loop_matrix.py
```
