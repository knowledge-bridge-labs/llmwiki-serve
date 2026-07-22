# Plan: Freshness Loop Test Matrix

## Approach

Start with Loop 0 before any new freshness implementation. Agents must review
prior art and library fit from trusted sources, then record whether the work
will adopt, wrap, defer, or custom-build the chosen strategy.

Use `tests.md` as the standardized matrix for freshness loop coverage. Each row
gets a stable `FL-*` identifier so future tests can cite the same scenario
instead of inventing strategy-specific names.

The matrix separates freshness authority from projection storage:

- Strict scan validates source files directly.
- Refresh interval intentionally skips validation until the interval expires.
- Producer manifest mode trusts an explicit producer marker or falls back to
  strict scan when the marker is unavailable or unsafe.
- Future watcher mode uses events only to mark dirty state.
- Redis mode stores derived projections, but never decides freshness by itself.

Candidate categories for Loop 0 include watcher providers, producer/build
manifest patterns, projection/cache stores, graph/search or traversal libraries
where freshness affects query shape, and contract/e2e test tooling. Custom code
needs a short fit rationale when trusted OSS or standards-backed options do not
meet source-boundary, safety, portability, or dependency constraints.

## Affected Areas

- Specs: freshness loop matrix and producer manifest test-plan link.
- Research: concise prior-art notes such as
  `docs/research/2026-07-17-freshness-invalidation-core-libraries.md`.
- Future tests: `tests/test_service.py`, `tests/test_public_api.py`, release
  smoke coverage, and optional performance/probe scripts.
- Production source: no changes in this scaffold.

## Risks

- Risk: the matrix becomes stale as new strategies land.
  Mitigation: require new freshness work to update the matching `FL-*` rows.

- Risk: Redis tests accidentally treat cache hits as freshness evidence.
  Mitigation: keep Redis rows phrased around validated signatures or generations.

- Risk: watcher tests overfit one backend.
  Mitigation: test the dirty-flag contract separately from backend event probes.

## Rollout

- Loop 0: complete `PA-010` before selecting a custom provider, cache, or
  freshness authority.
- Current branch: document the repeatable matrix and map existing coverage.
- Next unit-test pass: add explicit test names or comments that cite relevant
  `FL-*` IDs.
- Future watcher branches: add implementation tests by filling the deferred rows
  before expanding e2e smoke.
