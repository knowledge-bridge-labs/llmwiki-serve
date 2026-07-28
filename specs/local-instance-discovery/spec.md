# Spec: Local Instance Discovery

## Status

Draft; amended 2026-07-29 for cross-platform process/listener discovery
hardening.

## Problem

Operators can start multiple `llmwiki-serve serve` processes, but the CLI has no
local command that answers which roots are being served on which ports. Duplicate
parent/subfolder servers currently require process inspection plus HTTP probes.
The registry introduced in this spec is best-effort local state, so servers
started by older installs can still be running even when the current registry is
empty.

## Goals

- Add a local operator-facing `llmwiki-serve ls` command with `--json`.
- Register long-running `serve` processes in a per-user local state directory.
- Report PID, URL, host, port, root, adapter, source id, bundle id, page counts,
  start time, health, stale status, and duplicate/overlap hints.
- Surface healthy unregistered legacy/orphan local servers by discovering actual
  local `llmwiki-serve serve` processes and verifying their parsed endpoints.
- Avoid exposing absolute local paths through HTTP responses by default.
- Keep stale registry handling simple and safe after hard kills.

## Non-Goals

- Do not add daemon management, process killing, or automatic port selection.
- Do not make the registry authoritative source state.
- Do not add remote HTTP instance metadata that exposes local roots.
- Do not maintain a custom per-platform process command-line parser as the
  primary provider when a well-supported OSS process API can return exact argv.
- Do not perform default fixed-port, broad, or arbitrary port scans.

## Requirements

- `REQ-INST-001`: `serve` writes one local instance record after projection
  preflight succeeds and before `uvicorn.run(...)` starts.
- `REQ-INST-001a`: newly written registry records include a best-effort
  `process_create_time` alongside the PID. Readers accept older records without
  this field.
- `REQ-INST-002`: graceful `serve` shutdown removes its registry record.
- `REQ-INST-003`: `ls --json` returns a top-level `instances` array suitable for
  scripts.
- `REQ-INST-004`: default `ls` output is a human-readable table.
- `REQ-INST-005`: records whose PID is no longer running are reported as stale;
  `--prune-stale` removes them after reporting.
- `REQ-INST-006`: live records are probed through local `/health` when possible
  and marked `healthy` or `unhealthy`.
- `REQ-INST-007`: records with the same bundle id, the same root, or
  ancestor/descendant roots get note flags so duplicate parent/subfolder serving
  is visible.
- `REQ-INST-008`: the registry directory defaults to local per-user state and
  can be overridden with `LLMWIKI_SERVE_STATE_DIR`.
- `REQ-INST-009`: default `ls` and `status` use the registry first, then inspect
  the OS process table for actual command lines that indicate
  `llmwiki-serve serve`.
- `REQ-INST-010`: process discovery parses `--host`, `--port`, and the root
  argument when possible. If a matching `serve` command omitted `--host` or
  `--port`, discovery may use the current CLI defaults for that command; it must
  not infer ports from a fixed external candidate list.
- `REQ-INST-011`: unregistered process candidates with a healthy `/health` JSON
  document containing `service: llmwiki-serve` are included as verified healthy
  instances.
- `REQ-INST-011a`: unregistered process candidates whose parsed endpoint times
  out, refuses the connection, or otherwise fails before service identity can be
  verified are still included as `status=unhealthy`, `service_verified=false`,
  with notes that explain the health failure. A 200 `/health` response that
  identifies another service remains excluded.
- `REQ-INST-012`: process-discovered results include `registered=false`,
  `orphan=true`, source identity, version when available, PID, port, URL, health
  status, `discovery_source=process`, and `root_source=process-args` or
  `unknown`. JSON includes additive `service_verified`.
- `REQ-INST-013`: non-matching process command lines and invalid identity
  responses, including non-llmwiki 200 `/health` documents, are ignored without
  surfacing response bodies. Exact matching process candidates whose parsed
  endpoint fails health before identity can be verified remain visible as
  unhealthy/unverified per `REQ-INST-011a`.
- `REQ-INST-014`: operators can disable process discovery with
  `--no-processes`. They can pass explicit `--probe-port` values for manual
  loopback diagnostics, but default discovery must not depend on guessed ports
  or a scan-port environment variable.
- `REQ-INST-015`: if a platform process provider is unavailable, CLI output
  reports degraded discovery and does not fall back to fixed-port scanning.
  Provider warnings must use sanitized allowlisted provider/capability wording
  and must not include raw provider stdout, stderr, command strings, argv,
  process paths, PIDs, or exception text.
- `REQ-INST-016`: process discovery uses a vetted OS process provider as the
  primary source for exact argv, process cwd, and process create time on
  Windows, Linux, and macOS. A degraded fallback may use best-effort
  command-line strings, but fallback results must be marked through discovery
  warnings when provider-level access fails. Partial procfs fallback read
  failures are reported as one aggregated warning per degraded capability,
  without per-process identifiers or raw OS errors.
- `REQ-INST-017`: when multiple wrapper processes advertise the same parsed
  endpoint, process-discovered table and JSON output prefer the actual TCP
  listener PID reported by the OS socket table. The listener PID is marked
  verified only when it is itself a parsed llmwiki serve candidate or a
  descendant of one. If that relationship cannot be verified, discovery adds a
  note that the listener PID was unverified.
- `REQ-INST-018`: process root arguments that are relative paths are resolved
  against the advertising process cwd when cwd is available. If cwd is
  unavailable, discovery keeps the original argument and reports the process
  provider as degraded only if argv/cwd access failed at the provider level.
- `REQ-INST-019`: process discovery must recognize console scripts,
  `python -m llmwiki_serve` / `python -m llmwiki_serve.cli`, source module
  invocations, and launcher/wrapper command lines such as `uv run
  llmwiki-serve serve`, including quoted paths with spaces.
- `REQ-INST-020`: registered records are never treated as healthy from PID
  liveness alone. For registry records with `process_create_time`, readers
  compare the stored and current PID create times. A mismatched create time is
  treated as PID reuse and reported as stale with a `pid-reused` note. A reused
  PID with a failed or non-llmwiki health response is not promoted to a
  process-discovered healthy instance unless a matching serve command and
  healthy endpoint are also found.
- `REQ-INST-021`: `/health` probes used by `ls` and `status` use a conservative
  default timeout and expose a CLI override so normal local servers are not
  routinely missed by transient sub-second startup or scheduling delays.

## Compatibility

The change is additive. Existing commands, HTTP routes, MCP tools, response
schemas, and network manifest root redaction remain unchanged. `ls --json`
receives additive `registered`, `orphan`, `version`, `discovery_source`,
`root_source`, and diagnostic `notes` values. JSON may also include top-level
discovery warnings. `ls` and `status` gain a `--probe-timeout-seconds` option
for local diagnostics. Registry schema version 2 adds only optional
`process_create_time`; readers remain compatible with schema version 1 records.
This hardening intentionally does not add public raw argv/cwd/create-time
fields; PID correction is computed at read time from health, create-time, and
socket ownership evidence.

## Data Safety

The registry is local operator state and may contain absolute source roots. It
must not be exposed through network APIs, committed, or used as a portable source
identity. Process command lines may also contain local roots; local JSON marks
whether a root came from the registry or process arguments. HTTP probing only
reads exact process-derived endpoints or explicit manual diagnostic ports,
ignores non-llmwiki responses, and never exposes local roots through network
APIs. Full roots in local `--json` remain an accepted operator-facing contract
for registry and process-argument evidence. Raw command lines, launcher
arguments, credentials, and provider command strings must not be rendered in
human tables or JSON output.

## References

- GitHub issue: `#26`
- Architecture: `docs/architecture.md`
- Security: `SECURITY.md`
