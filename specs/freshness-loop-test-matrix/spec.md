# Spec: Freshness Loop Test Matrix

## Status

Draft test-planning scaffold.

## Problem

`llmwiki-serve` has multiple freshness strategies with different correctness
contracts: strict source scanning, refresh intervals, producer manifest markers,
Redis-backed projection caching, and future watcher paths. If each strategy
grows tests independently, regressions can hide in gaps between unit tests,
HTTP/MCP smoke coverage, and performance probes.

## Goals

- Define one repeatable freshness loop matrix for current and future strategies.
- Require Loop 0 prior-art/library-fit review before adding or choosing a
  freshness strategy implementation.
- Keep strict scan as the baseline correctness authority.
- Make bounded staleness, producer-owned freshness, watcher dirty signals, and
  Redis projection caching explicit and testable.
- Give future unit, integration, e2e, and probe tests stable scenario IDs.
- Keep this change limited to planning and test scaffolding.

## Non-Goals

- Do not implement a watcher, Redis cache, or formal producer manifest schema in
  this matrix.
- Do not change production freshness behavior.
- Do not replace existing focused service tests.
- Do not turn Redis or watcher events into source-of-truth freshness signals.

## Requirements

- `REQ-FL-000`: Before implementing a new freshness strategy, provider, or
  cache path, Loop 0 must document trusted-source prior art, candidate tool
  categories, and the adoption or custom-implementation rationale.
- `REQ-FL-001`: The matrix must cover strict scan, refresh interval, producer
  manifest, future watcher dirty flag, and Redis projection cache modes.
- `REQ-FL-002`: Current modes must map to existing or planned unit tests for
  Markdown, sidecar graph, adapter marker/config, add/delete, and visibility
  changes.
- `REQ-FL-003`: Future watcher tests must prove watcher events set dirty state
  only, and watcher errors or uncertain state fall back to strict validation.
- `REQ-FL-004`: Redis tests must prove cached projections are reused only after
  a validated source signature, producer generation, or dirty-state authority
  check.
- `REQ-FL-005`: E2E accumulation must exercise the same freshness loop through
  CLI, HTTP, MCP-style JSON-RPC, MCP Streamable HTTP, and optional A2A-style
  surfaces where those surfaces are relevant.

## AX Rubric Mapping

This matrix directly targets the AX knowledge warehouse rubric for freshness and
snapshot integrity:

- Score `2`: strict source signature is available and regression-tested.
- Score `3`: projection signatures, cache boundaries, and freshness diagnostics
  are visible enough to debug stale answers.
- Score `4`: immutable producer generations, an atomic current pointer, and
  fallback behavior are regression-gated.

It also supports evidence fidelity and agent traversal utility because stale
projection state can produce correct-looking citations or graph traversals over
the wrong generation. Any new freshness strategy should therefore add tests that
sample `read`, `search`, `context`, `graph`, and `graph_neighbors`, not just
internal cache counters.

It also supports prior-art and library fit: freshness work must pass the Loop 0
gate before agents choose a custom watcher, producer marker, cache, or
contract-test implementation.

## Compatibility

This is a documentation and test-planning change only. It does not alter CLI
options, HTTP/MCP/A2A-style response shapes, source adapter contracts, or default
freshness behavior.

## References

- Architecture: `docs/architecture.md`
- Producer manifest ADR:
  `docs/decisions/2026-07-17-producer-manifest-freshness-boundary.md`
- Producer manifest spec: `specs/producer-manifest-freshness/`
- Redis projection store spec: `specs/redis-projection-store/`
- Freshness research:
  `docs/research/2026-07-17-freshness-invalidation-core-libraries.md`
  and `docs/research/2026-07-17-hadoop-spark-freshness-patterns.md`
