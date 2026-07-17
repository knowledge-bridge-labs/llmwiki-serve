# Spec: Producer Manifest Freshness

## Status

Draft.

## Problem

Strict freshness currently verifies every projection-affecting Markdown, Org,
adapter marker/config, and `graph/graph.json` file on each request. That keeps
same-size and same-mtime rewrites safe, but for large generated wikis it can
dominate latency even when a client asks for a tiny graph neighborhood.

Some producers can emit a single freshness marker after ingest/compile
completes. If operators explicitly trust that producer marker, `llmwiki-serve`
can check the marker instead of rescanning every source file on every request.

## Goals

- Add an opt-in producer manifest freshness path for long-running servers.
- Keep default behavior unchanged and strict.
- Fall back to normal strict source scanning when the configured manifest is
  missing.
- Treat the producer manifest as a freshness marker only, not as source content,
  credentials, or executable configuration.
- Keep public `projection.signature`, `bundle_id`, and source-bundle identity
  content-derived rather than marker-derived.
- Measure the expected improvement against graph-neighborhood strict mode.

## Non-Goals

- Do not add a filesystem watcher in this slice.
- Do not bypass freshness checks by default.
- Do not trust mutable "latest projection" cache keys without an explicit
  producer marker.
- Do not define a full producer manifest schema for all upstream LLMWiki
  variants.

## Requirements

- `REQ-001`: With no producer manifest configured, strict freshness behavior is
  unchanged.
- `REQ-002`: With a configured manifest present, no-change requests validate
  only the manifest marker path.
- `REQ-003`: If source files change but the producer manifest does not, the
  cached projection may be reused. This is the explicit opt-in tradeoff.
- `REQ-004`: When the producer manifest changes, the service rebuilds the
  projection on the next request.
- `REQ-005`: If the configured manifest is missing, the service falls back to
  normal source scanning.
- `REQ-006`: If the configured manifest is a symlink, the service treats it as
  unsafe and falls back to normal source scanning.
- `REQ-007`: Marker-only changes do not change public
  `projection.signature` or `bundle_id`; source changes update that public
  identity only after the marker changes and the projection is rebuilt.
- `REQ-007`: Producer manifest marker state is only an internal freshness key.
  On initial load and marker changes, public projection and source-bundle
  identity are derived from projection-affecting source content.

## User / Agent Flow

1. A producer writes compatible Markdown/wiki output.
2. After a complete ingest/compile operation, the producer writes or atomically
   replaces a small manifest marker file.
3. The operator starts `llmwiki-serve` with `--producer-manifest <path>`.
4. Long-running requests check the marker for strict freshness instead of
   digesting every source file.

## Compatibility

- CLI: additive `--producer-manifest` serve option.
- HTTP/MCP/A2A-style: no response shape change.
- Source-folder adapters: no source format requirement unless the operator
  opts in.
- Existing clients: no behavior change by default.

## Data Safety

The manifest path is local operator input and is not exposed through network
responses. The manifest content is used only for digest-based freshness. It
must not contain secrets, credentials, or private endpoint URLs.

## Open Questions

- Should a future formal schema include page counts, producer id, build id,
  source hash, and completed-at timestamp?
- Should the manifest be represented in projection diagnostics after the
  freshness diagnostics branch is merged?
- Should production mode verify a producer-declared `projection_signature` with
  one initial full scan before trusting the marker for subsequent no-change
  requests?

## References

- ADR: `docs/decisions/2026-07-17-producer-manifest-freshness-boundary.md`
