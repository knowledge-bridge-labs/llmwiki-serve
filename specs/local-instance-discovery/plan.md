# Plan: Local Instance Discovery

## Approach

Add a small `instances` module that owns registry path resolution, record
serialization, process-liveness checks, `/health` probing, stale pruning, and
duplicate/overlap annotation. Wire `serve` to register in a `try/finally` around
`uvicorn.run(...)`. Add `ls` and `status` CLI commands that render the same
discovery result as a table or JSON.

Keep the registry outside the served root by default and behind an environment
override for tests and operators. Treat registry contents as best-effort local
diagnostics.

Extend discovery with a second provider that inspects the OS process table for
actual local command lines that identify `llmwiki-serve serve`. Parse `--host`,
`--port`, and the root argument when possible, then probe exactly that endpoint's
`/health` document. Results without a registry record are marked as
orphan/unregistered and record whether their root came from process arguments.
Operators can disable this provider with `--no-processes`. Explicit
`--probe-port` remains available for manual loopback diagnostics, but default
discovery does not scan guessed ports.

## Affected Areas

- Source modules: `src/llmwiki_serve/cli.py`, new
  `src/llmwiki_serve/instances.py`
- Tests: focused CLI/registry tests in a new test module
- Docs: `README.md`, `docs/architecture.md`, `CHANGELOG.md`, ADR, and this
  spec

## Risks

- Risk: a hard-killed process leaves stale records.
  Mitigation: `ls` marks non-running PIDs as stale and `--prune-stale` removes
  them.

- Risk: local roots leak through a network API or public docs.
  Mitigation: no HTTP endpoint is added; full roots appear only in local CLI
  output and local registry/process metadata. Public docs use placeholders.

- Risk: probing wildcard hosts such as `0.0.0.0` is unreliable.
  Mitigation: the probe uses localhost for wildcard bind addresses while keeping
  the operator-facing URL as configured.

- Risk: platform process inspection is unavailable.
  Mitigation: `ls` reports degraded discovery and does not substitute a guessed
  port scan.

- Risk: probing a non-llmwiki service leaks unrelated local service details.
  Mitigation: only endpoints parsed from matching `llmwiki-serve serve` command
  lines or explicit manual diagnostic ports are probed; non-matching `/health`
  responses are ignored and response bodies are not rendered.

## Rollout

- Add registry helpers and CLI commands.
- Register/unregister around `serve`.
- Add unit tests for JSON/table output, stale handling, pruning, duplicate
  bundle hints, and parent/subfolder root hints.
- Add process-discovery tests for Windows/POSIX command-line parsing, orphan
  discovery, arbitrary parsed ports, dedupe, non-matching processes, and failed
  or non-llmwiki health.
- Run full local validation.
