# Tasks: Managed Generic Markdown Context

- [x] Confirm `AGENTS.md` is absent in the working tree and follow provided
  session instructions instead.
- [x] Read README, architecture, existing ADR style, relevant specs, and
  freshness/Redis/relevance docs before drafting.
- [x] Create managed generic Markdown context spec package.
- [ ] Review the spec package with maintainers before implementation.
- [x] Confirm the exact adapter identity used for plain generic Markdown in the
  current code before naming CLI/API configuration.
- [x] Add disabled-by-default configuration and early validation.
- [x] Add adapter gating so only `generic-markdown` can activate managed
  context.
- [x] Add no-op tests for `llmwiki-markdown`, native LLMWiki folders, and
  authored `hot.md`, `index.md`, `overview.md`, and `quickstart.md` pages.
- [x] Add a source mutation guard that hashes or snapshots the served tree
  before and after managed context requests.
- [x] Add the local per-user sidecar backend with atomic replace semantics.
- [x] Add opaque page-key derivation with per-user salt or namespace-secret
  input.
- [x] Add serialization tests proving sidecar records omit raw queries, raw
  page ids, paths, endpoint labels, credentials, request bodies, and source
  snippets.
- [x] Add projection/source signature scoping and invalidation for managed
  orientation and page-hit prior state.
- [x] Add derived orientation candidate selection for approved
  `generic-markdown` pages without creating synthetic source pages.
- [x] Add query-relevance abstention so unrelated or negative non-empty context
  queries receive no managed orientation.
- [x] Add compact default managed-orientation snippets and a query-scoped
  orientation limit.
- [x] Avoid managed hit-prior updates for context requests where managed
  orientation abstains as unrelated.
- [x] Add bounded decayed page-hit counters for returned/read pages.
- [x] Add ranking composition so managed context applies only inside the lexical
  tie band.
- [x] Add regression tests proving managed boosts cannot invert materially
  stronger lexical relevance.
- [x] Add concurrent local writer coverage for advisory lock or optimistic
  generation behavior.
- [x] Add corrupt, partial, schema-mismatched, and signature-mismatched sidecar
  record fallback tests.
- [ ] Update README and architecture after implementation is stable, keeping the
  public contract impact and diagnostics deferral explicit.
- [ ] Add managed Redis keyspace design tests before implementing Redis writes.
- [ ] Implement managed Redis backend only after local sidecar behavior passes
  privacy, invalidation, and ranking tests.
- [ ] Add future redacted diagnostics only if a concrete operator workflow
  requires them.
- [ ] Ingest this spec package into the project LLMWiki after maintainer review
  if projection validation is requested.

## Initial Implementation Scope

Keep the first implementation intentionally narrow:

- disabled-by-default config
- adapter no-op gating
- local sidecar state model
- source mutation guard
- privacy serialization checks
- derived orientation candidates for `generic-markdown`
- bounded page-hit tie-break within lexical tie band

Defer these items:

- managed Redis backend
- public diagnostics
- embeddings/vector search
- cross-source routing
- cleanup command
- public benchmark claims

## Validation

Docs-only validation for this draft:

```bash
git diff --check -- specs/managed-generic-markdown-context
git status --short
```

Expected implementation validation:

```bash
uv run pytest -q tests/test_service.py -k "managed_context or generic_markdown or search"
uv run pytest -q tests/test_freshness_loop_matrix.py
uv run python scripts/export_openapi.py --check
uv run python scripts/release_smoke.py
```

Future Redis validation, after managed Redis exists:

```bash
uv run pytest -q tests/test_service.py -k "managed_context and redis"
```

## Follow-Up Work

- Choose final CLI/environment option names after maintainers review the config
  shape.
- Decide whether hit events should be recorded for explicit reads only, context
  inclusions only, or both.
- Tune default half-life, tie band, and boost cap with a public-safe benchmark
  corpus.
- Decide whether a future diagnostics endpoint should remain local-only or use
  redacted network diagnostics.
