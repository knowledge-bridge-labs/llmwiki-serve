## Summary

<!-- What changed, and why? Keep this focused on server behavior,
compatibility, security, documentation, or project maintenance impact. -->

## Prior Discussion

- [ ] This is a small, self-contained fix or documentation change.
- [ ] I linked the issue or discussion where direction was agreed for a
      substantial or ambiguous change.
- [ ] Not applicable.

## Usage Mode Impact

<!-- Check all that apply and explain compatibility impact. -->

- [ ] CLI commands (`manifest`, `query`, `serve`)
- [ ] Long-running HTTP server
- [ ] Library/imported server behavior
- [ ] LLMWiki folder serving or adapter behavior
- [ ] No user-facing usage mode impact

## Endpoint Impact

- [ ] HTTP endpoints (`/health`, `/manifest`, `/source-bundle`, `/source-refs`, `/query`, `/search`, `/read/{page_id}`, `/graph`, `/graph/neighborhood`)
- [ ] MCP JSON-RPC endpoint (`/mcp`)
- [ ] MCP Streamable HTTP endpoint (`/mcp/stream`)
- [ ] A2A agent-card or message endpoint (`/.well-known/agent-card.json`, `/message:send`)
- [ ] CLI-only behavior
- [ ] Documentation-only change
- [ ] CI, security, or repository maintenance

## Type

- [ ] Feature
- [ ] Bug fix
- [ ] Documentation
- [ ] Refactor
- [ ] Test
- [ ] CI or security maintenance

## Validation

- [ ] `uv run ruff format --check .`
- [ ] `uv run ruff check .`
- [ ] `uv run mypy src`
- [ ] `uv run pytest`
- [ ] `uv build`
- [ ] `uv run python scripts/release_smoke.py --wheel dist/*.whl --sdist dist/*.tar.gz`

<!-- For documentation-only changes, mark skipped checks as not run and explain why. -->

## Documentation and Release Notes

- [ ] I updated README, CONTRIBUTING, CHANGELOG, or docs when behavior, setup,
      compatibility, validation, or release expectations changed.
- [ ] I documented protocol impact for HTTP, MCP, or A2A changes.
- [ ] I documented adapter or source-folder compatibility impact when LLMWiki,
      Obsidian, Logseq, Foam, Dendron, Quartz, or generic Markdown serving changed.
- [ ] Not applicable.

## Security and Data Handling

- [ ] I did not include credentials, tokens, private endpoint URLs or exports,
      local environment files, raw sensitive logs, or private wiki content.
- [ ] I preserved the read-only source-folder model unless the scope change is
      explicit and reviewed.
- [ ] I considered whether served Markdown, paths, logs, traces, or graph output
      could expose sensitive data.
- [ ] I followed `SECURITY.md` for any suspected vulnerability.

## Notes for Reviewers

Review focus:

Known risks or compatibility concerns:

Skipped validation or follow-up work:

Generated or AI-assisted areas needing closer review:
