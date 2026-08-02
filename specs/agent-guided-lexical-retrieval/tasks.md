# Tasks: Agent-Guided Lexical Retrieval

- [x] Create this spec package.
- [x] Record the ADR for agent-guided lexical as the default agent workflow,
  not a new `SearchMode`.
- [x] Confirm current public `SearchMode` is
  `lexical|literal|vector|hybrid`.
- [x] Record the zero-write projection-extractive sketch boundary.
- [x] Record lexical-only caller-supplied variant fusion requirements.
- [x] Simplify the V1 canonical guidance schema to the closed Serve
  snake_case object in `spec.md`.
- [x] Align bridge camelCase mapping expectations to Serve as the canonical
  schema source.

## Implementation Tasks

- [x] Advertise exact capability `llmwiki_agent_guided_lexical_v1` through
  health, manifest, source-bundle, MCP JSON-RPC metadata, and MCP Streamable
  HTTP metadata/tool discovery.
- [x] Add `RetrievalGuidance`, `FolderCard`, and `PageCard` models matching the
  closed schema in `spec.md`.
- [x] Add optional `retrieval_guidance` to `ContextPack` and emit it by default
  for new-capability context responses.
- [x] Ensure every new serve wire/model field is snake_case and no camelCase
  aliases are emitted.
- [x] Enforce exact top-level fields:
  `schema_version`, `orientation_source`, `content_trust`,
  `max_query_variants`, `character_budget`, `folder_cards`, `page_cards`,
  `suggested_terms`, `exact_identifiers`, and `fallback_modes`.
- [x] Enforce exact folder-card fields: `path`, `page_count`, and `terms`.
- [x] Enforce exact page-card fields: `page_id`, `title`, `path`, `headings`,
  `terms`, `exact_identifiers`, and `excerpt`.
- [x] Enforce no `null` values, no unknown fields, no projection digest, no
  diagnostics, no selection object, no next-call object, no arbitrary
  frontmatter, and no raw snippets beyond bounded `excerpt`.
- [x] Enforce caps: `max_query_variants == 2`, `character_budget <= 6000`,
  `folder_cards <= 8`, `page_cards <= 12`, `folder_cards[].terms <= 8`,
  `page_cards[].headings <= 8`, `page_cards[].terms <= 12`,
  `page_cards[].exact_identifiers <= 8`, `suggested_terms <= 16`,
  `exact_identifiers <= 16`, and `excerpt <= 240` characters.
- [x] Emit `fallback_modes` as an ordered unique array drawn from available
  `literal`, `hybrid`, and `vector` capabilities only, with vector/hybrid
  requiring verified in-process provider availability rather than config
  enablement alone.
- [ ] Add a bridge interop fixture or contract export proving Serve snake_case
  guidance maps to bridge camelCase without Serve adding camelCase aliases.
- [x] Add optional `query_variants` to Python service context/search methods.
- [x] Add optional `query_variants` to HTTP `/query` and `/search` request
  models.
- [x] Add optional `query_variants` to MCP JSON-RPC tool argument parsing.
- [x] Add optional `query_variants` to MCP Streamable HTTP tool schemas.
- [x] Defer CLI `query/search --query-variant` flags and keep CLI query/search
  as single-primary-query surfaces in V1.
- [x] Add shared authored-orientation eligibility helper aligned with parser
  page roles.
- [x] Ensure root or hub-root `quickstart.md` qualifies only through parser
  role `index`, while nested ordinary quickstart pages with role `topic` are
  not authored orientation by name.
- [x] Add service-layer guidance assembly for authored orientation.
- [x] Add service-layer transient projection-extractive sketching for eligible
  `generic-markdown` sources.
- [x] Ensure generic sketches use only allowed immutable projection data and
  emit only the V1 card/term/identifier fields.
- [x] Ensure guidance applies draft/visibility filtering and character/item
  caps before card selection.
- [x] Ensure guidance marks all content with
  `content_trust: "untrusted_source_evidence"`.
- [x] Add Unicode-safe query variant trimming and NFC plus casefold
  deduplication.
- [x] Preserve empty-query overview behavior when variants are omitted or
  empty, and require a non-empty primary `query` only when a variant is
  supplied.
- [x] Reject empty variant strings after trimming.
- [x] Preserve first original spelling while deduplicating by NFC plus
  casefold.
- [x] Reject non-empty `query_variants` for `literal`, `vector`, and `hybrid`
  before fallback, provider setup, or search fan-out.
- [x] Implement deterministic lexical RRF fusion for variant channels.
- [x] Preserve no-variant lexical byte/order compatibility.
- [x] Preserve exact identifier, source-ref, path, and version safeguards.
- [x] Apply `exclude_page_ids`, `include_drafts`, `fields`, and
  `snippet_chars` consistently across variant channels.
- [x] Update generated OpenAPI.
- [ ] Update MCP tool descriptions and schema examples.
- [x] Update README and architecture direct-agent guidance.
- [x] Keep vector/hybrid documented as explicit optional escalation.

## Test Tasks

- [x] Add capability metadata tests for exact
  `llmwiki_agent_guided_lexical_v1` advertisement across all capability
  surfaces.
- [x] Add model serialization tests for the exact closed
  `retrieval_guidance` schema.
- [ ] Add schema-closure tests rejecting unknown fields, `null`, over-budget
  arrays, over-budget strings, and forbidden V1 fields.
- [ ] Add bridge mapping golden fixtures for every Serve snake_case field and
  expected bridge camelCase field.
- [x] Add context tests proving guidance is emitted by default when the
  capability is present.
- [x] Add authored orientation guidance tests.
- [x] Add generic Markdown projection-extractive guidance tests.
- [x] Add role-based authored orientation eligibility tests.
- [ ] Add tests proving native LLMWiki and `llmwiki-markdown` authored behavior
  is unchanged.
- [ ] Add source mutation tests proving guidance and variants write nothing
  under the served root.
- [ ] Add privacy tests for local roots, private endpoints, credentials, raw
  query history, arbitrary frontmatter, and draft content.
- [x] Add no-variant `/query` and `/search` lexical compatibility tests.
- [x] Add query variant fusion ordering tests.
- [x] Add rejection tests for more than two supplied variants, empty variant
  strings, missing/empty primary query when variants are supplied, and variants
  with non-lexical modes.
- [x] Add Unicode tests for Korean, mixed-script, dotted, snake_case,
  kebab-case, camelCase, path-like, version, and source-ref identifiers.
- [x] Add exact identifier guard tests under variant fusion.
- [x] Add HTTP `/query` and `/search`, MCP JSON-RPC, MCP Streamable HTTP, and
  Python service contract tests.
- [ ] Add CLI tests or help snapshots proving query-variant flags are not
  exposed in V1.
- [ ] Add OpenAPI export/check coverage.
- [ ] Add prompt-injection tests for authored orientation and sketch excerpts.
- [x] Add regression tests proving configured but unprobed vector services
  keep guidance literal-only without initializing provider/cache, valid
  injected or already-probed providers add vector/hybrid fallbacks, and
  provider initialization failures keep manifest capabilities and guidance
  aligned.

## Benchmark Tasks

- [x] Define the tiny bundled fixture as an engineering harness only, not
  release or public evidence.
- [x] Use external plan JSONL and make zero LLM calls in the runner.
- [x] Require explicit versioned plan provenance covering generator kind/model
  or human fixture identity, prompt/template revision and SHA-256,
  source-context/input digests, timestamp or stable-fixture marker, and token
  accounting source.
- [x] Reject forbidden gold/citation/qrel fields in plan rows.
- [x] Reject original query, primary query, and variant strings that exactly
  match positive qrel identifiers, paths, or page ids after NFC, casefold, and
  path/id normalization.
- [x] State that semantic leakage cannot be mechanically proven absent and
  release evidence requires independently generated source-only plans before
  qrels are available.
- [x] Split authored and projection corpora. Keep root `hot.md`/`index.md`
  only in the authored corpus and reject `hot.md`, `index.md`, or
  `overview.md` in the projection corpus.
- [x] Assert `retrieval_guidance.orientation_source=authored` for authored
  arms and `projection_extractive` for projection arms.
- [x] Pair raw lexical baselines to both authored and projection corpora.
- [x] Gate hybrid reference arms on an explicitly supplied provider-backed
  service.
- [x] Verify service-instance isolation by retaining service objects and
  rejecting singleton/reused factories across orientation/search/arm/query
  requests.
- [x] Report cold cache state as `null`/unknown unless provider evidence exists.
- [x] Treat skipped hybrid arms as not measured, with `null` metrics,
  `null` distributions, empty per-query arrays, zero/not-applicable usage, and
  an explicit limitation.
- [x] Report `public_search_requests` separately from
  `internal_lexical_channel_evaluations`.
- [x] Validate qrel/query consistency, including unknown qrel rejection,
  positive qrels for answerable queries, unknown-answerability rejection, and
  no qrels for unanswerable queries.
- [x] Reject duplicate qrel rows and require exact qrel row keys.
- [x] Use canonical projected/service page ids for fixture identities and
  reject duplicate canonical ids.
- [x] Report total, evaluated, negative, total-qrel, and positive-qrel counts.
- [x] Add strict report JSON schema and runtime validation with exact
  arms/status/metrics/usage/per-query/provenance shapes and closed additional
  properties.
- [x] Add provenance auto-detection for implementation revision/dirty state
  with unknown fallback and source/plan/query/qrel digests.
- [ ] Include SciFact, Korean, real LLMWiki/OpenWiki-style, generic Markdown,
  Obsidian-like, and code identifier fixtures before public claims.
- [x] Report query count, call count, variant count, character/token counting
  method, latency, quality metrics, citation precision, false-positive
  diagnostics, platform, commit SHA, and dirty-state flag.

## Engineering Evidence

- [x] Record sanitized Windows dirty-snapshot engineering validation:
  full suite `640` passed / `7` skipped, plus focused agent-guided lexical
  checks `22` passed. This is not clean-commit public release evidence.
- [x] Record sanitized DGX Spark Ubuntu dirty-snapshot engineering validation:
  full suite `645` passed / `2` skipped, deterministic checks `29/29`, and one
  real Qwen tool-call trial. This is non-public engineering evidence and not
  DeepAgents ACP validation.
- [ ] Rerun public-safe benchmark and release evidence from a clean commit.
- [ ] Complete actual DeepAgents ACP validation if a future Serve contract
  requires it.

## Validation Commands

Docs-only validation for this slice:

```powershell
git diff --check -- specs/agent-guided-lexical-retrieval/spec.md specs/agent-guided-lexical-retrieval/tasks.md specs/agent-guided-lexical-retrieval/tests.md specs/agent-guided-lexical-retrieval/plan.md docs/decisions/2026-08-02-agent-guided-lexical-default-agent-workflow.md
Select-String -Path specs/agent-guided-lexical-retrieval/spec.md,specs/agent-guided-lexical-retrieval/tasks.md,specs/agent-guided-lexical-retrieval/tests.md,specs/agent-guided-lexical-retrieval/plan.md,docs/decisions/2026-08-02-agent-guided-lexical-default-agent-workflow.md -Pattern '[ \t]+$'
```

Expected implementation validation after code exists:

```powershell
uv run pytest -q tests/test_agent_guided_lexical_runner.py
uv run python scripts/benchmark_adapters/agent_guided_lexical_runner.py --fixture-dir benchmarks/agent_guided_lexical/fixture --output-report .runtime-logs/agent-guided-lexical-smoke.json
uv run pytest -q tests/test_service.py tests/test_public_api.py tests/test_search_postings.py
uv run python scripts/export_openapi.py --check
uv run ruff format --check scripts/benchmark_adapters/agent_guided_lexical_runner.py tests/test_agent_guided_lexical_runner.py
uv run ruff check scripts/benchmark_adapters/agent_guided_lexical_runner.py tests/test_agent_guided_lexical_runner.py
uv run mypy
```

## Deferred

- Persisted derived orientation index build/load/require behavior.
- Managed Redis hot/history integration for guidance.
- LLM-generated server-side query variants.
- CLI `query/search --query-variant` flags.
- Raising the three-channel cap before latency evidence exists.
- Public quality or performance claims.
