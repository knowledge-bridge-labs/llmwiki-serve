# Producer Manifest Freshness Boundary

## Status

Draft.

## Context

Strict freshness protects live `llmwiki-serve` processes from stale source
projections by checking source file metadata and content digests on every
request. This is robust but expensive for large generated wikis.

Generated LLMWiki producers can often provide a stronger operational signal:
after ingest/compile completes, they can atomically write a small manifest or
build marker. Operators may prefer to trust that marker instead of rescanning
all source files for every request.

## Decision

Add an opt-in producer manifest freshness mode.

When configured and valid, the service treats the manifest file as the
freshness marker for the served source. If the marker changes, the projection is
rebuilt. If source files change but the marker does not, the cached projection
may remain in use. That is the explicit performance tradeoff.

The marker signature is not used as the public projection identity. On initial
load and when the marker changes, the service computes the content-derived
projection signature from the projection-affecting source files. Public
`projection.signature`, `bundle_id`, and source-bundle identity use that content
signature; marker state remains an internal freshness trust boundary.

The default remains strict source scanning. Missing or unsafe manifest paths
fall back to strict scanning.

This release treats the manifest as a marker file, not as an authoritative
schema document. That marker-only contract is production-usable when the
operator explicitly owns the producer discipline: the producer must update or
atomically replace the marker after every completed source-changing generation.
A future schema can add producer-declared projection signatures and ready-state
metadata, but the current public identity still comes from source content
scanned by `llmwiki-serve` at initial load and marker-triggered rebuilds.

## Consequences

- Large generated wikis can get strict-mode-like request paths close to
  refresh-interval hot paths when the producer reliably updates the marker.
- Operators must understand that correctness depends on producer discipline.
- Redis projection cache can pair with this later: manifest changes identify
  when to compute or look up a projection signature; Redis remains a projection
  storage layer, not a freshness oracle.

## Follow-Ups

- Define a formal producer manifest schema if upstream producers want native
  support.
- Validate producer-declared projection signatures before treating manifest
  content as an authoritative projection identity.
- Add diagnostics once freshness diagnostics and Redis branches are reconciled.
- Consider a watcher/dirty-flag mode as a separate operator-controlled
  freshness strategy.
