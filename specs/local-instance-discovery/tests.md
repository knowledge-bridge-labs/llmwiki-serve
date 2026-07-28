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
- Primary process discovery obtains exact argv, cwd, and create time through the
  OS provider on Windows and Linux, with graceful warnings when provider-level
  access is unavailable.
- Process discovery recognizes console script, `python -m llmwiki_serve`,
  `python -m llmwiki_serve.cli`, module path, and `uv run llmwiki-serve serve`
  command shapes, including quoted paths with spaces.
- With an empty registry, a healthy `/health` response from a process-discovered
  `llmwiki-serve serve` endpoint appears in `ls --json` as
  `registered=false`, `orphan=true`, and `discovery_source=process`.
- With an empty registry, an exact process-discovered `llmwiki-serve serve`
  endpoint whose `/health` probe times out or cannot connect appears as
  `status=unhealthy`, `service_verified=false`, and carries a reason note.
- When wrapper and child processes advertise the same endpoint, `ls --json`
  reports the OS socket listener PID rather than an arbitrary wrapper PID.
- Listener PID verification is true only when the listener is a parsed llmwiki
  serve candidate or a descendant of one; unrelated socket owners carry an
  unverified note.
- Process root arguments that are relative paths are resolved against the
  process cwd when cwd is available.
- A server discovered from both registry and process table on the same endpoint
  dedupes into one registered instance.
- A non-llmwiki process or non-llmwiki/invalid identity health response is
  ignored, while an exact matching process whose health probe fails before
  identity verification remains visible as unhealthy and unverified.
- A live reused PID with failed or non-llmwiki health is reported as
  `unhealthy` rather than healthy, and it is not rediscovered from process
  state unless a matching serve command and healthy endpoint exist.
- A registry record with mismatched `process_create_time` is treated as stale
  with a `pid-reused` note. A schema version 1 registry record without
  `process_create_time` still loads.
- An explicit `--probe-port` can be used for manual loopback diagnostics, but no
  fixed ports are guessed by default.
- `--probe-timeout-seconds` controls local health probe timeout for `ls` and
  `status`; the default is conservative enough for normal local startup jitter.
- Human and JSON output do not expose raw command lines, launcher arguments, or
  credential-bearing serve options.
- Provider-level psutil AccessDenied for cmdline, cwd, create time, parent PID,
  or listener sockets produces aggregated degraded warnings without per-process
  spam.
- Windows/POSIX fallback provider failures and procfs partial read failures
  produce sanitized degraded warnings without raw stdout, stderr, argv, command
  strings, PIDs, paths, or exception text.
- Repository-only CI workflow checks skip cleanly when `.github` is absent from
  an extracted sdist.
- Network manifest and health responses still omit local roots.

## Validation

- Focused CLI tests with a temporary `LLMWIKI_SERVE_STATE_DIR`.
- Focused process-discovery and manual-probe tests using an ephemeral local HTTP
  server.
- Focused provider tests using synthetic process entries and socket ownership
  maps so wrapper and degraded-provider behavior is deterministic.
- Windows real-server smoke on an arbitrary OS-assigned or free local port with
  an empty registry, verifying that `ls --json` finds the endpoint without a
  fixed-port scan and reports the listener PID.
- Full `pytest -q`.
- CLI smoke against the sample wiki.
