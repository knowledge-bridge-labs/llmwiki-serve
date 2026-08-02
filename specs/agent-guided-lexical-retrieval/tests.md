# Tests: Agent-Guided Lexical Retrieval

## Acceptance Coverage

- `SearchMode` remains exactly `lexical|literal|vector|hybrid`.
- Runtime metadata advertises exact capability
  `llmwiki_agent_guided_lexical_v1` only when the full V1 contract is
  available.
- New-capability context responses emit optional `retrieval_guidance` by
  default without breaking existing fields.
- `retrieval_guidance.orientation_source` is one of `authored`,
  `projection_extractive`, or `none`.
- `retrieval_guidance.content_trust` is always
  `untrusted_source_evidence`.
- `retrieval_guidance` is a closed snake_case schema with exactly these
  top-level fields:
  `schema_version`, `orientation_source`, `content_trust`,
  `max_query_variants`, `character_budget`, `folder_cards`, `page_cards`,
  `suggested_terms`, `exact_identifiers`, and `fallback_modes`.
- `fallback_modes` includes `hybrid` and `vector` only when vector availability
  has already been verified in this service instance through a valid injected
  provider or explicit manifest/vector search provider-cache probe.
- Context/guidance calls on configured but unprobed lazy providers do not
  initialize providers, download models, initialize caches, or build indexes,
  and report literal-only fallbacks.
- Provider initialization failures keep manifest capabilities and guidance
  fallback modes literal-only and aligned.
- Required arrays are present and may be empty. `null` is invalid. Unknown
  top-level or nested guidance fields are rejected by schema/model validation.
- The forbidden extended-draft fields are absent from V1 guidance:
  `trust`, `projection_digest`, `budgets`, `selection`, `next_calls`,
  `diagnostics`, `frontmatter`, `summary`, `role`, `tags`, `source_refs`,
  `links`, and `relative_path`.
- Bridge interop mapping preserves each canonical Serve guidance field under
  the expected camelCase public name and omits malformed guidance entirely.
- Search/query calls without `query_variants` preserve current single-query
  lexical result behavior.
- Non-empty `query_variants` is accepted only with `mode=lexical` and only on
  Python, HTTP `/query` and `/search`, MCP JSON-RPC compatibility, and MCP
  Streamable HTTP surfaces.
- CLI query-variant flags are absent in V1.
- Supplied `query_variants` has `maxItems: 2` before normalization. Each item
  must trim to a non-empty string.
- Effective variant channels are Unicode-safe, NFC plus casefold deduplicated,
  primary-query first, and capped at three total.
- Variant fusion is deterministic and preserves exact identifier safeguards.
- Authored orientation pages remain source-owned and unchanged.
- Authored orientation eligibility is role-based and consistent with parser
  roles.
- Generic Markdown projection-extractive sketches are transient, zero-write,
  visibility-filtered, capped, and based only on allowed projection data.
- Health, status, manifest, source-bundle, MCP tool discovery, and ordinary
  search do not build or write a persisted derived orientation index.
- Prompt-injection content in guidance is marked as untrusted evidence.

## Unit Tests

- `ContextPack` serializes and validates `retrieval_guidance` with
  `schema_version: "llmwiki.retrieval_guidance.v1"`.
- New `RetrievalGuidance` fields serialize as snake_case and do not expose
  camelCase aliases.
- `max_query_variants` serializes as literal `2`.
- `character_budget` validates as a positive bounded integer with max `6000`.
- `orientation_source` validates exactly `authored`,
  `projection_extractive`, and `none`.
- `folder_cards` validates length `0..8`.
- `folder_cards[]` validates only `path`, `page_count`, and `terms`.
- `folder_cards[].page_count` validates as integer `>= 1`.
- `folder_cards[].terms` validates length `0..8`, uniqueness, trimming, and
  per-item character cap.
- `page_cards` validates length `0..12`.
- `page_cards[]` validates only `page_id`, `title`, `path`, `headings`,
  `terms`, `exact_identifiers`, and `excerpt`.
- `page_cards[].headings` validates length `0..8`.
- `page_cards[].terms` validates length `0..12`.
- `page_cards[].exact_identifiers` validates length `0..8`.
- `page_cards[].excerpt` validates non-empty bounded excerpt text with max 240
  characters.
- Top-level `suggested_terms` validates length `0..16`, trimming, uniqueness,
  and Unicode preservation.
- Top-level `exact_identifiers` validates length `0..16`, trimming,
  uniqueness, and Unicode preservation.
- `fallback_modes` validates ordered unique values drawn only from available
  `literal`, `hybrid`, and `vector` modes.
- Guidance models reject unknown top-level and nested fields and reject `null`
  for every V1 field.
- Authored guidance identifies projected `hot`, `index`, and `overview` role
  pages without changing orientation output.
- The shared authored-orientation helper uses projected page roles rather than
  filename-only checks.
- Root or hub-root `quickstart.md` classified by the parser as `index` is
  eligible authored orientation.
- Nested ordinary `quickstart.md` classified as `topic` is not eligible
  authored orientation by name alone.
- Generic Markdown with no authored orientation produces
  `orientation_source=projection_extractive`.
- Native LLMWiki, `llmwiki-markdown`, and sources with authored orientation do
  not use projection-extractive sketching.
- Sketch cards omit local roots, arbitrary frontmatter, private endpoints,
  credentials, raw query history, Redis URLs, model paths, and draft-only data.
- Sketch selection is deterministic for the same projection, query, budget, and
  visibility.
- Empty or all-draft approved-only sources return
  `orientation_source=none` or empty capped arrays without crashing.
- Variant normalization trims and deduplicates variants while keeping the
  primary query first.
- Variant deduplication uses Unicode NFC plus casefold comparison and preserves
  the first original spelling.
- Variant normalization preserves Korean text, Unicode identifiers, path-like
  strings, dotted names, snake_case, kebab-case, camelCase, version strings,
  and source refs.
- Requests with more than two supplied variants are rejected actionably before
  normalization.
- Requests with an empty variant string after trimming are rejected actionably.
- Requests with variants and a missing or empty primary query are rejected
  actionably, while omitted or empty variants preserve legacy empty-query
  overview behavior.
- Duplicate variants of the primary query or earlier variants are dropped
  before fusion.
- Lexical variant fusion uses the documented RRF constant.
- RRF tie-breaks are stable.
- Exact identifier queries cannot be satisfied only by weaker variant matches.
- `exclude_page_ids` applies to every variant channel before fusion.
- `snippet_chars` and `fields` projection are applied after final result
  selection without changing result field names.

## Integration And Contract Tests

- Health, manifest, source-bundle, MCP JSON-RPC metadata, and MCP Streamable
  HTTP metadata/tool discovery advertise exactly
  `llmwiki_agent_guided_lexical_v1` when V1 is enabled.
- HTTP `/query` returns existing context fields plus default
  `retrieval_guidance` when the capability is advertised.
- HTTP `/query` with no variants preserves existing orientation/evidence order
  and field bytes aside from additive guidance.
- HTTP `/search` with no variants matches previous lexical output.
- HTTP `/query` and `/search` with lexical variants return fused results
  through the existing result shape.
- HTTP requests reject variants with `mode=literal`, `mode=vector`, and
  `mode=hybrid`, more than two supplied variants, empty variants, and variants
  without a non-empty primary query.
- MCP JSON-RPC `llmwiki_context` exposes default guidance.
- MCP JSON-RPC `llmwiki_search` accepts lexical variants and rejects invalid
  variant requests.
- MCP Streamable HTTP schemas expose the same additive arguments and
  constraints.
- Python `LlmWikiService.context()` and `.search()` expose the same behavior.
- CLI `query/search` help and parsing do not expose or accept
  `--query-variant` in V1.
- Generated OpenAPI includes `retrieval_guidance` and `query_variants` with
  snake_case field names and `query_variants` `maxItems: 2`.
- MCP Streamable HTTP `tools/list` input schemas for `llmwiki_context` and
  `llmwiki_search` expose `query_variants` as `array[string]` with
  `maxItems: 2`, and runtime tool calls reject more than two supplied variants.
- Generated OpenAPI marks guidance objects as closed and documents all caps.
- Bridge interop golden fixtures map Serve `retrieval_guidance` to bridge
  `retrievalGuidance`, including `schemaVersion`, `orientationSource`,
  `contentTrust`, `maxQueryVariants`, `characterBudget`, `folderCards`,
  `folderCards[].pageCount`, `pageCards`, `pageCards[].pageId`,
  `pageCards[].exactIdentifiers`, `suggestedTerms`, `exactIdentifiers`, and
  `fallbackModes`.
- Network draft restrictions continue to ignore `include_drafts=true` unless
  draft access is enabled.
- Source tree snapshots before and after context/search calls are identical
  when separately managed-context hit-state writes are disabled or isolated.
- Health/status/manifest/source-bundle/tool-list calls do not create sidecar
  files or derived orientation artifacts.
- Existing vector/hybrid provider-disabled errors are unchanged when variants
  are absent.
- Existing vector/hybrid behavior rejects variants before provider execution
  when variants are present.

## Security And Prompt-Injection Tests

- Authored orientation content saying to ignore instructions is returned only
  as cited untrusted source evidence.
- Projection-extractive excerpts containing prompt-like text do not change the
  `content_trust` marker or server policy.
- Guidance does not leak hidden/draft/private pages in approved-only
  responses.
- Guidance does not include raw local paths, credentials, tokens, private
  endpoints, Redis URLs, model cache paths, raw cache keys, or raw vectors.

## Benchmark And Manual Checks

- Run the deterministic paired six-arm fixture: authored raw lexical, authored
  agent-guided lexical, projection raw lexical, projection-sketch
  agent-guided lexical, authored raw hybrid, and projection raw hybrid.
- Assert the authored fixture corpus reports
  `retrieval_guidance.orientation_source=authored`.
- Assert the projection fixture corpus has no `hot.md`, `index.md`, or
  `overview.md` pages and reports
  `retrieval_guidance.orientation_source=projection_extractive`.
- Treat the two raw-hybrid arms as reference-only. Run them only when an
  explicit provider-backed service is supplied; otherwise assert skipped status
  with `null` metrics,
  `null` distributions, empty per-query rows, zero/not-applicable usage, and
  an explicit limitation.
- Verify the runner makes zero LLM calls and consumes only external plan JSONL.
- Verify singleton or reused service factories are rejected and every measured
  orientation/search request receives a unique service object retained by the
  tracker.
- Verify `cold_usage_cache` is `null`/unknown unless direct provider evidence
  exists, and skipped arms do not claim cold cache or service isolation.
- Verify plan rows include versioned provenance for generator kind/model or
  human fixture identity, prompt/template revision and SHA-256,
  source-context/input digests, timestamp or stable-fixture marker, and
  accounting source.
- Reject forbidden gold/citation/qrel/relevance/score/target/page-id/doc-id
  fields, including nested fields.
- Reject original query, primary query, and variant strings that exactly match
  positive qrel identifiers, paths, or page ids after NFC, casefold, and
  path/id normalization.
- Falsify qrel consistency: unknown qrel query ids are rejected, answerable
  queries require at least one positive qrel, unknown answerability is
  rejected, unanswerable queries must not have qrels, qrel rows must have
  exact keys, and duplicate qrel rows are rejected.
- Verify fixture page identities come from canonical projected/service page ids
  and duplicate canonical ids are rejected.
- Record public search requests separately from internal lexical channel
  evaluations; each primary-plus-deduplicated variant set counts as one public
  search request.
- Record search calls, read calls, variant count, character/token counting
  method, latency, nDCG/recall/MAP or accepted metric set, citation precision,
  total/evaluated/negative query counts, qrel counts, and negative-query
  false-positive diagnostics.
- Validate the report against the strict JSON schema and runtime validator,
  including exact arm/status/metric/usage/per-query/provenance shapes and
  closed additional properties. Falsify mixed skipped/available shapes in both
  runtime validation and bundled JSON schema validation.
- Include source, plan, query, qrel, implementation revision, and dirty-state
  provenance digests/metadata.
- Future public-claim expansion: add SciFact as a no-authored-orientation
  corpus and label whether it is raw lexical or transient-sketch-agent.
- Future public-claim expansion: add Korean judged-pool or accepted Korean
  fixtures with explicit limitations.
- Future public-claim expansion: add real LLMWiki/OpenWiki-style fixtures with
  authored orientation.
- Future public-claim expansion: add generic Markdown/Obsidian-like fixtures
  with no authored orientation.
- Future public-claim expansion: add code identifier fixtures where exact
  symbols must survive variant generation and fusion.
- Include platform, package version, commit SHA, and dirty-state flag.
- State that semantic leakage cannot be mechanically proven absent.
- Do not publish quality or performance claims from dirty-snapshot reports or
  from the tiny bundled engineering fixture.
- Release evidence must use independently generated source-only plans before
  qrels are available.

## Hard-Fail Tests

- Any advertised capability string other than
  `llmwiki_agent_guided_lexical_v1` for this workflow.
- Any new serve guidance wire/model field using camelCase.
- Any source file created, rewritten, deleted, or annotated by guidance or
  variant search.
- Any generated `hot.md`, `index.md`, `overview.md`, or `quickstart.md`.
- Any persisted derived orientation artifact built by health, status,
  manifest, source-bundle, context, or ordinary search.
- Any no-variant lexical ordering regression.
- Any ASCII-only variant normalization that drops Korean or Unicode
  identifiers.
- Any request accepting more than two supplied `query_variants`.
- Any variant request accepting a missing or empty primary `query` when a
  variant is supplied.
- Any variant request accepting an empty variant string after trimming.
- Any variant request with vector/hybrid/literal that silently falls back to
  lexical.
- Any exact identifier result satisfied only by approximate or unrelated
  variant evidence.
- Any guidance payload that omits `content_trust`, emits a forbidden
  extended-draft field, allows `null`, exceeds caps, or accepts an unknown
  top-level or nested field.
- Any Serve response that exposes camelCase guidance aliases.
- Any bridge interop mapping fixture that drops, reorders, or changes a
  canonical Serve guidance value.
- Any benchmark report that presents dirty-snapshot results as public release
  evidence.
- Any benchmark report that presents the tiny engineering fixture as release
  or public evidence.
- Any benchmark runner LLM call.
- Any benchmark plan derived from qrels.
- Any benchmark plan missing required versioned provenance.
- Any benchmark plan containing gold/citation/qrel fields.
- Any query or variant that exactly matches a positive qrel identifier, path,
  or page id after normalization.
- Any authored/projection fixture corpus overlap that makes orientation-source
  comparisons ambiguous.
- Any projection fixture corpus containing `hot.md`, `index.md`, or
  `overview.md`.
- Any authored/projection arm with the wrong
  `retrieval_guidance.orientation_source`.
- Any report that collapses public search requests and internal lexical
  channel evaluations into one ambiguous count.
- Any answerable query without a positive qrel, unknown qrel query id, unknown
  answerability, or unanswerable query with qrels.
- Any duplicate qrel row or qrel row with extra/missing keys.
- Any fixture identity derived from filename stems instead of canonical
  projected/service page ids, or any duplicate canonical page id.
- Any benchmark report accepted with extra JSON properties outside the strict
  schema/runtime contract.
- Any skipped arm accepted with numeric metrics, non-null distributions,
  synthetic per-query rows, or measured-cache claims.
- Any available arm accepted with null metrics or missing per-query rows.
- Any report claiming `cold_usage_cache=true` without direct provider evidence.
- Any singleton or reused service factory accepted by the harness.
