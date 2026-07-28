# Tasks: Local Instance Discovery

- [x] Read issue `#26`, README, architecture, safety posture, CLI code, and
  related tests.
- [x] Create feature spec files.
- [x] Add local instance registry helpers.
- [x] Register and unregister `serve` processes.
- [x] Add `llmwiki-serve ls` and `llmwiki-serve status` CLI commands.
- [x] Add stale, health, duplicate bundle, same-root, and parent/subfolder
  annotations.
- [x] Add process-table orphan/legacy server discovery with opt-out and explicit
  manual probe controls.
- [x] Add focused tests.
- [x] Update README and architecture docs.
- [x] Update ADR and changelog for registry-plus-process discovery behavior.
- [x] Run validation.
- [x] Amend spec/ADR/tests for robust process/listener discovery behavior.
- [x] Add a vetted OSS process provider for exact argv, cwd, and listening TCP
  socket ownership.
- [x] Deduplicate launcher/wrapper process candidates by endpoint and prefer
  the actual listener PID in reported records.
- [x] Resolve relative process roots against cwd when available.
- [x] Add a configurable, conservative `ls/status` health probe timeout.
- [x] Add focused tests for Windows quoting, Linux argv, console script,
  `python -m`, `uv run`, wrapper dedupe, arbitrary ports, spaced paths,
  non-llmwiki health, PID reuse, alternate state dirs, and degraded providers.
- [x] Keep exact process candidates visible as unhealthy unverified rows when
  `/health` times out or cannot connect, while excluding non-llmwiki 200 health.
- [x] Add backward-compatible registry process create-time writes and PID reuse
  comparisons.
- [x] Make Windows CI release smoke avoid shell glob expansion.
- [x] Update third-party notices for `psutil` and `types-psutil`.
- [x] Sanitize fallback provider and procfs degraded warnings, strengthen
  third-party notice matching, and make repo-only workflow tests sdist-safe.
- [x] Restrict process-derived health probes to verified local listener
  endpoints, preserve manual probe and registry contracts, fix IPv6 wildcard
  probing, downgrade wrapper-only listener roots, and bound process probe
  latency.
- [x] Run Windows focused tests, full suite, and a real arbitrary-port server
  discovery smoke.

## LLMWiki Ingestion Candidates

- `specs/local-instance-discovery/`
- `README.md`
- `docs/architecture.md`
- `docs/decisions/2026-07-28-local-instance-registry-boundary.md`
- `CHANGELOG.md`
