# Spec: Managed Generic Markdown Context

## Status

Draft.

## Problem

Generic Markdown folders often lack the author-managed orientation pages that
native LLMWiki folders use for agent context. A service can derive a small
orientation prior and learn bounded page-hit preferences over time, but doing so
must not mutate the source tree, must not override existing LLMWiki semantics,
and must not persist raw user queries, raw paths, private endpoint labels, or
other operator-specific details.

The current service posture is local-first and read-only. Any managed context
state has to live outside the served source folder and remain a small retrieval
hint, not a source of truth, semantic index, or public contract change.

## Goals

- Keep the served source tree completely read-only.
- Apply managed context only to the `generic-markdown` adapter path.
- Make existing `hot.md`, `index.md`, `overview.md`, `quickstart.md`, and
  `llmwiki-markdown` behavior a no-op for this feature.
- Store managed state in an external per-user sidecar and, when Redis/Valkey is
  enabled later, in a Redis keyspace separate from projection-store keys.
- Never persist raw query text, raw local paths, source-root paths, private
  endpoint labels, credentials, or raw request bodies in managed context state.
- Derive orientation candidates from the current projection and combine them
  with a bounded, decayed page-hit prior.
- Emit managed orientation for non-empty queries only when the current evidence
  search shows sufficient query relevance; unrelated or negative context
  queries abstain from managed orientation.
- Keep managed orientation payloads compact through a query-scoped candidate
  limit and compact default snippets.
- Preserve lexical relevance as the primary ranking signal. Managed context may
  act only as a small tie-break or boost inside a narrow lexical tie band.
- Invalidate derived orientation and page-hit priors by projection/source
  signature.
- Define concurrency and atomicity expectations for local sidecar and future
  Redis writes.
- Include operator configuration, rollout, and rollback behavior.
- Keep public response schemas unchanged by default. Diagnostics are a future
  extension, not part of the initial contract.

## Non-Goals

- Do not create, edit, delete, normalize, annotate, or reserve files inside the
  served source tree.
- Do not synthesize new Markdown pages that are visible through `read`, `graph`,
  source bundle, or manifest as source pages.
- Do not change native LLMWiki or `llmwiki-markdown` orientation behavior.
- Do not add embeddings, RedisVL, vector search, model calls, answer synthesis,
  or multi-source orchestration.
- Do not make page-hit history override a better lexical match.
- Do not expose managed context internals through OpenAPI, HTTP, MCP, or
  Streamable HTTP in the first implementation slice.
- Do not copy implementation code from upstream projects listed in the
  references.

## Requirements

- `REQ-MGMC-001`: The feature is disabled by default. With no opt-in
  configuration, manifest, query, search, read, graph, MCP, Streamable HTTP, and
  OpenAPI behavior remains unchanged.
- `REQ-MGMC-002`: When enabled, the feature is active only when the selected
  adapter/implementation is `generic-markdown`.
- `REQ-MGMC-003`: For `llmwiki-markdown`, native LLMWiki folders, and source
  folders that already use `hot.md`, `index.md`, `overview.md`, or
  `quickstart.md` as authored orientation pages, managed generic context is a
  no-op. Those pages are not rewritten, regenerated, reclassified, or replaced.
- `REQ-MGMC-004`: The source tree is immutable input. Managed context writes
  only to external per-user state or to a dedicated managed-context Redis
  keyspace; it never writes below the served source root.
- `REQ-MGMC-005`: Managed context records must not store raw query text, raw
  request bodies, raw local paths, source-root-relative paths, private endpoint
  labels, Redis URLs, credentials, tokens, or API keys.
- `REQ-MGMC-006`: Page identity in managed state uses opaque derived page keys.
  A page key is computed from current projection identity, source identity, a
  per-user salt or namespace secret, and the service page id. Raw page ids and
  paths are not persisted in sidecar or managed Redis records.
- `REQ-MGMC-007`: Derived orientation is computed from approved pages in the
  current projection using non-mutating signals such as title, role, headings,
  graph centrality, inbound links, source references, and shallow folder
  position. It may select existing pages for context, but it must not create a
  synthetic source page.
- `REQ-MGMC-008`: Page-hit priors are bounded and decay over time. A serving
  event may increment counters for pages that were actually returned or read,
  but only the opaque page key, bounded counter state, timestamps, schema
  version, source id, and path-free projection/source signature digests are
  persisted.
- `REQ-MGMC-009`: The managed prior cannot invert lexical relevance. A result
  with a materially lower lexical score must not outrank a result outside the
  configured lexical tie band because of managed context.
- `REQ-MGMC-010`: Any managed boost is capped by configuration and defaults to
  a conservative value suitable only for tie-breaking or slight reordering among
  near-equal lexical candidates.
- `REQ-MGMC-011`: Managed orientation and page-hit state is scoped by schema
  version, namespace, source id, adapter kind, projection signature digest, and
  source signature digest. Raw path-bearing signature tuples are never
  persisted. Signature mismatch is a cache miss or ignored history, not
  evidence for the current projection.
- `REQ-MGMC-012`: Explicit service refresh, source signature changes,
  projection signature changes, adapter changes, draft/visibility changes, and
  sidecar graph changes invalidate the in-memory managed view before the next
  ranking decision.
- `REQ-MGMC-013`: Local sidecar writes are atomic at the record-file boundary:
  write a complete replacement to a temporary file in the sidecar directory,
  fsync when supported, then replace the previous record. Partial records are
  ignored.
- `REQ-MGMC-014`: Concurrent local writers use an advisory lock or optimistic
  generation check. Lost-update behavior must be bounded to page-hit counters;
  it must not corrupt the sidecar or affect source projection correctness.
  Stale locks or local sidecar I/O failures fail open by skipping managed
  reads/writes for that request.
- `REQ-MGMC-015`: Future Redis/Valkey support uses a managed-context keyspace
  distinct from projection-store keys. Keys include schema version, namespace,
  source id, path-free projection signature digest, and opaque page keys; they
  do not include raw paths, raw queries, or private endpoint labels.
- `REQ-MGMC-016`: Redis updates use atomic server-side operations or compare
  and set semantics for decayed counters and generation metadata. Corrupt,
  stale, mismatched, or unavailable Redis state falls back to no managed prior
  or local sidecar behavior according to operator configuration.
- `REQ-MGMC-017`: Rollback is immediate: disabling the feature must ignore all
  managed context state and restore current lexical/orientation behavior without
  source cleanup.
- `REQ-MGMC-018`: Diagnostics for managed context are deferred. If added later,
  diagnostics may expose only redacted backend kind, enabled status, counts,
  age, path-free projection/source signature digest prefixes, and availability;
  they must not expose raw queries, raw paths, raw keys, payloads, or private
  endpoints.
- `REQ-MGMC-019`: For non-empty context queries, managed orientation is emitted
  only when ordinary evidence search returns at least one sufficiently scored
  result whose current projection page overlaps significant query tokens.
  Otherwise managed orientation abstains by returning no managed orientation
  items. Empty-query context still acts as an overview request.
- `REQ-MGMC-020`: Query-scoped managed orientation uses a compact candidate
  limit and compact default snippets when the caller did not request a snippet
  size. This changes only opt-in managed generic orientation payload content,
  not public response schemas.
- `REQ-MGMC-021`: Managed orientation abstention must not suppress ordinary
  evidence search results. For context requests where managed orientation
  abstains as unrelated, managed page-hit prior state is not updated from those
  evidence results.

## User / Agent Flow

1. The operator serves a plain generic Markdown folder with managed context
   explicitly enabled.
2. The service builds the normal read-only projection from Markdown files and
   optional adapter-loaded sidecar graph facts.
3. If the adapter is not `generic-markdown`, the managed context layer returns
   no orientation candidates and no page-hit boost.
4. Query/search/context ranking starts with existing lexical relevance.
5. For `generic-markdown`, the service computes a projection-scoped
   orientation candidate list from approved pages.
6. For non-empty context queries, the managed layer filters orientation to
   query-related pages and abstains when current evidence search does not show
   sufficient query relevance.
7. The managed layer may reorder only near-tied lexical candidates by applying
   the bounded decayed page-hit prior.
8. After a successful serving event, the managed layer records bounded hit
   updates for returned/read pages using opaque page keys only, except for
   context requests where managed orientation abstained as unrelated.

## Compatibility

- CLI: additive opt-in configuration only.
- Environment: additive opt-in configuration only.
- HTTP/MCP/Streamable HTTP: no new fields, routes, tools, or schemas in the
  first implementation slice.
- OpenAPI: unchanged for the first implementation slice.
- Ranking: unchanged when disabled; when enabled, lexical relevance remains the
  primary signal and managed context acts only within a configured tie band.
- Source adapters: no input format change; `generic-markdown` is the only active
  path.
- Existing clients: no response shape change.

## Data Safety

Managed context state is local, derived, and sensitive. It may reveal that a
page has been useful recently, so it must be stored outside the source tree and
must avoid raw identifiers. Sidecar and managed Redis records may contain only
opaque page keys, bounded counters, timestamps, schema/config metadata, source
identity, and path-free projection/source signature digests.

This spec does not change the separate serve I/O logging boundary. Managed
context itself must never persist raw query text, raw request bodies, local
paths, source-root-relative paths, private endpoint labels, credentials, tokens,
or raw Redis URLs.

## Prior-Art Summary

The following sources are design references only. No code is copied.

| Project | Pinned source link | Pinned ref / license from prompt | Design takeaway |
| --- | --- | --- | --- |
| OpenWiki | https://github.com/langchain-ai/openwiki/tree/9c253af17f264ac2589ab6781e79e9bb5b5d1238 | `0.2.4`, commit `9c253af17f264ac2589ab6781e79e9bb5b5d1238`, MIT | `index-sync.ts` and `okf-middleware.ts` show a source-mutating reserved-doc pattern. This spec rejects that boundary and keeps managed state external. |
| Microsoft LLMWiki | https://github.com/microsoft/llmwiki/tree/74a8a5bf0011b1092f135e5cbc51bbb44c1e07e7 | main commit `74a8a5bf0011b1092f135e5cbc51bbb44c1e07e7`, MIT | Index, query, and backlink behavior inform derived orientation and graph-aware context selection without source mutation. |
| Pratiyush llm-wiki | https://github.com/Pratiyush/llm-wiki/tree/834998747ec5368f4a4f3ffa450995048ac7c4af | `v1.3.82`, commit `834998747ec5368f4a4f3ffa450995048ac7c4af`, license unknown/unverified from prompt | `hot.md`, per-project cache, index, overview, and log patterns inform the no-op boundary for native authored orientation. |
| Foam | https://github.com/foambubble/foam/tree/6635f557ae4e214f872d75f728880c0970d9f6a8 | main commit `6635f557ae4e214f872d75f728880c0970d9f6a8`, MIT | Links, backlinks, placeholders, and bounded traversal inform non-mutating graph-derived orientation candidates. |
| Quartz | https://github.com/jackyzha0/quartz/tree/d25a6eabf96751ffca56f8a8139272def7a65041 | `v4`, commit `d25a6eabf96751ffca56f8a8139272def7a65041`, MIT | Generated `contentIndex.json` illustrates a sidecar-style generated index boundary, kept separate from authored Markdown. |
| Continue | https://github.com/continuedev/continue/tree/5522c6f44ca0ac3528b37244818fbfa39b5af470 | main commit `5522c6f44ca0ac3528b37244818fbfa39b5af470`, Apache-2.0 | Multi-signal retrieval and context budget ideas inform bounded signal composition, with lexical relevance kept primary. |

## Open Questions

- Should the first implementation expose only a local sidecar backend and defer
  managed Redis until the local privacy/ranking tests are stable?
- Should hit events count only explicit reads, context inclusions, or both?
- What default half-life and tie band produce useful behavior without
  overfitting local usage?
- Should a future diagnostic endpoint be local-only, or is redacted network
  diagnostics acceptable for operator dashboards?

## References

- Architecture: `docs/architecture.md`
- Redis projection boundary:
  `docs/decisions/2026-07-22-redis-projection-store-derived-cache-boundary.md`
- Producer freshness boundary:
  `docs/decisions/2026-07-17-producer-manifest-freshness-boundary.md`
- Search relevance spec: `specs/korean-numeric-search-relevance/`
- Freshness matrix: `specs/freshness-loop-test-matrix/tests.md`
