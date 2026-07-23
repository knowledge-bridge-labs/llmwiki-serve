# Plan: Operator Default Limits

## Approach

Add explicit constants for built-in context and graph defaults. Validate
operator-configured defaults once at app or CLI startup, then pass the resolved
values through HTTP handlers, MCP JSON-RPC handling, and MCP Streamable HTTP
tool signatures.

Keep per-request behavior forgiving for network MCP calls by clamping malformed
or absent call arguments to the configured default. Keep HTTP request validation
unchanged for explicit invalid `/query` bodies.

## Affected Areas

- Source modules: `src/llmwiki_serve/api.py`, `src/llmwiki_serve/cli.py`,
  `src/llmwiki_serve/service.py`
- Tests: focused API/MCP/CLI coverage in `tests/test_service.py` and public
  OpenAPI coverage in `tests/test_public_api.py`
- Docs/contracts: `README.md`, `docs/architecture.md`, `docs/openapi.json`

## Risks

- Risk: tool descriptions lose issue #20 metadata scoping.
  Mitigation: compose limit details inside the existing metadata prefixing
  helper.

- Risk: custom `/query` defaults cannot be reflected precisely in the static
  module-level Pydantic request model.
  Mitigation: default `create_app(...)` still exports the built-in default, and
  runtime handlers use the configured default only when the request omits
  `limit`.

- Risk: lower graph default surprises clients that depended on omitted limits.
  Mitigation: explicit `limit` remains available through HTTP and MCP up to the
  existing maximum.

## Rollout

- Update spec and docs.
- Implement defaults and CLI/env resolution.
- Add focused tests for HTTP graph defaults, MCP no-arg defaults, Streamable
  HTTP tool schema/description defaults, and CLI/env propagation.
- Regenerate and check OpenAPI.
- Run targeted pytest and lint/type checks as time allows.
