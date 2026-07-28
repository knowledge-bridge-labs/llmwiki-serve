# ADR: Local Instance Registry Boundary

## Status

Accepted; amended 2026-07-29.

## Context

Operators can run several local `llmwiki-serve serve` processes at once. Before
the local instance discovery work, port-to-root mapping required process-table
inspection plus one or more HTTP probes. Adding the served root to HTTP
discovery would conflict with the existing network redaction posture.

Independent Ubuntu/DGX validation of 0.2.5 showed the normal Linux path already
discovers arbitrary ports, roots with spaces, empty-registry process instances,
listener PIDs, and non-llmwiki health exclusions. The hardening therefore
preserves that Linux behavior and focuses on using the same production-grade
provider path cross-platform, fixing Windows wrapper PID drift, and making
provider permission failures explicit without adding a guessed port scan.

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
That local JSON root exposure remains accepted for registry records and
verified process arguments, while raw command lines are not rendered.

Hard-killed processes can leave stale records. Discovery checks process
liveness, probes `/health` for live records, reports stale status, and supports
operator pruning. Registry schema version 2 adds an optional
`process_create_time` field so readers can distinguish a still-running original
PID from a reused PID. Readers remain compatible with schema version 1 records
that do not contain create-time metadata.

Because older installs did not write this registry, `ls` and `status` also
inspect the local OS process table for actual command lines that identify
`llmwiki-serve serve` processes. For unregistered legacy/orphan processes,
discovery parses `--host`, `--port`, and the root argument when possible, then
ties that parsed endpoint to an actual local listener socket before probing
`/health`. Process-derived probes use the listener-confirmed local probe host
instead of arbitrary argv hostnames or IP addresses. Wildcard listeners are
probed through loopback for their address family: `0.0.0.0` through
`127.0.0.1`, and `::` through `::1`. Only responses that identify the service
as `llmwiki-serve` are reported. Unregistered process results are marked
`registered=false` and `orphan=true`, include source identity and version when
available, and mark whether the root came from process arguments.
If an exact process candidate is found but its local listener cannot be verified
or its listener-confirmed `/health` endpoint times out or cannot be reached,
discovery reports it as an unverified unhealthy orphan instead of dropping it.
A reachable 200 `/health` document identifying a different service is still
excluded.

Process and socket inspection use `psutil` as the primary cross-platform path
instead of shelling out and reparsing command-line strings in the normal case.
Discovery collects exact argv, cwd, and process create time when the OS allows
it, resolves relative roots against cwd, and reads the local TCP listener table
to map verified endpoints back to the PID that owns the listening socket. This
keeps wrapper chains such as `uv run`, console scripts, and parent Python
launchers from causing the displayed PID to drift away from the actual server
process. The listener PID is marked verified only when it is itself a parsed
llmwiki candidate or a descendant of one; otherwise the PID may still be shown
as the socket owner, but a `listener-pid-unverified` note makes the missing
relationship explicit. When the listener process itself is parsed, its argv/cwd
provide the root evidence. When only a parent/wrapper is parsed, the listener
PID may still be used but root evidence is downgraded to `unknown` rather than
inherited from the wrapper. If argv, cwd, create time, parent PID, or socket
ownership cannot be read, discovery emits one aggregated warning per degraded
provider capability and continues without guessed ports. Fallback process
provider failures use sanitized provider/capability wording only; raw
stdout/stderr, shell command text, argv values, process paths, PIDs, and OS
exception text are not rendered in warnings.

This amendment avoids adding raw argv, cwd, command-line, or create-time fields
to public `ls --json` output. PID reuse and wrapper correction are computed at
read time from PID liveness, optional registry create-time, exact `/health`
identity, and socket owner evidence. This keeps existing registry records
compatible and limits local JSON changes to additive `service_verified` and
diagnostic `notes`.

Discovery does not perform a default fixed-port or broad loopback scan. If a
platform process provider is unavailable, `ls` reports degraded discovery and
does not substitute guessed ports. Operators can still pass an explicit
`--probe-port` for manual loopback diagnostics, and registry records keep their
existing local health-check contract. `ls` and `status` expose a local
`--probe-timeout-seconds` override, and the default timeout is conservative
enough to avoid routinely missing healthy local servers during normal startup
or scheduler jitter. Process-derived probes are bounded so many exact but
unreachable candidates do not create sequential `N * timeout` CLI latency;
candidates that cannot be probed inside that budget remain unhealthy and
unverified local diagnostics.

## Consequences

- Operators can identify duplicate same-root and parent/subfolder servers from
  the registry and can also see verified pre-registry or legacy local servers
  running on arbitrary ports when their process command line is available.
- Process-discovered rows report the actual listener PID when socket ownership
  is available, even if multiple wrapper processes advertise the same endpoint.
- Local roots remain outside HTTP and MCP response contracts.
- The registry directory must be treated as local diagnostic state and should
  not be committed or shared without redaction.
- Process discovery depends on platform process-table access. When that access
  fails, discovery is explicitly degraded rather than silently heuristic.

## Follow-Ups

- Align JSON field names with companion bridge/start status commands as those
  implementations settle.
- Monitor macOS process and socket permission behavior and keep degradation
  warnings explicit when the OS denies argv, cwd, or listener ownership.

## References

- Spec: `specs/local-instance-discovery/`
- GitHub issue: `#26`
- Security: `SECURITY.md`
