# ADR: Local Instance Registry Boundary

## Status

Accepted.

## Context

Operators can run several local `llmwiki-serve serve` processes at once. Before
the local instance discovery work, port-to-root mapping required process-table
inspection plus one or more HTTP probes. Adding the served root to HTTP
discovery would conflict with the existing network redaction posture.

## Decision

Use a per-user local registry as the primary process discovery source. `serve`
writes an ephemeral instance record containing PID, host, port, URL, root,
source id, bundle id, adapter, page counts, and start time after projection
preflight succeeds and before starting Uvicorn. It removes that record on
graceful shutdown.

The registry is local operator state, not a public API and not an authoritative
source catalog. Network `/manifest` and `/health` continue to omit absolute
local roots. The local `llmwiki-serve ls` command may include full root paths in
local `--json` output for registered records because it is explicitly
operator-facing; the default human table redacts roots to a short tail label.

Hard-killed processes can leave stale records. Discovery checks process
liveness, probes `/health` for live records, reports stale status, and supports
operator pruning.

Because older installs did not write this registry, `ls` and `status` also
inspect the local OS process table for actual command lines that identify
`llmwiki-serve serve` processes. For unregistered legacy/orphan processes,
discovery parses `--host`, `--port`, and the root argument when possible, then
probes exactly that host and port at `/health`. Only responses that identify the
service as `llmwiki-serve` are reported. Unregistered process results are marked
`registered=false` and `orphan=true`, include source identity and version when
available, and mark whether the root came from process arguments.

Discovery does not perform a default fixed-port or broad loopback scan. If a
platform process provider is unavailable, `ls` reports degraded discovery and
does not substitute guessed ports. Operators can still pass an explicit
`--probe-port` for manual loopback diagnostics.

## Consequences

- Operators can identify duplicate same-root and parent/subfolder servers from
  the registry and can also see verified pre-registry or legacy local servers
  running on arbitrary ports when their process command line is available.
- Local roots remain outside HTTP and MCP response contracts.
- The registry directory must be treated as local diagnostic state and should
  not be committed or shared without redaction.
- Process discovery depends on platform process-table access. When that access
  fails, discovery is explicitly degraded rather than silently heuristic.

## Follow-Ups

- Align JSON field names with companion bridge/start status commands as those
  implementations settle.
- Consider a tiny process-inspection dependency only if stdlib/subprocess
  providers are insufficient on supported platforms.

## References

- Spec: `specs/local-instance-discovery/`
- GitHub issue: `#26`
- Security: `SECURITY.md`
