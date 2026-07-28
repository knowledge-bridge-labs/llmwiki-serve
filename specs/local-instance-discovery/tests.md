# Tests: Local Instance Discovery

## Acceptance

- `serve` writes a registry record before the HTTP server starts and removes it
  after graceful shutdown.
- `llmwiki-serve ls --json` reports PID, host, port, URL, root, source id,
  bundle id, adapter, page counts, start time, health, stale status, and notes.
- Stale records remain visible by default and are removed with `--prune-stale`.
- Duplicate bundle ids and same-root or parent/subfolder roots are called out in
  `notes`.
- Windows and POSIX command-line parsing identifies `llmwiki-serve serve`,
  extracts `--host`, `--port`, and the root argument, and ignores non-serve
  commands.
- With an empty registry, a healthy `/health` response from a process-discovered
  `llmwiki-serve serve` endpoint appears in `ls --json` as
  `registered=false`, `orphan=true`, and `discovery_source=process`.
- A server discovered from both registry and process table on the same endpoint
  dedupes into one registered instance.
- A non-llmwiki process, failed health probe, or non-llmwiki health response is
  ignored.
- An explicit `--probe-port` can be used for manual loopback diagnostics, but no
  fixed ports are guessed by default.
- Network manifest and health responses still omit local roots.

## Validation

- Focused CLI tests with a temporary `LLMWIKI_SERVE_STATE_DIR`.
- Focused process-discovery and manual-probe tests using an ephemeral local HTTP
  server.
- Full `pytest -q`.
- CLI smoke against the sample wiki.
