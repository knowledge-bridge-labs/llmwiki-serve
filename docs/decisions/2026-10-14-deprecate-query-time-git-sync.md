# ADR: Deprecate Query-Time Git Sync

## Status

Accepted. `--git-sync` is deprecated in 0.2.16 and is scheduled for removal in
a later release.

## Context

0.2.15 added opt-in query-time git sync (`--git-sync`, `LLMWIKI_GIT_SYNC`). When
enabled, a request runs `git fetch` and then `git merge --ff-only` in the git
work tree that contains the served root, so the served files change underneath
the running service.

That feature shipped without a spec or ADR, and it conflicts with the
architecture's Read-Only Guarantee:

- "`llmwiki-serve` treats the source folder as immutable input."
- "No source files are rewritten, normalized, migrated, or annotated."

Fast-forwarding the checkout is a write to the served source, even though it
never resets or overwrites local changes. It also makes the server responsible
for git credentials, network fetches, and repository state, which belongs to
whoever maintains the checkout.

The freshness need behind the feature is already covered by the read path: a
long-running service compares source metadata and content digests on each
request (or after `--refresh-interval-seconds`) and rebuilds the projection
when Markdown files change. Any process that updates the checkout from outside
the server therefore gets the same "new commits are visible on the next
request" behavior. The producer-manifest freshness boundary
(`2026-07-17-producer-manifest-freshness-boundary.md`) keeps the same split:
producers change the source, the server observes it.

## Decision

- Mark `--git-sync` and `LLMWIKI_GIT_SYNC` as deprecated in 0.2.16. Behavior is
  unchanged so existing deployments keep working.
- `serve --git-sync` prints a deprecation warning to stderr at startup, and
  `LlmWikiService(git_sync=GitSyncConfig(enabled=True))` emits a
  `DeprecationWarning`. Disabled or omitted git sync emits nothing.
- Document the replacement: keep the serving clone current from outside the
  server, for example a scheduled `git -C <clone> pull --ff-only`, and rely on
  the existing per-request freshness check.
- Remove the option, its status surfaces (`/diagnostics/git-sync`, the
  `git_sync` health field, and the `io.llmwiki/gitSync` MCP `_meta`), and the
  `git_sync` module in a later minor release after at least one release with
  the warning.

## Consequences

The Read-Only Guarantee holds again for every supported configuration once the
option is removed; until then the deprecation is visible to operators.

Operators lose the bounded "wait up to N seconds for a fetch" behavior. An
external schedule adds up to one interval of staleness. For a 60-second
schedule this matches the default `--git-sync-cooldown-seconds`.

## Alternatives Considered

- Keep git sync as an explicit exception to the guarantee. Rejected: the
  guarantee is the project's core contract, and the same freshness is
  available without the server writing to the source.
- Fetch into a server-owned copy outside the served root. Rejected for now: it
  would add a second managed checkout and its lifecycle to the server, which
  the derived-artifact ADR reserves for an explicit, separately specified
  lifecycle.

## References

- Architecture: `../architecture.md#read-only-guarantee`
- Producer freshness ADR: `2026-07-17-producer-manifest-freshness-boundary.md`
- 0.2.15 change: PR #58
