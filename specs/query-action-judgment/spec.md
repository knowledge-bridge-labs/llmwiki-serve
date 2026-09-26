# Spec: Query-Action Judgment

## Status

Implementation branch.

## Problem

Direct agents using `llmwiki-serve` often start with `/query` or
`llmwiki_context`, then decide whether to answer, read returned pages, search
again, inspect graph context, or ask the user for clarification. Existing
`retrieval_guidance` gives source-derived retrieval hints, but it does not make
a compact next-action recommendation.

The requested System-One/Jev integration should improve this next-action
choice without changing default retrieval behavior or exporting private wiki
content by default.

## Goals

- Add default-off System-One/Jev query-action judgment.
- Run judgment only after normal context pack assembly.
- Return additive `retrieval_action_guidance` to HTTP, MCP, CLI, and Python
  service callers when enabled.
- Keep default lexical query/search/read/graph behavior unchanged.
- Export only masked, structural state to the provider.
- Fail open when the feature is disabled, unconfigured, or the provider fails.
- Keep provider opt-in under operator control, not request-body client control.

## Non-Goals

- Do not rerank evidence or alter graph projection.
- Do not synthesize final answers.
- Do not add hosted RAG, auth, agent runtime hosting, or a raw query language.
- Do not enable provider calls by default.
- Do not claim a universal speed, quality, or token-efficiency improvement
  without separate public benchmark evidence.

## Requirements

- `REQ-QAJ-001`: Default configuration omits `retrieval_action_guidance` from
  serialized context packs and never calls the provider.
- `REQ-QAJ-002`: Operator opt-in is available through environment and CLI
  configuration for `query` and `serve`.
- `REQ-QAJ-003`: HTTP `/query`, MCP `llmwiki_context`, CLI `query`, and Python
  `LlmWikiService.context` share the same service-level judgment behavior.
- `REQ-QAJ-004`: The provider payload includes only masked query text and
  structural summaries, not raw page text, raw snippets, page ids, source-ref
  labels, raw paths, local roots, private URLs, or obvious credentials.
- `REQ-QAJ-005`: Missing provider credentials return status `unconfigured`
  without modifying normal evidence, orientation, graph, or limitations.
- `REQ-QAJ-006`: Provider failures return status `failed` with sanitized
  diagnostics and preserve normal context output.
- `REQ-QAJ-007`: Enabled service manifests advertise
  `llmwiki_retrieval_action_guidance`; disabled services do not.
- `REQ-QAJ-008`: The public OpenAPI contract exposes
  `RetrievalActionGuidance` as an optional additive response field.

## Compatibility

The feature is additive. Existing clients that ignore unknown response fields
or run with default configuration see the same outputs. Provider guidance is
not used to change core retrieval ranking, source parsing, projection caches,
GraphStore behavior, OKF behavior, or MCP tool names.

## Data Safety

The provider boundary is opt-in and data-minimized. The payload is designed for
fast action judgment, not evidence transfer. It includes counts and structural
signals while omitting raw source content, raw snippets, page ids, source-ref
labels, raw paths, local roots, and obvious credentials. Release reports must
not include provider keys, private endpoint URLs, raw private wiki content, or
absolute local paths.

## Acceptance Criteria

- Focused query-action tests cover default-off compatibility, enabled
  provider guidance, missing-key handling, provider failure, payload masking,
  HTTP output, and MCP output.
- Public API/OpenAPI tests cover the optional response schema.
- Full release validation gates pass before publication.
