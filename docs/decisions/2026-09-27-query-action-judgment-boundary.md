# ADR: Opt-In Query-Action Judgment Boundary

## Status

Accepted.

## Context

`llmwiki-serve` already returns cited evidence, orientation, graph hints, and
agent-guided lexical metadata. A direct agent still has to decide whether the
current `/query` result is enough or whether it should read a page, search
again, inspect graph context, or ask for clarification.

System-One/Jev-style judging can help with that fast decision, but it is an
external provider boundary. The service must preserve its local-first default,
avoid exporting raw source content by accident, and avoid making provider
availability part of core retrieval correctness.

## Decision

Add an optional query-action judgment stage after normal context assembly.
Expose it only through operator configuration:

- `LLMWIKI_QUERY_ACTION_JUDGE=system-one`
- `llmwiki-serve query --query-action-judge system-one`
- `llmwiki-serve serve --query-action-judge system-one`

Use `LLMWIKI_QUERY_ACTION_JUDGE_API_KEY` for the provider key. `TYPESAFE_API_KEY`
and `JEV_API_KEY` are accepted as compatibility aliases. Provider keys remain
environment-only and must not be exposed through request bodies or CLI
arguments.

Do not add a request-body flag that lets arbitrary clients trigger provider
export. Advertise `llmwiki_retrieval_action_guidance` only when the feature is
enabled.

The provider receives a masked structural payload, not raw wiki content. The
payload may include redacted query text, result counts, score buckets, route
labels, snippet lengths, source-ref counts, path depth, graph counts, and
overlap ratios. It must omit raw page text, raw snippets, page ids, source-ref
labels, raw paths, local roots, private URLs, and obvious credentials.

Return the provider result as additive `retrieval_action_guidance` with status,
recommended action, confidence, evidence-sufficiency score, bounded target
ids, and sanitized diagnostics. If the feature is disabled, omit the field. If
the API key is missing or the provider fails, fail open without changing
evidence, ranking, graph output, or answerability.

## Consequences

- Existing clients keep the same default `/query`, `/search`, `/read`,
  `/graph`, MCP, and CLI behavior.
- Approved deployments can use System-One/Jev to reduce unnecessary follow-up
  tool calls or to route toward graph/search/read when structural evidence
  suggests it.
- The result is guidance for the caller, not an authority over source facts or
  final answers.
- The feature adds an external provider trust boundary only when explicitly
  enabled by the operator.

## Follow-Ups

- Collect public-safe evidence on whether the guidance reduces agent tool
  steps across representative fixtures.
- Mirror the operator guidance in `llmwiki-docs` release/status pages.
- Revisit provider response schema validation if additional System-One/Jev
  answer shapes become stable.

## References

- Spec: `../../specs/query-action-judgment/`
- Architecture: `../architecture.md`
