# Projection Freshness Diagnostics Plan

## Affected Modules

- `src/llmwiki_serve/service.py`: track the last index/freshness branch and
  expose redacted diagnostics.
- `src/llmwiki_serve/api.py`: extend the diagnostics response schema.
- `docs/openapi.json`: regenerate the public OpenAPI contract.
- `tests/test_service.py` and `tests/test_public_api.py`: cover freshness
  branches, redaction, and schema exposure.

## Approach

1. Add private service diagnostics state updated inside `LlmWikiService.index()`.
2. Add lightweight signature scan/path/digest counters to the existing source
   signature cache.
3. Extend `projection_store_diagnostics()` with public-safe fields only.
4. Add focused tests for interval reuse, projection-store hit after source
   change, miss/rebuild once, and diagnostics redaction.
5. Regenerate OpenAPI after the response model changes.

## Risks

- Diagnostics field names become semi-public once shipped.
- Backend error messages can contain operator-specific details, so redaction
  must cover configured URLs, hostnames, credentials, and source roots.
- Counters are process-local observability hints, not durable metrics.
