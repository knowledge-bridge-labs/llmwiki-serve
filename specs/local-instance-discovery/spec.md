# Spec: Local Instance Discovery

## Status

Draft.

## Problem

Operators can start multiple `llmwiki-serve serve` processes, but the CLI has no
local command that answers which roots are being served on which ports. Duplicate
parent/subfolder servers currently require process inspection plus HTTP probes.

## Goals

- Add a local operator-facing `llmwiki-serve ls` command with `--json`.
- Register long-running `serve` processes in a per-user local state directory.
- Report PID, URL, host, port, root, adapter, source id, bundle id, page counts,
  start time, health, stale status, and duplicate/overlap hints.
- Avoid exposing absolute local paths through HTTP responses by default.
- Keep stale registry handling simple and safe after hard kills.

## Non-Goals

- Do not add daemon management, process killing, or automatic port selection.
- Do not make the registry authoritative source state.
- Do not add remote HTTP instance metadata that exposes local roots.
- Do not require platform-specific process listing tools.

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

## Compatibility

The change is additive. Existing commands, HTTP routes, MCP tools, response
schemas, and network manifest root redaction remain unchanged.

## Data Safety

The registry is local operator state and may contain absolute source roots. It
must not be exposed through network APIs, committed, or used as a portable source
identity. HTTP probing only reads the existing redacted `/health` document.

## References

- GitHub issue: `#26`
- Architecture: `docs/architecture.md`
- Security: `SECURITY.md`
