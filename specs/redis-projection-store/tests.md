# Tests: Redis Projection Store

## Acceptance Criteria

- `REQ-REDIS-001`: Default service and CLI paths run without the Redis optional
  dependency or a Redis server.
- `REQ-REDIS-002` through `REQ-REDIS-005`: Redis selection honors CLI/env
  precedence and produces actionable errors for missing URL, invalid
  `LLMWIKI_PROJECTION_STORE`, and missing optional extra.
- `REQ-REDIS-006` and `REQ-REDIS-007`: Redis keys are namespaced, include schema
  version/source id/projection signature, sanitize unsafe key parts, and avoid
  local roots.
- `REQ-REDIS-008` and `REQ-REDIS-009`: Redis cache hits occur only after the
  configured freshness authority validates the current source generation.
- `REQ-REDIS-010` and `REQ-REDIS-011`: Serialized payloads omit local root paths
  while preserving the derived `WikiIndex` content needed for equivalent public
  responses.
- `REQ-REDIS-012`: `fallback-local` keeps serving from memory after client
  failure; `fail-fast` raises a controlled error.
- `REQ-REDIS-013`: corrupt, schema-mismatched, namespace-mismatched,
  source-id-mismatched, or signature-mismatched payloads are treated as misses.
- `REQ-REDIS-014`: diagnostics expose backend status without Redis URL,
  credentials, or local root paths.
- `REQ-REDIS-015`: Redis-backed and memory-backed services return equivalent
  manifest, query, search, read, graph, MCP, and MCP Streamable HTTP payloads
  for the same source generation.
- `REQ-REDIS-016`: README, architecture, release, and spec docs distinguish
  Redis projection caching from runtime prompt/history/prefix caches and
  document retention options for stale derived records.

## Unit / Integration Tests

- `tests/test_service.py::test_projection_store_hit_skips_wiki_builder`
- `tests/test_service.py::test_projection_store_miss_writes_and_fresh_service_can_hydrate_hit`
- `tests/test_service.py::test_projection_record_payload_excludes_absolute_root_and_hydrates_local_root`
- `tests/test_service.py::test_redis_projection_store_uses_namespaced_keys_and_round_trips_without_paths`
- `tests/test_service.py::test_redis_projection_store_sanitizes_namespace_and_source_key_parts`
- `tests/test_service.py::test_redis_projection_store_treats_corrupt_payload_as_cache_miss`
- `tests/test_service.py::test_redis_projection_store_corrupt_payload_is_rebuilt_by_service`
- `tests/test_service.py::test_redis_projection_store_invalidates_source_projection_keys`
- `tests/test_service.py::test_redis_projection_store_falls_back_to_local_memory_after_client_failure`
- `tests/test_service.py::test_redis_projection_store_fail_fast_raises_redacted_error`
- `tests/test_service.py::test_redis_projection_store_http_fallback_matches_default_payloads`
- `tests/test_service.py::test_redis_projection_store_missing_extra_error_is_actionable`
- `tests/test_service.py::test_projection_store_diagnostics_redacts_redis_url_and_local_root`
- `tests/test_service.py::test_projection_store_uses_new_cache_key_after_source_change`
- `tests/test_service.py::test_cli_rejects_redis_projection_store_without_url`
- `tests/test_service.py::test_cli_uses_projection_store_env_namespace_and_source_id`
- `tests/test_service.py::test_redis_projection_store_matches_default_http_and_mcp_payloads`
- `tests/test_public_api.py` coverage for `/diagnostics/projection-store`
- `tests/test_redis_projection_store_integration.py`, gated by `LLMWIKI_REDIS_URL`

## Manual / Release Checks

- Run the no-Redis release smoke from a default install to prove Redis is not
  required.
- Run a live Redis/Valkey smoke with `--redis-failure-policy fail-fast`,
  `--cache-namespace`, and `--source-id` against `examples/sample-wiki`.
- Query `/diagnostics/projection-store` and confirm no URL, credential, or local
  path is present.
- Inspect Redis keys and payloads only on non-sensitive fixtures; do not paste
  raw values into public release artifacts.
- Confirm the operator retention path is documented. The current implementation
  does not set an automatic TTL, so release docs should mention Redis/Valkey
  eviction or TTL policy, namespace rotation, or namespace deletion for stale
  projection records.

## Redis Release-Candidate Evidence

On 2026-07-22, the Redis projection-store release candidate was validated with
the gated live integration test against a non-sensitive sample wiki and a
loopback Docker Redis database. A separate manual smoke used explicit
namespace/source-id settings and `fail-fast` policy, then checked `/manifest`,
`/query`, and `/diagnostics/projection-store`.

The gate passed. Diagnostics did not expose Redis URL details, port,
credentials, or local source paths. The manual validation namespace was cleaned
after the check, and the reused Docker Redis container was stopped. No raw Redis
keys, cached payloads, private paths, or private wiki snippets are stored in
this spec.

## Skipped Or Deferred

- RedisVL and embedding-backed semantic search are deferred.
- Multi-root serving is deferred to a separate source registry design.
- Stable diagnostics schema guarantees beyond the current OpenAPI model are
  deferred until an operator workflow depends on them.
