# Spec: AX Knowledge Warehouse Rubrics

## Status

Draft.

## Problem

`llmwiki-serve`, `llmwiki-agent-bridge`, and `llmwiki-chat` are intended to
serve AX knowledge warehouses, not just small demo wikis. Development needs a
repeatable loop that tells coding agents which qualities must improve before a
change is ready.

## Goals

- Define stable rubrics for loop-engineering work across the `llmwiki-*`
  repositories.
- Require a Loop 0 prior-art/library-fit gate before agents choose custom
  implementations for new loop work.
- Tie each rubric to deterministic tests, contract checks, or documented
  validation commands.
- Keep repo files canonical. LLMWiki projections can index these files, but do
  not become the source of truth.

## Non-Goals

- Do not define a new external CKG standard.
- Do not require one storage backend, runtime provider, or chat UI.
- Do not store private endpoints, private wiki content, raw traces, credentials,
  or local absolute paths as public evidence.

## Rubrics

Scores use a 0-4 scale.

| Rubric | Score 4 expectation |
| --- | --- |
| Evidence fidelity | Results preserve source identity, citation identity, graph identity, and exact-read follow-up paths across single-source and multi-source workflows. |
| Freshness and snapshot integrity | Projection freshness has explicit semantics for strict, bounded-stale, and producer-signaled modes, with tests for content, sidecar graph, restart, and refresh behavior. |
| Agent traversal utility | Host agents can do orientation-first retrieval, bounded graph-neighborhood traversal, exact reads, and source-bundle inspection without fetching full graph payloads by default. |
| Connection setup and interoperability | Chat, bridge, and serve expose compatible defaults, source/runtime discovery, readiness semantics, direct-source and bridge-managed-source paths, and actionable diagnostics so a user can connect a local stack without stale selections blocking a healthy path. |
| Prior-art and library fit | Before implementation, agents document trusted-source research across relevant OSS libraries, standards, and protocol/tooling options; the selected approach uses or extends existing tools when fit is strong and records why custom work is justified when not. |
| Loop observability and evalability | Regressions are diagnosable through stable test IDs, trace IDs, redacted diagnostics, contract checks, and small runnable loop matrices. |
| Safety and governance | Public surfaces enforce source visibility, draft boundaries, URL policy, bearer/CORS policy, and redaction of private roots, credentials, and upstream error bodies. |

## Loop 0 Trusted-Source Criteria

Loop 0 evidence should prefer official docs, upstream repositories, published
specs, maintained package registry metadata, release/issue notes, and local
reproducible probes. Candidate categories include filesystem watchers,
producer/build manifest patterns, projection/cache stores, graph/search or
traversal libraries, and API/contract test tooling. Avoid relying on secondary
summaries unless they point back to these trusted sources.

## Compatibility

The rubric is an engineering gate. It does not change public HTTP, MCP, A2A-style,
CLI, or package contracts by itself.

## Data Safety

Test fixtures must use synthetic roots and example origins. Do not commit local
vault paths, Tailscale URLs, provider keys, raw private wiki content, or real
runtime traces.
