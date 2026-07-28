# Spec: Local Instance Discovery

## Status

Draft.

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
- Do not require a third-party process inspection dependency unless stdlib and
  subprocess-backed providers prove insufficient.
- Do not perform default fixed-port, broad, or arbitrary port scans.

## Requirements

- `REQ-INST-001`: `serve` writes one local instance record after projection
  preflight succeeds and before `uvicorn.run(...)` starts.
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
- `REQ-INST-011`: unregistered process candidates are included only after their
  parsed host and port return a healthy `/health` JSON document with
  `service: llmwiki-serve`.
- `REQ-INST-012`: process-discovered results include `registered=false`,
  `orphan=true`, source identity, version when available, PID, port, URL, health
  status, `discovery_source=process`, and `root_source=process-args` or
  `unknown`.
- `REQ-INST-013`: non-matching process command lines, failed health probes, and
  non-llmwiki `/health` responses are ignored without surfacing response bodies.
- `REQ-INST-014`: operators can disable process discovery with
  `--no-processes`. They can pass explicit `--probe-port` values for manual
  loopback diagnostics, but default discovery must not depend on guessed ports
  or a scan-port environment variable.
- `REQ-INST-015`: if a platform process provider is unavailable, CLI output
  reports degraded discovery and does not fall back to fixed-port scanning.

## Compatibility

The change is additive. Existing commands, HTTP routes, MCP tools, response
schemas, and network manifest root redaction remain unchanged. `ls --json`
receives additive `registered`, `orphan`, `version`, `discovery_source`, and
`root_source` fields. JSON may also include top-level discovery warnings.

## Data Safety

The registry is local operator state and may contain absolute source roots. It
must not be exposed through network APIs, committed, or used as a portable source
identity. Process command lines may also contain local roots; local JSON marks
whether a root came from the registry or process arguments. HTTP probing only
reads exact process-derived endpoints or explicit manual diagnostic ports,
ignores non-llmwiki responses, and never exposes local roots through network
APIs.

## References

- GitHub issue: `#26`
- Architecture: `docs/architecture.md`
- Security: `SECURITY.md`
