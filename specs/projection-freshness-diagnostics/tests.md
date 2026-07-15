# Projection Freshness Diagnostics Tests

## Acceptance Coverage

- Interval-active requests return the cached projection without consulting the
  projection store or rebuilding.
- A changed source can hydrate a matching projection from the projection store.
- A changed source with no stored projection rebuilds once and then reuses the
  refreshed in-process projection.
- Diagnostics report useful backend/cache/freshness state while redacting Redis
  URLs, credentials, private endpoints, and local source roots.
- The OpenAPI schema exposes the extended diagnostics response model.

## Validation Commands

```bash
uv run pytest -q tests/test_service.py tests/test_public_api.py
uv run python scripts/export_openapi.py --check
```
