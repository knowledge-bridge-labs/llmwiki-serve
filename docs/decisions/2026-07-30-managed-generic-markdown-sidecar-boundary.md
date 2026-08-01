# ADR: Managed Generic Markdown Sidecar Boundary

## Status

Accepted.

## Context

`llmwiki-serve` already serves Markdown-compatible source folders as immutable
input. Native LLMWiki-style folders can provide their own hub pages such as
`hot.md`, `index.md`, `overview.md`, and `quickstart.md`; the
`llmwiki-markdown` adapter must continue to read those producer-owned pages
without rewriting or supplementing them.

Plain `generic-markdown` folders often do not have author-curated orientation
pages. Agents can still benefit from a small amount of derived orientation and a
weak page-hit prior, but putting that state in the source tree would violate the
read-only guarantee and would make unrelated Markdown folders look like managed
LLMWiki outputs.

The repo already has a Redis projection-store boundary for derived projections.
Managed generic context needs its own state boundary because page-hit priors are
per-user usage signals, not source facts and not projection payloads.

## Decision

Add managed generic Markdown context only for the `generic-markdown` adapter.
The existing `llmwiki-markdown`, Obsidian, Logseq, Foam, Dendron, and Quartz
adapter behavior remains unchanged unless a future ADR explicitly opts them in.
Existing source-owned `hot.md`, `index.md`, `overview.md`, and
`quickstart.md` behavior remains a no-op for this feature.

Managed state lives outside the served source tree in per-user sidecar storage.
The source folder remains fully read-only: no generated Markdown, no annotations,
no hidden marker files, and no migrated hub pages are written under the served
root. Sidecar records store only derived, bounded metadata such as opaque page
keys, path-free projection/source signature digests, decayed hit counters,
orientation generation metadata, timestamps, and schema/config versions.

Raw query strings, raw request paths, raw local filesystem paths, private
endpoints, credentials, and raw Redis URLs must not be stored in the sidecar or
included in committed documentation. Page-hit updates can be matched to stable
page identifiers in memory while ranking the current projection, but persisted
managed state uses opaque page keys rather than literal page ids, local paths,
or request payloads.

If Redis is used for this feature, it uses a Redis keyspace separate from the
existing projection-store keyspace. Projection Redis records remain derived
`WikiIndex` caches; managed generic context Redis records hold only the bounded
per-user sidecar metadata. Keys include schema version, deployment namespace,
feature namespace, source id, and path-free projection/source signature digests.
Raw local paths and raw path-bearing signature tuples are never Redis key parts.

The page-hit prior is deliberately weak. Lexical relevance remains the primary
ranking signal, and the managed prior can only act as a small bounded boost or
tie-break among pages with comparable lexical scores. It must not move a weak or
non-matching page above a materially stronger lexical match.

Managed orientation is derived from the current generic Markdown projection and
the bounded decayed prior. The sidecar is invalidated when projection-affecting
path-free source signature digest, adapter identity, source id, projection
schema version, or managed-sidecar schema/config versions no longer match.
Cache misses and stale sidecar records rebuild derived orientation from the
current read-only projection.
For non-empty context queries, managed orientation is query-relevance gated: if
ordinary evidence search does not produce sufficiently scored current pages that
overlap significant query tokens, managed orientation abstains instead of adding
generic overview pages to an unrelated query. This abstention does not suppress
ordinary evidence search results.

Sidecar writes are atomic from the reader's perspective. Concurrent requests can
race to update bounded counters, but readers must see either the previous valid
record or the next valid record, not partial JSON or a mixed projection
generation. File-backed sidecars use temporary-write-and-replace with best-effort
locking or compare-and-swap metadata where available; Redis-backed sidecars use
single-key atomic updates or version-checked transactions.
Stale local locks or sidecar I/O failures skip managed reads/writes for that
request rather than failing public search, read, or context responses.

Configuration is opt-in for the initial implementation and can be rolled back by
disabling the managed generic context option, clearing or ignoring the sidecar
namespace, or rotating the managed sidecar namespace. Rollback does not require
source tree cleanup because the source tree is never written.

There is no public HTTP, MCP, OpenAPI, or CLI response contract impact by
default. Public payloads continue to expose the same context/search/read shapes.
Managed generic diagnostics are deferred to a future extension and must be
specified separately before any public diagnostic fields or endpoints are added.

## Consequences

- Generic Markdown folders can get lightweight derived orientation without
  adopting a producer-owned LLMWiki folder contract.
- Native LLMWiki and app-specific adapters keep their current source-owned hub
  semantics.
- Operators gain an external per-user state file or Redis namespace that must be
  treated as local derived state, but the served source tree stays clean.
- Ranking behavior can improve repeated-use ergonomics only within a bounded
  relevance envelope; lexical matching remains explainable and testable.
- Invalidation depends on both source projection identity and sidecar schema
  identity, so stale usage state cannot silently apply to a different source
  generation.

## Follow-Ups

- Specify exact configuration names and defaults in
  `specs/managed-generic-markdown-context/` before implementation.
- Add focused tests that prove source-tree immutability, adapter no-op behavior,
  weak boost bounds, invalidation, and atomic sidecar writes.
- Keep public diagnostics out of the first implementation slice; define a
  separate diagnostics ADR/spec if operators need visibility into managed
  generic context state.
- Document the feature in README and architecture only after the implementation
  contract is accepted.

## References

- Spec: `specs/managed-generic-markdown-context/`
- Architecture: `docs/architecture.md#read-only-guarantee`
- Redis projection-store ADR:
  `docs/decisions/2026-07-22-redis-projection-store-derived-cache-boundary.md`
- Producer manifest ADR:
  `docs/decisions/2026-07-17-producer-manifest-freshness-boundary.md`
- OpenWiki, MIT, pinned `0.2.4` commit
  [`9c253af17f264ac2589ab6781e79e9bb5b5d1238`](https://github.com/langchain-ai/openwiki/tree/9c253af17f264ac2589ab6781e79e9bb5b5d1238);
  its reserved-doc update pattern is prior art only and is not copied because
  this design does not mutate source docs.
- Microsoft LLMWiki, MIT, pinned main commit
  [`74a8a5bf0011b1092f135e5cbc51bbb44c1e07e7`](https://github.com/microsoft/llmwiki/tree/74a8a5bf0011b1092f135e5cbc51bbb44c1e07e7);
  index, query, and backlink behavior informs retrieval concepts only.
- Pratiyush `llm-wiki`, license unknown/unverified for this pinned note, pinned
  `v1.3.82` commit
  [`834998747ec5368f4a4f3ffa450995048ac7c4af`](https://github.com/Pratiyush/llm-wiki/tree/834998747ec5368f4a4f3ffa450995048ac7c4af);
  `hot.md`, per-project cache, index, overview, and log behavior inform prior
  art only.
- Foam, MIT, pinned main commit
  [`6635f557ae4e214f872d75f728880c0970d9f6a8`](https://github.com/foambubble/foam/tree/6635f557ae4e214f872d75f728880c0970d9f6a8);
  graph links, backlinks, placeholders, and bounded traversal inform graph
  context only.
- Quartz, MIT, pinned `v4` commit
  [`d25a6eabf96751ffca56f8a8139272def7a65041`](https://github.com/jackyzha0/quartz/tree/d25a6eabf96751ffca56f8a8139272def7a65041);
  generated `contentIndex.json` is sidecar prior art only.
- Continue, Apache-2.0, pinned main commit
  [`5522c6f44ca0ac3528b37244818fbfa39b5af470`](https://github.com/continuedev/continue/tree/5522c6f44ca0ac3528b37244818fbfa39b5af470);
  multi-signal retrieval and context-budget behavior inform ranking constraints
  only.
