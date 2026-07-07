# Contributing

Thanks for helping improve `llmwiki-serve`.

## Development Setup

```bash
uv sync --extra dev
```

## Checks

Run the same checks before submitting changes:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest
uv build
uv run python scripts/release_smoke.py --wheel dist/*.whl --sdist dist/*.tar.gz
```

The release smoke installs the built wheel into a clean temporary environment
offline by default. If a local machine has not cached all runtime dependency
wheels, either warm the cache with `uv sync --extra dev --locked` or rerun the
same smoke with `--allow-network-install` and call that out in the PR.
GitHub CI uses `--allow-network-install` because hosted runners start with an
empty uv cache.

## Pull Requests

- Keep changes focused and avoid unrelated formatting churn.
- Add or update tests for behavior changes.
- Update `CHANGELOG.md` for notable user-facing, compatibility, security, or
  project-maintenance changes.
- Do not commit secrets, credentials, token caches, private exports, or raw
  sensitive data.
- Document user-facing behavior changes in `README.md` when appropriate.
- Use the pull request template to call out usage mode, HTTP/MCP/A2A endpoint
  impact, validation, and data-handling considerations.

For substantial or ambiguous changes, open an issue or discussion first and get
agreement on the direction before investing in a large pull request. Examples
include new adapter contracts, new MCP or A2A behavior, authentication changes,
write-path proposals, release automation, or any change that expands the
supported serving model.

Maintainers may close low-effort, unverified, or mostly generated issues and PRs
when they do not include a clear problem statement, implementation rationale, and
reproducible validation. AI-assisted contributions are welcome, but contributors
remain responsible for understanding, testing, and maintaining the change.

## Review Expectations

Review feedback should be actionable for contributors:

- Mark blocking issues clearly and explain the expected behavior or invariant.
- Prefer file- or line-specific comments when a concrete code change is needed.
- Distinguish optional design suggestions from merge-blocking defects.
- Ask for specific validation when behavior, adapter compatibility, package
  contents, or network surfaces change.
- Keep generated or AI-assisted changes reviewable by describing the intended
  behavior and the tests that prove it.

Pull requests receive a generated review-guide comment based on changed paths.
That comment is only a contributor aid; maintainer approval and CI remain the
merge signal.

## Issues

- Use the bug template for reproducible defects.
- Use the feature template for focused server, adapter, endpoint, or operations
  improvements.
- Use the adapter/protocol compatibility template for source-folder detection,
  graph projection, HTTP, MCP, or A2A contract concerns.
- Use the documentation template for missing or confusing docs.
- Check `SUPPORT.md` for support routing expectations.
- Report suspected vulnerabilities privately through the process in
  `SECURITY.md`.

## Project Scope

`llmwiki-serve` is a read-only serving layer for LLMWiki-style Markdown folders.
Changes should preserve the Markdown folder as the source of truth and avoid
adding write paths unless the project scope changes explicitly.

## Releases

Before publishing a release or release candidate, follow `docs/release.md`.
