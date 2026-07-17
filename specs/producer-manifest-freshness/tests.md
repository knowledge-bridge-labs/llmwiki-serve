# Tests: Producer Manifest Freshness

## Standard Matrix

Producer manifest freshness is tracked as `FS-PRODUCER` in the shared freshness
loop matrix at `specs/freshness-loop-test-matrix/tests.md`. New unit or e2e
coverage should cite the matching `FL-*` scenario ID from that matrix so strict
scan, refresh interval, producer manifest, future watcher dirty flag, and future
Redis projection cache tests accumulate consistently.

## Acceptance Criteria

- `REQ-001`: Existing strict tests still pass.
- `REQ-002`: Configured manifest reduces repeated strict graph-neighborhood
  checks to marker-file validation.
- `REQ-003`: Source file changes without manifest changes reuse the cached
  projection.
- `REQ-004`: Manifest changes trigger a rebuild and expose updated source
  content.
- `REQ-005`: Missing manifest falls back to existing source scanning and detects
  source changes.
- `REQ-007`: Marker-only changes do not change public `projection.signature` or
  `bundle_id`; source changes update that identity only after the marker changes.

## Unit Tests

- Test source changes are not observed until manifest changes.
- Test missing manifest fallback detects source changes.
- Test outside-root manifest is ignored and falls back to strict scanning.
- Test symlinked manifest is treated as unsafe and falls back to strict
  scanning.
- Test producer manifest mode publishes the same content-derived projection
  signature and source-bundle identity as strict mode after initial load and
  marker-triggered rebuilds.

## Integration / Contract Tests

- OpenAPI contract remains unchanged except for the existing graph neighborhood
  surface from the parent branch.
- Release smoke continues to pass with the default strict behavior.

## Manual Checks

- Benchmark strict source scan vs producer manifest marker on synthetic 1,200
  page wiki.

## Skipped Or Deferred

- Filesystem watcher support is deferred.
- Formal producer manifest schema is deferred.
