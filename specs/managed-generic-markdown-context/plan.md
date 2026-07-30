# Plan: Managed Generic Markdown Context

## Approach

Introduce an opt-in managed context layer that sits after the normal read-only
projection and before query/search/context result assembly. The layer is active
only for `generic-markdown`; all native LLMWiki and `llmwiki-markdown` paths
return an empty managed-context decision.

The first implementation should be narrow:

1. Add adapter gating and configuration with disabled-by-default behavior.
2. Add a local per-user sidecar backend with opaque page keys and atomic writes.
3. Derive orientation candidates from the current projection without creating
   source pages.
4. Apply a bounded decayed page-hit prior only inside a narrow lexical tie band.
5. Defer managed Redis and diagnostics until local sidecar behavior, privacy
   checks, and ranking invariants are stable.

The managed layer must consume path-free digests of the current
projection/source signatures before serving results. Signature mismatch
invalidates derived orientation and prior state for that request. The layer
never treats sidecar or Redis state as source evidence.

## Data Model

Managed state is schema-versioned and source-scoped:

```text
schema_version
namespace
source_id
adapter_kind
projection_signature_digest
source_signature_digest
created_at
updated_at
orientation_generation
page_hit_prior[]
```

`page_hit_prior` stores opaque page keys, bounded decayed counters, last-hit
timestamps, and generation metadata. It does not store raw query text, raw page
ids, paths, endpoint labels, request bodies, raw path-bearing signature tuples,
or source snippets.

The local sidecar backend stores one complete record per source/projection
generation and replaces it atomically. The future Redis backend uses a separate
managed-context keyspace so projection-store payloads and managed page-hit
state can be rolled out, evicted, and inspected independently.

## Affected Areas

- Future source modules:
  - service/config resolution for opt-in managed context
  - adapter/projector boundary for `generic-markdown` gating
  - search/context ranking composition
  - local sidecar state backend
  - optional future managed Redis backend
- Future tests:
  - source mutation guards
  - adapter no-op coverage
  - privacy serialization checks
  - signature invalidation
  - ranking non-inversion
  - local concurrency and atomic write checks
  - future Redis keyspace and atomic update checks
- Docs/contracts:
  - this spec package first
  - README/architecture only after implementation is ready
  - no OpenAPI changes in the initial slice

## Configuration

Configuration should be explicit and startup-scoped.

Suggested controls, subject to implementation review:

| Setting | Default | Purpose |
| --- | --- | --- |
| managed context mode | `off` | Enables or disables the layer. |
| managed context backend | `local-sidecar` | Selects local sidecar first; Redis is deferred. |
| managed context namespace | derived safe default | Separates per-user/deployment state. |
| max managed boost | conservative cap | Bounds page-hit influence. |
| lexical tie band | conservative cap | Prevents relevance inversion. |
| page-hit half-life | conservative duration | Decays old local behavior. |
| sidecar state directory | per-user local state | Keeps writes outside the source root. |

Invalid values should fail early with operator-readable errors. Disabling the
mode ignores all existing sidecar/Redis state.

## Ranking Composition

The ranking pipeline keeps lexical relevance primary:

1. Compute existing lexical scores.
2. Partition or compare results by a configured lexical tie band.
3. Compute a decayed page-hit prior for candidates whose opaque page keys match
   the current projection generation.
4. Apply the prior only within the tie band and below `max managed boost`.
5. Preserve original lexical order for candidates outside the tie band.

Tests should prove that a lower lexical match cannot cross a materially better
lexical match because of the managed prior.

## Concurrency And Atomicity

Local sidecar writes:

- Build the next complete record in memory.
- Write to a temporary file in the sidecar directory.
- Flush and fsync when supported.
- Atomically replace the previous record.
- Ignore unreadable, partial, schema-mismatched, or signature-mismatched
  records.

Concurrent local processes should use an advisory lock where available or an
optimistic generation check. If two processes race, a lost counter increment is
acceptable; source projection corruption, partial record reuse, and path/query
leakage are not acceptable.

Future Redis writes:

- Use a keyspace distinct from projection-store keys.
- Use atomic Redis operations or compare-and-set semantics for counter updates.
- Treat unavailable, corrupt, stale, or mismatched Redis state as no managed
  prior unless the operator explicitly configures a stricter failure policy.

## Risks

- Risk: managed context is mistaken for a source writer.
  Mitigation: external sidecar only, source mutation tests, and no public
  synthetic source pages.

- Risk: raw user queries or private paths are persisted.
  Mitigation: state schema excludes those fields, serialization tests inspect
  sidecar and future Redis records, and diagnostics stay deferred.

- Risk: page-hit history overpowers lexical relevance.
  Mitigation: tie-band-only application, small boost cap, and ranking
  non-inversion tests.

- Risk: native LLMWiki behavior regresses.
  Mitigation: adapter gating tests for `llmwiki-markdown` and authored
  orientation filenames.

- Risk: concurrent serve processes corrupt managed state.
  Mitigation: atomic replacement, schema validation, advisory lock or
  optimistic generation, and corrupt-record fallback.

- Risk: managed Redis is conflated with projection-store Redis.
  Mitigation: separate keyspace, separate schema version, separate rollout
  flag, and independent tests.

## Rollout

1. Land this spec package.
2. Implement disabled-by-default config resolution and adapter no-op behavior.
3. Add local sidecar state with privacy and atomicity tests, but keep ranking
   effect disabled until serialization invariants pass.
4. Add derived orientation for `generic-markdown` using current projection
   facts only.
5. Add bounded page-hit prior within lexical tie band.
6. Run targeted service tests plus source mutation and doc hygiene checks.
7. Document the opt-in operator path in README/architecture only after behavior
   is implemented and verified.
8. Add managed Redis only after local sidecar behavior is stable.
9. Consider redacted diagnostics only after an operator workflow requires them.

## Rollback

Rollback is configuration-first:

- Set managed context mode to `off`.
- Ignore all local sidecar and managed Redis records.
- Keep source tree unchanged because no source files were created or modified.
- Remove managed sidecar state through an explicit local cleanup command only
  if operators want to reclaim space; cleanup is not required for correctness.
- If Redis support exists, rotate or delete the managed-context namespace
  independently from projection-store namespaces.

## LLMWiki Ingestion Candidates

- `specs/managed-generic-markdown-context/`
- `docs/architecture.md`
- `specs/korean-numeric-search-relevance/`
- `specs/redis-projection-store/`
- `specs/freshness-loop-test-matrix/tests.md`
