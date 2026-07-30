# Tests: Managed Generic Markdown Context

## Acceptance Criteria

- `REQ-MGMC-001`: With managed context disabled, public payloads and OpenAPI
  output match current behavior.
- `REQ-MGMC-002` and `REQ-MGMC-003`: Enabling managed context has no effect for
  adapters other than `generic-markdown`, including `llmwiki-markdown` and
  native authored orientation pages.
- `REQ-MGMC-004`: Managed context requests do not create, modify, delete, or
  rename files under the served source root.
- `REQ-MGMC-005` and `REQ-MGMC-006`: Local sidecar and future Redis records omit
  raw query text, request bodies, raw page ids, paths, endpoint labels,
  credentials, tokens, API keys, and source snippets.
- `REQ-MGMC-007`: Derived orientation selects approved existing pages from the
  current projection without exposing a synthetic source page through read,
  manifest, graph, or source-bundle responses.
- `REQ-MGMC-008`: Page-hit counters are bounded, decay over time, and persist
  only opaque page keys plus metadata.
- `REQ-MGMC-009` and `REQ-MGMC-010`: Managed prior cannot reorder a materially
  better lexical result below a weaker lexical result.
- `REQ-MGMC-011` and `REQ-MGMC-012`: Projection/source signature changes,
  explicit refresh, sidecar graph changes, adapter changes, and visibility
  changes invalidate managed orientation and page-hit prior state.
- `REQ-MGMC-013` and `REQ-MGMC-014`: Local sidecar writes are atomic, tolerate
  concurrent writers, and ignore partial or corrupt records.
- `REQ-MGMC-015` and `REQ-MGMC-016`: Future Redis support uses a separate
  managed-context keyspace, atomic counter updates, and safe fallback on
  unavailable or mismatched state.
- `REQ-MGMC-017`: Disabling managed context ignores existing sidecar/Redis state
  and restores baseline ranking without source cleanup.
- `REQ-MGMC-018`: No managed-context diagnostics are exposed in the first slice.
- `REQ-MGMC-019`: Non-empty unrelated or negative context queries receive no
  managed orientation items.
- `REQ-MGMC-020`: Query-scoped managed orientation uses compact default
  snippets and a compact candidate limit without changing response schemas.
- `REQ-MGMC-021`: Managed orientation abstention leaves ordinary evidence
  search intact and does not update managed page-hit state for that context
  request.

## Unit Tests

- Config defaults to disabled and does not instantiate a sidecar backend.
- Invalid managed context config fails early with an operator-readable error.
- Adapter gate returns empty managed decisions for `llmwiki-markdown`,
  Obsidian-style, Foam, Quartz, and other non-`generic-markdown` adapters.
- Authored `hot.md`, `index.md`, `overview.md`, and `quickstart.md` pages are
  treated as source pages, not rewritten managed artifacts.
- Sidecar state directory resolution stays outside the served source root.
- Sidecar serialization uses opaque page keys and omits raw queries, raw page
  ids, path strings, endpoint labels, credentials, request bodies, and source
  snippets.
- Sidecar hydration rejects schema-version, namespace, source-id, adapter,
  projection-signature, and source-signature mismatches.
- Derived orientation candidate selection uses only approved current projection
  pages.
- Query-scoped derived orientation filters to current evidence-related pages.
- Unrelated or negative non-empty context queries abstain from managed
  orientation.
- Managed orientation abstention does not create or update local sidecar hit
  records for that context request.
- Managed orientation defaults to compact snippets when callers do not request a
  snippet size.
- Draft or unapproved pages do not contribute to default orientation candidates
  or page-hit increments unless draft access is explicitly enabled elsewhere.
- Page-hit decay clamps counters to the configured maximum.
- Ranking composition applies managed boosts only within the lexical tie band.
- Ranking composition preserves lexical ordering outside the tie band.
- Explicit refresh rebuilds the managed in-memory view before the next ranking
  decision.
- Sidecar graph add, replace, and delete cases invalidate managed orientation
  when projection/source signatures change.
- Atomic write tests ignore partial temporary files and recover from corrupt
  previous records.
- Concurrent writer tests prove final state is parseable and bounded even if one
  page-hit increment is lost.

## Integration / Contract Tests

- Run baseline query/search/read/graph/MCP payload checks with managed context
  disabled and compare to existing snapshots or structural invariants.
- Enable managed context on a generic Markdown fixture and confirm query/context
  can include derived orientation candidates without response schema changes.
- Enable managed context on a generic Markdown fixture and confirm unrelated
  context queries return ordinary evidence search results if lexical search
  finds them, but no managed orientation.
- Enable managed context on a native LLMWiki fixture and confirm query/context
  output is unchanged except for normal nondeterministic timing fields.
- Snapshot the source tree before and after managed context requests and assert
  no source file content or source file list changed.
- Confirm `manifest`, `source-bundle`, `read`, and `graph` do not expose a
  synthetic managed orientation page.
- Confirm OpenAPI remains unchanged for the initial implementation slice.
- Confirm serve I/O logging behavior remains governed by the existing logging
  boundary and is not used as managed context storage.

## Freshness Matrix Coverage

Map managed context tests onto the shared freshness matrix:

| Matrix ID | Managed context expectation |
| --- | --- |
| `FL-010` | No-change requests may reuse managed orientation and prior state for the same projection/source signatures. |
| `FL-020` | Markdown rewrites invalidate managed state when the validated projection/source signature changes. |
| `FL-030` | `graph/graph.json` add, replace, and delete cases invalidate graph-derived orientation candidates. |
| `FL-040` | Added or deleted pages get new opaque page-key coverage only after projection rebuild. |
| `FL-050` | Visibility changes remove draft/private pages from default managed orientation and hit updates. |
| `FL-060` | Adapter marker/config changes can disable managed context when the adapter is no longer `generic-markdown`. |
| `FL-070` | Explicit refresh bypasses stale managed in-memory state. |
| `FL-080` | Restart hydrates only matching sidecar/Redis state after validating projection/source identity. |
| `FL-090` | Corrupt sidecar, stale local locks, unavailable sidecar/Redis state, unsafe state directory, or mismatched signatures fall back to no managed prior. |

## Future Redis Tests

- Managed Redis keys use a managed-context prefix/keyspace distinct from Redis
  projection-store keys.
- Managed Redis keys include schema version, namespace, source id, path-free
  projection signature digest, and opaque page keys only.
- Redis records omit raw queries, paths, endpoint labels, request bodies,
  credentials, raw keys from other namespaces, and source snippets.
- Atomic Redis counter updates preserve decay and cap behavior under concurrent
  requests.
- Redis outage falls back according to configured policy without changing source
  projection correctness.
- Projection-store Redis and managed-context Redis can be enabled together
  without key collision or payload schema confusion.

## Manual / Release Checks

Docs-only draft checks:

```bash
git diff --check -- specs/managed-generic-markdown-context
git status --short
```

Implementation release checks:

```bash
uv run pytest -q tests/test_service.py -k "managed_context or generic_markdown or search"
uv run pytest -q tests/test_freshness_loop_matrix.py
uv run pytest -q tests/test_public_api.py
uv run python scripts/export_openapi.py --check
uv run python scripts/release_smoke.py
```

## Skipped Or Deferred

- Public diagnostics for managed context are deferred.
- Managed Redis backend is deferred until local sidecar behavior passes privacy,
  freshness, and ranking tests.
- RedisVL, embeddings, semantic search, and model-generated summaries are
  deferred to separate specs.
- Public quality benchmark claims are deferred until a judged public-safe corpus
  validates the ranking changes.
