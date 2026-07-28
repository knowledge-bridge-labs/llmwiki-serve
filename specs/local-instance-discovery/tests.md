# Tests: Local Instance Discovery

## Acceptance

- `serve` writes a registry record before the HTTP server starts and removes it
  after graceful shutdown.
- `llmwiki-serve ls --json` reports PID, host, port, URL, root, source id,
  bundle id, adapter, page counts, start time, health, stale status, and notes.
- Stale records remain visible by default and are removed with `--prune-stale`.
- Duplicate bundle ids and same-root or parent/subfolder roots are called out in
  `notes`.
- Network manifest and health responses still omit local roots.

## Validation

- Focused CLI tests with a temporary `LLMWIKI_SERVE_STATE_DIR`.
- Full `pytest -q`.
- CLI smoke against the sample wiki.
