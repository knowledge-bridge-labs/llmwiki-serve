# Projection Freshness Diagnostics Spec

## Problem

Operators need to understand whether a request reused the in-process projection,
hit a shared projection store, rebuilt from source files, or skipped freshness
work because a refresh interval is active. The diagnostics must be useful for
large local wikis and Redis/Valkey deployments without exposing Redis URLs,
private endpoints, credentials, or local filesystem paths.

## Goals

- Extend `GET /diagnostics/projection-store` with minimal public-safe freshness
  and cache fields.
- Report the last `LlmWikiService.index()` path: interval reuse, unchanged
  signature reuse, projection-store hit, projection-store miss/rebuild, or
  explicit refresh.
- Report public-safe source/cache identifiers, projection signature and bundle
  id when already known, refresh interval state, and signature scan counters.
- Preserve default freshness semantics.

## Non-Goals

- No new cache backend.
- No background filesystem watcher.
- No Redis URL, hostname, credentials, local root, or source path disclosure.
- No change to search ranking, draft filtering, or source bundle semantics.

## Requirements

- Diagnostics responses remain JSON and keep existing fields compatible.
- The diagnostics route must not trigger a rebuild or freshness scan by itself.
- Backend errors returned through diagnostics must be redacted.
- Added identifiers must be limited to public-safe ids already used in network
  manifests or cache keys.
