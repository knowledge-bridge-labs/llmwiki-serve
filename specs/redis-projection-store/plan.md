# Plan: Redis Projection Store

## Approach

Define a `ProjectionStore` boundary with an in-memory implementation that
preserves current behavior and an optional Redis/Valkey implementation selected
only by operator configuration. The service computes or validates the
projection-affecting source signature first, builds a projection key from the
namespace, source id, schema version, and content-derived projection signature,
then attempts a store read. Cache misses or invalid payloads rebuild from disk
and write a new derived record.

The Redis store serializes contract-safe `WikiIndex` JSON without the local root
path. Hydration reattaches the current local root so network manifest redaction
and local CLI manifest behavior remain unchanged.

## Affected Areas

- Source modules: `src/llmwiki_serve/projection_store.py`,
  `src/llmwiki_serve/service.py`, `src/llmwiki_serve/cli.py`,
  `src/llmwiki_serve/api.py`
- Tests: projection-store parity, Redis payload round-trip, fallback,
  corruption, diagnostics, and optional live Redis integration tests
- Docs: README, architecture, release checklist, Redis spec and ADR
- Contracts: additive `/diagnostics/projection-store` OpenAPI schema
- Packaging: additive `redis` optional dependency extra

## Risks

- Risk: Redis is mistaken for the source of truth or freshness authority.
  Mitigation: key only after validated source/projection signatures and repeat
  the derived-cache boundary in architecture, ADR, spec, and release docs.

- Risk: Redis stores sensitive derived wiki data, including drafts.
  Mitigation: document Redis as sensitive storage; keep default memory-only;
  require non-sensitive fixtures for release validation; redact diagnostics.

- Risk: source-id or namespace collisions mix cache entries across roots.
  Mitigation: recommend explicit deployment-specific source ids and namespaces;
  sanitize key parts and include the projection signature in projection keys.

- Risk: Redis outage hides misconfiguration or breaks local-first serving.
  Mitigation: support both `fallback-local` and `fail-fast`; keep
  `fallback-local` as the default for optional-cache behavior.

- Risk: corrupt or stale Redis payload is served.
  Mitigation: validate schema version, namespace, source id, and projection
  signature before hydration; treat invalid payloads as misses.

- Risk: stale derived payloads outlive deleted or reclassified wiki content.
  Mitigation: document Redis as sensitive storage and require an operator
  retention path such as Redis/Valkey eviction or TTL policy, namespace
  rotation, or namespace deletion. Automatic TTL is deferred.

## Rollout

1. Keep memory as the default and validate no-Redis quickstart behavior.
2. Validate missing extra and missing URL errors are actionable.
3. Validate Redis parity with memory for public payloads.
4. Validate key sanitization and root-path omission from payloads.
5. Validate fallback-local, fail-fast, corrupt payload, and diagnostics behavior.
6. Run optional live Redis/Valkey integration only against non-sensitive sample
   roots and isolated namespaces.
7. Update package release notes to call out that `[redis]` is optional and that
   Redis stores sensitive derived projection data.
8. Keep runtime prompt/history/prefix-cache guidance in bridge/runtime docs;
   `llmwiki-serve[redis]` is not an orchestration or session cache.

## LLMWiki Ingestion Candidates

- `specs/redis-projection-store/`
- `docs/decisions/2026-07-22-redis-projection-store-derived-cache-boundary.md`
- `README.md`
- `docs/architecture.md`
- `docs/release.md`
- `docs/research/redis-projection-store-adoption-plan.md`
