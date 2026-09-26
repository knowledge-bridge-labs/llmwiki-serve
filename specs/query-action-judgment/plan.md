# Plan: Query-Action Judgment

## Implementation

- Add `RetrievalActionGuidance` public model and optional context-pack field.
- Add `llmwiki_serve.query_action_judgment` for configuration, payload
  construction, provider calls, response normalization, and fail-open behavior.
- Route query-action configuration through service, app factory, and CLI.
- Run judgment after `LlmWikiService.context` builds the normal context pack.
- Advertise `llmwiki_retrieval_action_guidance` only when enabled.
- Add focused tests for default-off compatibility, enabled guidance, masking,
  missing-key behavior, provider failure, HTTP, MCP, and OpenAPI.
- Update README, architecture docs, ADR, changelog, OpenAPI, and release
  evidence.

## Risks

- Provider calls can export sensitive state if the payload expands beyond
  structural summaries.
- Making the option request-controlled would let clients trigger unexpected
  provider export.
- Provider latency may make a single query slower even if it reduces later
  agent tool calls.
- Treating guidance as an answer-quality signal would overstate the feature.

## Rollout

Ship as `0.2.13` with the feature disabled by default. Operators must set
`--query-action-judge system-one` or `LLMWIKI_QUERY_ACTION_JUDGE=system-one`
and provide an approved provider key through environment configuration.
