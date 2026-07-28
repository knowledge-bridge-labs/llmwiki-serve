# ADR: Local Instance Registry Boundary

## Status

Accepted.

## Context

Operators can run several local `llmwiki-serve serve` processes at once. Before
the local instance discovery work, port-to-root mapping required process-table
inspection plus one or more HTTP probes. Adding the served root to HTTP
discovery would conflict with the existing network redaction posture.

## Decision

Use a per-user local registry for process discovery. `serve` writes an ephemeral
instance record containing PID, host, port, URL, root, source id, bundle id,
adapter, page counts, and start time after projection preflight succeeds and
before starting Uvicorn. It removes that record on graceful shutdown.

The registry is local operator state, not a public API and not an authoritative
source catalog. Network `/manifest` and `/health` continue to omit absolute
local roots. The local `llmwiki-serve ls` command may show full root paths
because it is explicitly operator-facing.

Hard-killed processes can leave stale records. Discovery checks process
liveness, probes `/health` for live records, reports stale status, and supports
operator pruning.

## Consequences

- Operators can identify duplicate same-root and parent/subfolder servers
  without shelling out to platform-specific process tools.
- Local roots remain outside HTTP and MCP response contracts.
- The registry directory must be treated as local diagnostic state and should
  not be committed or shared without redaction.
- Servers started by older versions will not appear unless they are manually
  registered or restarted with this version.

## Follow-Ups

- Align JSON field names with companion bridge/start status commands as those
  implementations settle.
- Consider optional bounded port-range probing later if support for pre-registry
  instances becomes important.

## References

- Spec: `specs/local-instance-discovery/`
- GitHub issue: `#26`
- Security: `SECURITY.md`
