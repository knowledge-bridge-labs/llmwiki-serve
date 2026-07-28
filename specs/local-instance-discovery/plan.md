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

## Affected Areas

- Source modules: `src/llmwiki_serve/cli.py`, new
  `src/llmwiki_serve/instances.py`
- Tests: focused CLI/registry tests in a new test module
- Docs: `README.md`, `docs/architecture.md`, and this spec

## Risks

- Risk: a hard-killed process leaves stale records.
  Mitigation: `ls` marks non-running PIDs as stale and `--prune-stale` removes
  them.

- Risk: local roots leak through a network API.
  Mitigation: no HTTP endpoint is added; full roots appear only in local CLI
  output and local registry files.

- Risk: probing wildcard hosts such as `0.0.0.0` is unreliable.
  Mitigation: the probe uses localhost for wildcard bind addresses while keeping
  the operator-facing URL as configured.

## Rollout

- Add registry helpers and CLI commands.
- Register/unregister around `serve`.
- Add unit tests for JSON/table output, stale handling, pruning, duplicate
  bundle hints, and parent/subfolder root hints.
- Run full local validation.
