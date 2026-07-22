# Tasks: Redis Projection Store

- [x] Confirm ADR requirement for derived-cache and sensitivity boundary.
- [x] Create Redis projection-store spec files.
- [x] Keep memory projection store as the default backend.
- [x] Add optional Redis/Valkey projection store selected by CLI or environment.
- [x] Add `llmwiki-serve[redis]` optional dependency extra.
- [x] Add friendly missing-extra and missing-URL errors.
- [x] Add namespace, source-id, projection-signature, and schema-version keying.
- [x] Sanitize Redis key parts and avoid local-root path keys.
- [x] Serialize derived `WikiIndex` payloads without local root paths.
- [x] Add fallback-local and fail-fast failure policies.
- [x] Treat corrupt or mismatched payloads as cache misses.
- [x] Add redacted `/diagnostics/projection-store` endpoint.
- [x] Update README and architecture docs for Redis operator posture.
- [x] Update release checklist for optional Redis validation and PyPI notes.
- [x] Run optional live Redis validation before a Redis-affecting release.
- [x] Document user-purpose Redis setup, Docker/managed Redis guidance, and
  retention/cleanup posture.
- [ ] Ingest this spec, ADR, and release docs into the project LLMWiki after
  maintainer review if projection validation is requested.

## Validation

Docs-only validation for this refresh:

```bash
rg -n "redis|Redis|projection store" docs specs README.md
git diff --check
```

Implementation validation expected for Redis-affecting releases:

```bash
uv run pytest -q tests/test_service.py -k "projection_store or redis"
LLMWIKI_REDIS_URL=redis://127.0.0.1:6379/0 \
  uv run pytest -q tests/test_redis_projection_store_integration.py
uv run python scripts/export_openapi.py --check
uv run python scripts/release_smoke.py
```

Redis release-candidate live validation recorded on 2026-07-22:

- `uv sync --extra dev --extra redis`
- `LLMWIKI_REDIS_URL=<loopback Docker Redis DB> uv run pytest -q
  tests/test_redis_projection_store_integration.py`
- Manual smoke with explicit namespace/source id and `fail-fast` Redis policy
  covered `/manifest`, `/query`, and
  `/diagnostics/projection-store`.
- Diagnostics redaction passed; no Redis URL, port, credential, local root, raw
  key, cached payload, or private wiki snippet was recorded.
- Manual namespace keys were deleted after the smoke, and the reused Docker
  Redis container was stopped.

## Follow-Up Work

- Decide whether a future release should reject unsafe explicit namespace or
  source-id inputs earlier at the CLI boundary instead of relying on key-part
  sanitization.
- Keep RedisVL semantic/vector search deferred until projection-cache behavior
  is stable and separately specified.
