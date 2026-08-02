# Spec: Agent-Guided Lexical Retrieval

## Status

Implementation branch.

This spec defines the additive context-first retrieval workflow now implemented
in this worktree. It does not add a new `SearchMode`, implement server-side
query rewriting, publish a benchmark claim, or release the feature.

## Problem

`llmwiki-serve` already exposes read-only wiki context through HTTP, MCP-style
JSON-RPC, MCP Streamable HTTP, CLI, and Python service surfaces. For coding
agents, the strongest default path is to inspect wiki orientation first, then
choose precise lexical terms from the task, authored orientation, file names,
code identifiers, and compact retrieval guidance.

Native LLMWiki-style folders often have authored `hot.md`, `index.md`, or
`overview.md` pages. Generic Markdown folders often do not. For those generic
folders, the server can provide a compact zero-write projection-extractive
orientation sketch without embeddings, model calls, source writes, persisted
derived indexes, or generated source-visible orientation pages.

## Goals

- Make agent-guided lexical retrieval the default recommended direct-agent
  workflow: context, orient, generate lexical variants, search, read, then
  optionally escalate.
- Advertise the exact capability string `llmwiki_agent_guided_lexical_v1`.
- Preserve public `SearchMode = lexical|literal|vector|hybrid`.
- Preserve existing no-variant lexical behavior and search result shapes.
- Add optional `ContextPack.retrieval_guidance` using the closed V1 schema in
  this spec.
- Add optional caller-supplied `query_variants` for lexical search only.
- Preserve legacy empty-query overview behavior when `query_variants` is
  omitted or empty; require the primary `query` to trim non-empty only when at
  least one non-empty lexical variant is supplied.
- Use deterministic lexical fusion for variant channels.
- Use authored orientation when present and keep source-owned pages unchanged.
- For eligible `generic-markdown` sources without authored orientation, provide
  only a transient projection-extractive sketch from immutable projection data.

## Non-Goals

- Do not add `agent`, `guided`, `derived`, or similar values to `SearchMode`.
- Do not change single-query lexical scoring or result ordering when variants
  are absent.
- Do not route default retrieval through embeddings, vector search, hybrid
  search, rerankers, hosted RAG, or model answer synthesis.
- Do not call an LLM in `llmwiki-serve` to generate query variants.
- Do not add CLI `query/search --query-variant` flags in V1.
- Do not create, rewrite, annotate, normalize, reserve, or delete source files.
- Do not generate `hot.md`, `index.md`, `overview.md`, `quickstart.md`, or any
  equivalent source-visible orientation page.
- Do not persist the transient sketch as a derived orientation artifact.
- Do not emit arbitrary frontmatter, projection digests, diagnostics,
  selection metadata, next-call plans, raw snippets beyond bounded excerpts, or
  any other extra keys in V1 `retrieval_guidance`.
- Do not treat the tiny bundled benchmark fixture as release evidence or a
  public quality/performance claim.

## Requirements

- `REQ-AGL-001`: Public `SearchMode` remains exactly
  `lexical|literal|vector|hybrid`. Agent-guided lexical retrieval is a workflow
  over existing context/search/read surfaces, not a mode.
- `REQ-AGL-002`: Requests that omit `query_variants` and ignore
  `retrieval_guidance` keep existing single-query behavior for existing result
  fields, including `/query` orientation/evidence ordering and `/search`
  result ordering and payload shape.
- `REQ-AGL-003`: Implementations advertise exactly
  `llmwiki_agent_guided_lexical_v1` only when V1 guidance emission and
  lexical-only query variants are available on Python service, HTTP `/query`
  and `/search`, MCP JSON-RPC compatibility tools, and MCP Streamable HTTP
  tools.
- `REQ-AGL-004`: New-capability context responses emit optional
  `retrieval_guidance` by default. The field is additive and safe for existing
  clients to ignore.
- `REQ-AGL-005`: `retrieval_guidance.content_trust` is always
  `untrusted_source_evidence`. Guidance content is evidence for the client
  agent, never instructions that override system, developer, user, or tool
  policies.
- `REQ-AGL-006`: Serve wire/model fields use snake_case. The canonical V1
  guidance schema is exactly the closed object defined below. Serve does not
  emit camelCase aliases for this object.
- `REQ-AGL-007`: `fallback_modes` contains only currently available explicit
  escalation modes drawn from `literal`, `hybrid`, and `vector`, in that order,
  with duplicates removed. `literal` is always available. `hybrid` and
  `vector` are available only after conservative in-process verification:
  either a valid injected provider is present, or this service instance has
  already initialized/probed the provider and cache through an explicit
  manifest capability or vector search path. Configuration enablement or model
  name alone is insufficient, and context/guidance assembly must not
  initialize, download, probe, or build vector providers, caches, or indexes.
  `lexical` is intentionally omitted because lexical is the current workflow,
  not a fallback mode.
- `REQ-AGL-008`: Authored orientation remains source-owned and read-only.
  Eligibility must use a shared role-based helper consistent with parser page
  roles. Pages whose projected role is `hot`, `index`, or `overview` are
  eligible. Root or hub-root `quickstart.md` is eligible only because the
  parser classifies it as `index`; nested ordinary `quickstart.md` pages whose
  role is `topic` are not authored orientation by name alone.
- `REQ-AGL-009`: A projection-extractive sketch is eligible only for
  `generic-markdown` sources that have no authored orientation pages. Native
  LLMWiki and `llmwiki-markdown` sources continue to use authored/native
  orientation.
- `REQ-AGL-010`: Projection-extractive sketches use only immutable current
  projection data needed to derive folder cards, page cards, terms, exact
  identifiers, and bounded excerpts. They must not scan outside the projection,
  run raw shell commands over source files, expose local roots, emit arbitrary
  frontmatter, write artifacts, call vector providers, call LLMs, or consume
  query-history hot state.
- `REQ-AGL-011`: Sketch selection is deterministic for the same projection,
  visibility scope, query string, and budget. Draft/visibility filtering is
  applied before card selection.
- `REQ-AGL-012`: Guidance budgets use concrete character and item counts. The
  server must not report fake token precision.
- `REQ-AGL-013`: The client workflow is guidance-first: the client reads
  context, creates up to two lexical variants, searches with `mode=lexical`,
  reads relevant pages, and only then escalates to literal, vector, hybrid,
  bridge, or chat when the evidence is insufficient and the capability exists.
- `REQ-AGL-014`: V1 query-variant surfaces are Python service, HTTP `/query`
  and `/search`, MCP JSON-RPC compatibility tools `llmwiki_context` and
  `llmwiki_search`, and MCP Streamable HTTP tools. CLI query/search variant
  flags are deferred.
- `REQ-AGL-015`: Legacy query/search calls that omit `query_variants` or pass
  `[]` preserve empty-query overview behavior. If at least one non-empty
  `query_variants` item is supplied, the primary `query` must trim to a
  non-empty value. When present, `query_variants` is an input array with
  `maxItems: 2` before normalization. Each item must be a string that trims to
  a non-empty value.
- `REQ-AGL-016`: Effective lexical channels are built by placing primary
  `query` first, then caller variants in order, then removing exact duplicates
  after Unicode NFC plus casefold normalization. The first original spelling is
  preserved. Effective channels are capped at the primary query plus at most
  two additional variants.
- `REQ-AGL-017`: Non-empty `query_variants` is valid only with effective
  `mode=lexical`. Variants with `literal`, `vector`, or `hybrid` are rejected
  before lexical fallback, provider setup, vector/hybrid execution, or search
  fan-out.
- `REQ-AGL-018`: Variant fusion is deterministic RRF over per-channel lexical
  result lists, with a fixed constant and stable tie-breaks documented in
  code/tests.
- `REQ-AGL-019`: Exact identifier safeguards remain stronger than variant
  recall. Code identifiers, versions, source refs, paths, and metadata strings
  visible to lexical indexing must not be broken by variant normalization or
  fusion.
- `REQ-AGL-020`: Variant normalization is Unicode-safe. Korean, English,
  mixed-script identifiers, path-like strings, dotted package names, snake
  case, kebab case, camel case, version strings, and source refs must not be
  stripped through ASCII-only regular expressions.
- `REQ-AGL-021`: `exclude_page_ids`, `fields`, `snippet_chars`,
  `include_drafts`, network draft restrictions, and lexical/literal
  `min_score` compatibility remain as currently defined. Variant fusion applies
  exclusions and visibility consistently to every lexical channel.
- `REQ-AGL-022`: Health, status, manifest, source-bundle metadata, MCP tool
  discovery, ordinary lexical search without variants, literal search, vector
  search, and hybrid search do not build or persist a derived orientation
  index.
- `REQ-AGL-023`: Public docs describe agent-guided lexical as the default
  direct-agent path and semantic vector/hybrid as explicit fallback/escalation,
  not as a required install step.
- `REQ-AGL-024`: The bounded benchmark harness must make zero LLM calls. It
  may only consume an external, versioned plan artifact and must not generate
  variants from qrels.
- `REQ-AGL-025`: External plan rows must include versioned provenance covering
  generator kind and model or human fixture identity, prompt/template revision
  and SHA-256, source-context/input digests, timestamp or stable-fixture
  marker, and token accounting source.
- `REQ-AGL-026`: Benchmark plan validation must reject forbidden gold,
  answer, citation, qrel, relevance, score, target, page-id, doc-id, and
  corpus-id fields, including nested fields. It must also reject original
  query, primary query, and variant strings that exactly match positive qrel
  identifiers, paths, or page ids after Unicode NFC, casefold, and path/id
  normalization.
- `REQ-AGL-027`: The harness must state that semantic leakage cannot be
  mechanically proven absent. Release evidence must use independently
  generated source-only plans before qrels are available.
- `REQ-AGL-028`: Benchmark corpora must separate authored orientation from a
  generic projection corpus. The authored corpus must assert
  `retrieval_guidance.orientation_source=authored`; the projection corpus must
  have no `hot.md`, `index.md`, or `overview.md` pages and must assert
  `retrieval_guidance.orientation_source=projection_extractive`.
- `REQ-AGL-029`: Raw lexical baselines must be paired to each authored and
  projection corpus. The bundled report shape is six-arm: four lexical arms
  for authored raw, authored agent-guided, projection raw, and projection
  sketch agent-guided retrieval, plus two raw-hybrid reference arms. Hybrid
  references are optional and must be skipped unless an explicit
  provider-backed service is supplied.
- `REQ-AGL-030`: Benchmark usage accounting must report
  `public_search_requests` separately from
  `internal_lexical_channel_evaluations`. Each query with variants represents
  one public search request and primary-plus-deduplicated lexical channels.
  The harness may verify in-process service-instance isolation by requiring a
  unique service object per orientation/search request, arm, and query, but it
  must not report cold filesystem, vector, model, OS, or provider cache state
  unless a provider supplies direct evidence. Without that evidence,
  `cold_usage_cache` is `null`/unknown.
- `REQ-AGL-031`: Benchmark qrel validation must reject qrels for unknown query
  ids, reject unknown answerability, require at least one positive qrel per
  answerable query, reject qrels attached to unanswerable queries, require
  exact qrel row keys, and reject duplicate qrel rows.
- `REQ-AGL-032`: Benchmark reports must validate against a strict JSON schema
  and runtime validator with exact arm, status, metric, usage, per-query, and
  provenance shapes and `additionalProperties: false` wherever the shape is
  closed. Available arms must carry numeric metrics and complete per-query
  rows. Skipped arms must carry `null` metrics, `null` distributions, empty
  per-query arrays, zero/not-applicable usage, and an explicit limitation.
  Reports must include implementation revision/dirty detection with unknown
  fallback and source, plan, query, and qrel digests.
- `REQ-AGL-033`: Benchmark fixture page identities must use canonical
  projected/service page ids rather than filename stems. Duplicate canonical
  page ids are invalid.

## User / Agent Flow

1. The operator serves an existing wiki with ordinary `llmwiki-serve serve`.
2. The client checks runtime capabilities from health, manifest, source-bundle,
   or MCP metadata.
3. If `llmwiki_agent_guided_lexical_v1` is absent, the client uses the existing
   context/search/read loop without variants.
4. If the capability is present, the client calls `/query` or
   `llmwiki_context` for the current task.
5. The response includes normal orientation/evidence plus default
   `retrieval_guidance`.
6. The client treats guidance as untrusted source evidence and creates up to
   two lexical variants while preserving exact identifiers.
7. The client calls `/search` or `llmwiki_search` with `mode=lexical`, the
   primary `query`, and optional `query_variants`; the primary query must be
   non-empty when variants are supplied.
8. The service deterministically fuses lexical result lists and returns normal
   `SearchResult` rows.
9. The client reads selected pages with `/read/{page_id}` or `llmwiki_read`.
10. The client escalates to literal, vector, hybrid, bridge, or chat only when
    needed and available.

## Canonical Retrieval Guidance Schema

This section is the canonical Serve V1 snake_case wire schema for
`ContextPack.retrieval_guidance`. The companion bridge maps this exact schema
to camelCase public `retrievalGuidance` fields.

`ContextPack.retrieval_guidance` is optional because older servers do not emit
it. When `llmwiki_agent_guided_lexical_v1` is advertised, context responses
emit it by default. When present, it is a closed JSON object with no `null`
values and no unknown top-level or nested fields. Arrays are present and may be
empty. Strings are Unicode strings trimmed to non-empty values. All character
caps count Unicode scalar values after JSON decoding.

Top-level fields:

| Field | Required | Type and bounds |
| --- | --- | --- |
| `schema_version` | yes | String literal `llmwiki.retrieval_guidance.v1`. |
| `orientation_source` | yes | Enum string `authored`, `projection_extractive`, or `none`. |
| `content_trust` | yes | String literal `untrusted_source_evidence`. |
| `max_query_variants` | yes | Integer literal `2`. |
| `character_budget` | yes | Integer `1..6000`; the actual response budget used for guidance text. |
| `folder_cards` | yes | Array length `0..8` of `FolderCard`. |
| `page_cards` | yes | Array length `0..12` of `PageCard`. |
| `suggested_terms` | yes | Array length `0..16` of unique strings, each `1..120` characters. |
| `exact_identifiers` | yes | Array length `0..16` of unique strings, each `1..240` characters. |
| `fallback_modes` | yes | Ordered unique array of enum strings drawn from `literal`, `hybrid`, and `vector`, including only modes currently available for explicit escalation under the verified in-process availability rules in `REQ-AGL-007`. |

`FolderCard` is a closed object:

| Field | Required | Type and bounds |
| --- | --- | --- |
| `path` | yes | Source-relative path string, `1..1024` characters, using `/` separators; must not be absolute or contain `..` path segments. |
| `page_count` | yes | Integer `>= 1`. |
| `terms` | yes | Array length `0..8` of unique strings, each `1..120` characters. |

`PageCard` is a closed object:

| Field | Required | Type and bounds |
| --- | --- | --- |
| `page_id` | yes | Serve page id string, `1..512` characters. |
| `title` | yes | String `1..240` characters. |
| `path` | yes | Source-relative path string, `1..1024` characters, using `/` separators; must not be absolute or contain `..` path segments. |
| `headings` | yes | Array length `0..8` of unique strings, each `1..160` characters. |
| `terms` | yes | Array length `0..12` of unique strings, each `1..120` characters. |
| `exact_identifiers` | yes | Array length `0..8` of unique strings, each `1..240` characters. |
| `excerpt` | yes | Bounded non-empty excerpt string, `1..240` characters, extracted only from projected text. |

V1 guidance does not contain arbitrary frontmatter, projection digests,
diagnostics, selection objects, next-call objects, raw source snippets beyond
bounded `excerpt`, nullable fields, or extra keys. Page cards that cannot
produce a safe non-empty excerpt are omitted rather than emitted with `null` or
an empty string.

### Bridge CamelCase Mapping

The bridge maps valid Serve guidance to this exact public camelCase shape:

| Serve field | Bridge public field |
| --- | --- |
| `retrieval_guidance` | `retrievalGuidance` |
| `schema_version` | `schemaVersion` |
| `orientation_source` | `orientationSource` |
| `content_trust` | `contentTrust` |
| `max_query_variants` | `maxQueryVariants` |
| `character_budget` | `characterBudget` |
| `folder_cards` | `folderCards` |
| `folder_cards[].page_count` | `folderCards[].pageCount` |
| `page_cards` | `pageCards` |
| `page_cards[].page_id` | `pageCards[].pageId` |
| `page_cards[].exact_identifiers` | `pageCards[].exactIdentifiers` |
| `suggested_terms` | `suggestedTerms` |
| `exact_identifiers` | `exactIdentifiers` |
| `fallback_modes` | `fallbackModes` |

All other nested field names (`path`, `terms`, `title`, `headings`, and
`excerpt`) are already lower camelCase-compatible single words and are
preserved as-is by the bridge.

Unknown or malformed guidance is invalid for Serve emission. The bridge omits
unknown or malformed source guidance entirely rather than partially forwarding
it.

## Query Variant Request Field

The canonical Serve request field is `query_variants`. It is available only on
V1 Python service, HTTP `/query` and `/search`, MCP JSON-RPC compatibility
tools, and MCP Streamable HTTP structured surfaces. CLI flags are omitted in
V1.

`query` preserves legacy compatibility. Omission or an empty/whitespace value
means overview behavior when `query_variants` is omitted or `[]`.
`query_variants` is optional. Omission or `[]` means no additional lexical
channels and preserves existing single-query behavior. `null` is invalid.

When present, `query_variants` is an array of at most two JSON strings before
normalization. Each item is trimmed with Unicode-safe whitespace handling and
must remain non-empty. Empty strings, non-strings, and arrays with more than
two items are invalid.

Effective lexical channels are normalized deterministically:

1. Validate supplied variants first. If any variant is supplied, trim the
   primary `query`; it must be non-empty.
2. Start channels with the original primary `query` for legacy no-variant
   calls, or the trimmed primary query when variants are supplied.
3. Append caller variants in caller order.
4. Deduplicate by Unicode NFC plus casefold comparison.
5. Preserve the first original spelling for each deduplicated channel.
6. Cap effective channels at three: the primary query plus at most two
   additional variants.

Non-empty `query_variants` is valid only when the effective `mode` is
`lexical`. Non-empty variants with `literal`, `vector`, or `hybrid` are
invalid and must be rejected before fallback, provider setup, vector/hybrid
execution, or search fan-out.

## Compatibility

- HTTP and MCP request changes are additive.
- `ContextPack` adds an optional field and keeps existing fields intact.
- Search result rows remain unchanged.
- Existing `/query` and `/search` single-query lexical behavior is preserved
  when variants are absent; `/query` only adds default guidance for
  new-capability responses.
- Existing `literal`, `vector`, and `hybrid` behavior is preserved when
  variants are absent.
- OpenAPI and MCP schemas change only to describe additive guidance and variant
  fields using the closed snake_case schema defined here.
- Clients that do not understand guidance or variants can keep using the
  current context/search/read loop.
- CLI `query/search` remains a single-primary-query surface in V1.
- Bridge interop maps Serve snake_case guidance to public camelCase. Serve
  does not implement bridge APIs or camelCase guidance aliases.

## Data Safety

Guidance and sketches may expose projected page titles, relative paths,
headings, terms, exact identifiers, and bounded excerpts. They must not expose
local roots, private endpoint URLs, credentials, raw Redis keys, model paths,
raw query history, arbitrary frontmatter, draft content in approved-only
responses, or source text beyond configured excerpt limits.

Projection-extractive sketches are transient response content. They are not
source pages, not derived index artifacts, not vector caches, and not managed
hot files. They are invalidated by current projection freshness.

## Acceptance Criteria

- The spec and ADR exist before implementation.
- `SearchMode` is unchanged.
- The exact capability string is `llmwiki_agent_guided_lexical_v1`.
- New-capability context responses emit optional `retrieval_guidance` by
  default.
- `retrieval_guidance` follows the exact closed schema in this spec and emits
  no extra fields.
- Bridge interop maps every canonical Serve guidance field to the expected
  camelCase public field and omits malformed guidance entirely.
- `query_variants` is accepted only for lexical mode, has `maxItems: 2` before
  normalization, requires non-empty strings, requires a non-empty primary query
  only when variants are supplied, and preserves omitted/empty-variant overview
  behavior.
- Effective variants deduplicate by NFC plus casefold while preserving original
  spelling and Unicode content.
- Generic Markdown sketching uses only allowed projection data and writes
  nothing.
- Context before vector verification reports literal-only fallbacks; valid
  injected providers or successful in-process manifest/vector probes may add
  `hybrid` and `vector`.
- Provider initialization failures keep manifest capabilities and guidance
  fallback modes literal-only and aligned.
- Authored orientation remains source-owned and unchanged.
- Authored orientation eligibility uses the shared role-based helper.
- OpenAPI and MCP schemas cover the additive fields.
- Unit and contract tests cover single-query compatibility, variant fusion,
  Unicode identifiers, draft filtering, prompt-injection markers, guidance
  schema closure, and no-write behavior.
- The bundled benchmark fixture is labeled as a tiny engineering harness, not
  release or public evidence.
- Benchmark reports expose exact paired six-arm authored/projection/raw/hybrid
  status, strict provenance, qrel consistency counts, public-vs-internal search
  usage, service-instance isolation evidence, cold-cache unknown status,
  skipped-arm null semantics, and source/plan/query/qrel digests.

## Resolved Decisions

- CLI query/search variant flags are deferred.
- `retrieval_guidance` is emitted by default for context responses when
  `llmwiki_agent_guided_lexical_v1` is advertised.
- The V1 cap is the primary `query` channel plus at most two supplied
  `query_variants`; the primary query may be empty only when variants are
  omitted or empty to preserve legacy overview behavior.
- Serve is the canonical source for the snake_case guidance schema; the bridge
  maps that schema to camelCase and omits unknown or malformed guidance.
- Clean-commit benchmark threshold policy and persisted derived orientation
  artifact consumption remain separate follow-on work.

## References

- ADR: `../../docs/decisions/2026-08-02-agent-guided-lexical-default-agent-workflow.md`
- Derived orientation lifecycle ADR:
  `../../docs/decisions/2026-08-02-derived-orientation-index-artifact-lifecycle.md`
- Semantic vector retrieval spec: `../semantic-vector-retrieval/`
- Architecture: `../../docs/architecture.md`
