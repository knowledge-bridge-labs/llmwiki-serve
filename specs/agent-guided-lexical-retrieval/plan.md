# Plan: Agent-Guided Lexical Retrieval

## Approach

Implement this as an additive agent workflow over the existing service. The
server returns a compact, closed retrieval-guidance object that a capable
coding agent can inspect before searching. The server then accepts a small
caller-supplied lexical variant set and deterministically fuses ordinary
lexical results. Semantic vector/hybrid remains explicit escalation.

Servers that implement the workflow advertise the exact capability
`llmwiki_agent_guided_lexical_v1`.

Keep the first implementation narrow:

- add `RetrievalGuidance`, `FolderCard`, and `PageCard` models matching the
  exact V1 schema in `spec.md`;
- add optional `retrieval_guidance` to `ContextPack`, emitted by default for
  new-capability context responses;
- report `hybrid` and `vector` guidance fallbacks only from verified
  in-process availability, never from configuration enablement alone;
- generate authored-orientation guidance through a shared role-based helper
  aligned with parser roles;
- generate a transient projection-extractive sketch only for eligible
  `generic-markdown` sources without authored orientation;
- add lexical-only `query_variants` parsing across Python, HTTP `/query` and
  `/search`, MCP JSON-RPC compatibility, and MCP Streamable HTTP structured
  surfaces;
- defer CLI query-variant flags;
- implement deterministic lexical RRF fusion without changing single-query
  lexical behavior;
- update OpenAPI, MCP tool schemas/descriptions, README, architecture, and
  tests;
- leave persisted derived orientation indexes for a later explicit lifecycle.

## Affected Areas

- `src/llmwiki_serve/models.py`
- `src/llmwiki_serve/service.py`
- `src/llmwiki_serve/search.py`
- `src/llmwiki_serve/api.py`
- `docs/openapi.json`
- `README.md`
- `docs/architecture.md`
- `tests/test_service.py`
- `tests/test_public_api.py`
- `tests/test_search_postings.py`
- new focused tests when behavior no longer fits existing files

## Workstreams

### 1. Contract Models

- Add compact Pydantic models for the exact guidance object:
  `schema_version`, `orientation_source`, `content_trust`,
  `max_query_variants`, `character_budget`, `folder_cards`, `page_cards`,
  `suggested_terms`, `exact_identifiers`, and `fallback_modes`.
- Add exact `FolderCard` fields: `path`, `page_count`, and `terms`.
- Add exact `PageCard` fields: `page_id`, `title`, `path`, `headings`,
  `terms`, `exact_identifiers`, and `excerpt`.
- Use snake_case for every new serve wire/model field.
- Reject unknown fields and `null` values in V1 guidance models.
- Keep forbidden extended-draft fields out of V1: projection digests,
  diagnostics, selection metadata, next-call metadata, arbitrary frontmatter,
  and raw snippets beyond bounded `excerpt`.
- Add optional `retrieval_guidance` to `ContextPack` and emit it by default
  when `llmwiki_agent_guided_lexical_v1` is advertised.
- Add optional `query_variants` to HTTP `/query` and `/search`, MCP JSON-RPC
  compatibility parsing, MCP Streamable HTTP tool schemas, and Python service
  methods.
- Enforce `query_variants` `maxItems: 2` before normalization.
- Advertise `llmwiki_agent_guided_lexical_v1` in all runtime capability
  surfaces only when the full V1 contract is available.

### 2. Guidance Assembly

- Reuse the immutable retrieval snapshot already read by `context()`.
- Classify guidance source:
  - `authored` when the shared helper finds projected role-based orientation
    pages;
  - `projection_extractive` only for `generic-markdown` with no authored
    orientation;
  - `none` when no safe compact guidance can be produced.
- Implement the shared helper against projected page roles.
- For authored guidance, derive cards and terms from projected orientation
  evidence without changing normal orientation output.
- For projection-extractive guidance, derive folder cards, page cards,
  suggested terms, exact identifiers, and bounded excerpts from allowed current
  projection data only.
- Apply visibility filtering before extraction.
- Use character and item caps, not fake token counts.
- Mark all guidance content with
  `content_trust: "untrusted_source_evidence"`.

### 3. Lexical Variant Fusion

- Trim variants with Unicode-safe whitespace handling.
- Preserve legacy empty-query overview behavior when variants are omitted or
  empty, and require a non-empty primary query only when a variant is supplied.
- Reject non-string, empty-after-trim, or more-than-two supplied variants.
- Insert the primary query first.
- Deduplicate by Unicode NFC plus casefold comparison while preserving first
  original spelling.
- Reject non-empty variants for non-lexical modes before fallback, provider
  setup, vector/hybrid execution, or search fan-out.
- Run the existing lexical path for each effective channel.
- Fuse with deterministic RRF and stable tie-breaks.
- Preserve exact identifier guards and primary-query evidence priority.
- Apply `exclude_page_ids`, visibility, `fields`, and `snippet_chars`
  consistently.
- Keep no-variant lexical output byte/order-compatible.

### 4. Protocol And Documentation

- Update OpenAPI request/response schema.
- Update MCP JSON-RPC and MCP Streamable HTTP tool schemas.
- Update tool descriptions to recommend context-first, guided lexical search,
  read, and optional escalation.
- Document that CLI query-variant flags are deferred and that CLI
  `query/search` remains a single-primary-query surface in V1.
- Update README and architecture with the direct agent workflow.
- Keep vector/hybrid language as explicit optional escalation, with
  guidance fallbacks limited to a valid injected provider or a provider/cache
  already verified by this service instance through manifest capability or
  vector search behavior.
- Avoid public benchmark claims until clean-commit reports exist.

### 5. Benchmark Harness

- Add a bounded engineering harness that reads externally authored plan JSONL
  and makes zero LLM calls.
- Require explicit plan provenance: generator kind/model or human fixture
  identity, prompt/template revision and SHA-256, source-context/input
  digests, timestamp or stable-fixture marker, and accounting source.
- Reject forbidden gold/citation/qrel fields and exact positive qrel
  identifier/path/page-id matches after NFC, casefold, and path/id
  normalization. State that semantic leakage cannot be mechanically proven
  absent.
- Use separate authored and projection corpora. Pair a raw lexical baseline to
  each corpus; assert `orientation_source=authored` for authored arms and
  `orientation_source=projection_extractive` for projection arms.
- Emit the paired six-arm report shape: four deterministic lexical arms
  (`authored_raw_lexical`, `authored_agent_guided_lexical`,
  `projection_raw_lexical`, and `projection_sketch_agent_guided_lexical`) plus
  two optional raw-hybrid reference arms.
- Gate hybrid reference arms on an explicitly supplied provider-backed service.
  When the provider-backed service is absent, skipped hybrid arms carry null
  metrics/distributions, empty per-query rows, zero/not-applicable usage, and
  an explicit limitation.
- Verify in-process service-instance isolation by retaining every returned
  service object and rejecting factories that return the same object across
  orientation/search/arm/query requests. Report cold cache state as
  `null`/unknown unless provider evidence exists.
- Report `public_search_requests` separately from internal lexical channel
  evaluations and count one public request per primary-plus-deduplicated
  variant set.
- Validate qrel/query consistency, including positive qrels per answerable
  query, unknown-qrel rejection, exact qrel row keys, duplicate qrel row
  rejection, and negative-query counts.
- Validate report JSON against an exact closed schema and runtime validator,
  including arm status, metrics, usage, per-query rows, provenance,
  implementation revision/dirty detection, and source/plan/query/qrel digests.
- Use canonical projected/service page ids for fixture identities and reject
  duplicate canonical ids.
- Treat skipped arms as not measured: `null` metrics, `null` distributions,
  empty per-query rows, zero/not-applicable usage, and an explicit limitation.
- Keep the tiny fixture labeled as an engineering harness, not release or
  public evidence. Release evidence must use independently generated
  source-only plans before qrels are available.

## Risks

- Risk: guidance is treated as instructions.
  Mitigation: schema-level `content_trust`, docs, tool descriptions, and
  prompt-injection tests.

- Risk: variant fusion changes existing lexical behavior.
  Mitigation: strict no-variant compatibility tests and separate variant tests.

- Risk: generic Markdown sketches leak private data.
  Mitigation: allowed projection data, visibility filtering, excerpt caps, no
  local roots, and privacy tests.

- Risk: query variants weaken exact identifiers.
  Mitigation: exact identifier safeguards, primary-query insertion, and
  Unicode/path/version/source-ref tests.

- Risk: this is mistaken for persisted derived indexing.
  Mitigation: zero-write implementation, explicit docs, and tests proving
  health/status/search do not build artifacts.

## Rollout

1. Land spec and ADR.
2. Implement models and no-op compatibility tests.
3. Implement guidance assembly with authored orientation first.
4. Add generic Markdown projection-extractive sketching.
5. Add lexical-only query variants and fusion.
6. Update OpenAPI/MCP schemas and docs.
7. Confirm CLI query-variant flags remain deferred.
8. Run focused contract tests, full local test suite, and `git diff --check`.
9. Run benchmark smoke reports, clearly labeled as engineering evidence only.

## Rollback

Because the feature is additive, rollback can remove or disable
`retrieval_guidance` emission and reject `query_variants` while leaving
existing lexical, literal, vector, hybrid, context, search, read, and graph
behavior intact. No source cleanup is required because the workflow writes no
source files and no derived orientation artifact.

## LLMWiki Ingestion Candidates

- `specs/agent-guided-lexical-retrieval/`
- `docs/decisions/2026-08-02-agent-guided-lexical-default-agent-workflow.md`
- `docs/architecture.md` after implementation docs are updated
