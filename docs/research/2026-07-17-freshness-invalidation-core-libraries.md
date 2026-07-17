# Freshness Invalidation Core Library Survey

Date: 2026-07-17

Status: local research and benchmark evidence. Do not treat this as a merged
provider decision.

## Context

`llmwiki-serve` currently keeps default strict freshness by validating the
source tree before reusing an in-memory projection. That is robust, but on large
wiki roots the no-change request path is dominated by file stat/hash work.

Recent local measurements on a synthetic 1,200 page wiki showed:

| Mode | Context median | Full graph median | Neighborhood median |
| --- | ---: | ---: | ---: |
| Strict source scan | 819.554 ms | 840.378 ms | 752.939 ms |
| Producer manifest marker | 9.715 ms | 10.714 ms | 1.196 ms |

This means the main opportunity is not graph traversal itself. It is avoiding
unnecessary full-tree freshness validation when the source is known clean.

## Loop 0 Gate Use

This note is current `PA-010` evidence for freshness invalidation work. Refresh
it before implementation when target platforms, dependency constraints, or
source-boundary assumptions change. Trusted sources here are official docs,
upstream package pages, published specs, and local reproducible probes; candidate
categories are watcher providers, producer/build manifest patterns, advanced
watcher daemons, projection cache boundaries, and related contract/test tooling.
This note does not by itself finalize a provider or product posture.

## Candidates Reviewed

### 1. `watchfiles`

`watchfiles` is a Python package backed by Rust `notify`. Its API watches files
or directories recursively by default, supports sync and async usage, filters,
debounce, step intervals, and polling fallback controls.

Relevant official docs:

- https://watchfiles.helpmanual.io/api/watch/
- https://pypi.org/project/watchfiles/

Fit for `llmwiki-serve`:

- Best Python-native default watcher candidate.
- Already present transitively in this environment through the dev stack.
- Good match for a dirty-flag provider because it batches events naturally.
- Should not be used as the sole correctness authority because the public API
  does not expose a strong Watchman-style recrawl/overflow contract.

### 2. `watchdog`

`watchdog` is a mature Python filesystem event library. It supports native APIs
on Linux, macOS, Windows, and a polling fallback. Official docs call out platform
differences and resource limits, such as Linux inotify needing one watch per
directory.

Relevant official docs:

- https://python-watchdog.readthedocs.io/en/stable/index.html
- https://python-watchdog.readthedocs.io/en/stable/installation.html
- https://pypi.org/project/watchdog/

Fit for `llmwiki-serve`:

- Strong ecosystem maturity and simple API.
- Good fallback if avoiding Rust wheels matters.
- App-level debounce/coalescing has to be built around it.
- In this Windows synthetic test it produced very low event latency, but that
  does not remove the need for dirty-flag plus signature validation.

### 3. Producer Manifest Marker

This is not a watcher library. It is a producer/consumer contract: an ingest or
compile producer atomically publishes a small freshness marker after a complete
generation is ready. `llmwiki-serve` can then check the marker instead of
rescanning every source file on no-change requests.

Relevant precedents:

- Ninja depfile/restat patterns: https://ninja-build.org/manual.html
- Bazel action metadata/CAS: https://bazel.build/remote/caching
- SQLite WAL/atomic commit publication pattern:
  https://sqlite.org/wal.html and https://www.sqlite.org/atomiccommit.html
- OCI descriptor shape for digest and size metadata:
  https://specs.opencontainers.org/image-spec/descriptor/
- TUF metadata version/expiry/signature ideas:
  https://theupdateframework.github.io/specification/latest/

Fit for `llmwiki-serve`:

- Best fit for controlled compiler/ingest pipelines.
- Lowest idle overhead.
- Huge no-change speedup.
- Changes the trust model: if the producer fails to update the marker,
  `llmwiki-serve` can serve stale projection.
- Should remain explicit opt-in, not the default strict behavior.

### Watchman As An Optional Future Backend

Watchman is a daemon designed to watch roots, record changes, support clocks,
subscriptions, settling, recrawls, and conservative recovery behavior.

Relevant official docs:

- https://facebook.github.io/watchman/
- https://facebook.github.io/watchman/docs/cmd/query
- https://facebook.github.io/watchman/docs/clockspec
- https://facebook.github.io/watchman/docs/cmd/subscribe

Fit for `llmwiki-serve`:

- Likely strongest backend for large managed developer machines.
- Operationally heavier: requires installed daemon, PATH/state/log management,
  `.watchmanconfig`, and platform-specific support handling.
- Not locally tested in this run because `watchman` was not on PATH.

## Local Probe

Probe script:

```powershell
uv run --with watchfiles --with watchdog python scripts\freshness_strategy_probe.py --pages 1200 --events 50
```

The probe creates a synthetic wiki, mutates Markdown pages, and records event
latency or manifest refresh behavior.

### Event Watcher Results

| Backend | Version | Events seen | Missed | Startup | Median event latency | p95 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `watchfiles` | 1.2.0 | 50 | 0 | 251.592 ms | 20.825 ms | 21.006 ms |
| `watchdog` | 6.0.0 | 50 | 0 | 251.260 ms | 0.242 ms | 0.761 ms |

Interpretation:

- Both libraries detected all 50 sequential writes in this Windows local test.
- `watchdog` was much faster in this synthetic event-latency measurement.
- `watchfiles` latency includes the configured batching/debounce behavior, which
  is useful for avoiding rebuild storms.
- Neither result proves correctness under burst overflow, editor temp-file
  patterns, network paths, or watcher restarts.

### Producer Manifest Results

| Metric | Median |
| --- | ---: |
| Strict no-change `read("index")` | 823.891 ms |
| Manifest no-change `read("index")` | 1.008 ms |
| Manifest refresh after marker update | 1581.619 ms |

Interpretation:

- Producer manifest mode eliminates almost all no-change freshness overhead.
- Refresh after marker update still rebuilds the projection, so it is expensive.
- The probe confirms the trust boundary: source changes are stale until the
  marker changes.

## Recommended Architecture

Use a hybrid model:

1. Keep strict scan as the default correctness authority.
2. Add an optional watcher provider interface for serve mode.
3. Use watcher events only as a dirty flag. If clean, reuse memory/Redis
   projection. If dirty, run the existing strong signature/projection path
   before serving updated content.
4. Make `watchfiles` the first optional Python-native watcher provider because
   it is simple, recursive, batched, and easy to ship.
5. Keep `watchdog` as a documented alternative or later provider if real-world
   tests show better behavior for Windows roots.
6. Keep producer manifest mode separate for controlled compiler/ingest
   pipelines.
7. Add Watchman only as an optional advanced backend for large managed roots.

## Producer Manifest Hardening Direction

The current marker-only contract is enough for explicit producer-owned
freshness. Rich producer integrations should move toward a staged generation
model:

1. Producer writes outputs to `.llmwiki/staging/<run_id>/`.
2. Producer computes output descriptors and projection signature.
3. Producer validates the generation.
4. Producer atomically publishes `.llmwiki/current.json` last.
5. `llmwiki-serve` reads only `current.json` and the referenced manifest for
   freshness.
6. If memory/Redis has a matching `projection_signature`, reuse it.
7. If cache misses, rebuild from the committed generation only.

Suggested manifest fields:

```json
{
  "schema_version": "llmwiki.producer-manifest.v1",
  "source_id": "project-alpha",
  "producer": {"name": "llm-wiki-compiler", "version": "x.y.z"},
  "generation": {
    "id": "2026-07-17T12-00-00Z-abc123",
    "sequence": 42,
    "status": "committed",
    "completed_at": "2026-07-17T12:00:00Z",
    "expires_at": "2026-07-24T12:00:00Z"
  },
  "projection": {
    "signature": "sha256:...",
    "adapter": "llmwiki-markdown",
    "page_count": 123,
    "graph_node_count": 456,
    "graph_edge_count": 789
  },
  "outputs": [
    {
      "path": "index.md",
      "media_type": "text/markdown",
      "size": 1234,
      "digest": "sha256:..."
    }
  ],
  "inputs": {
    "strategy": "producer-verified",
    "tree_digest": "sha256:...",
    "watchman_clock": "c:123:456"
  }
}
```

## Implementation Notes

- Watchers should mark the service dirty, not rebuild inside the event callback.
- Dirty state should bypass `--refresh-interval-seconds`; interval should never
  hide known writes.
- Watcher errors, queue overflow, backend exit, permission errors, or unknown
  health should force fallback to strict scan.
- Debounce must be configurable. Too small causes rebuild storms; too large
  delays visibility.
- Ignore paths should match existing signature ignore rules:
  `.git`, `node_modules`, `.venv`, `__pycache__`, `dist`, `build`.
- Do not follow symlinks for freshness authority.
- Keep private local paths out of network diagnostics.

## Next Test Round

Before choosing a production provider, test:

- burst writes of 100, 1,000, and 5,000 files;
- same-size and preserved-mtime rewrites;
- `os.replace()` atomic replacement;
- editor temp-file save patterns;
- `graph/graph.json` updates;
- root moves/deletes;
- Windows network share, OneDrive, and WSL paths if those are target scenarios;
- idle CPU and RSS for 60 seconds.
