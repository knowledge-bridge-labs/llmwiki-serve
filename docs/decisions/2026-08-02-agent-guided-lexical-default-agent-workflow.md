# ADR: Agent-Guided Lexical Is The Default Agent Workflow, Not A New SearchMode

## Status

Accepted and implemented in this branch for the Serve V1 runtime contract.

This decision defines the default recommended direct-agent workflow and
additive contracts. It does not change `SearchMode`, build a persisted derived
orientation index, publish a benchmark, release the feature, or make a public
quality or performance claim. Clean-commit public benchmark/release evidence
remains pending.

Sanitized dirty-snapshot engineering validation has been recorded for this
branch: Windows full suite `640` passed / `7` skipped plus focused
agent-guided checks `22` passed; DGX Spark Ubuntu full suite `645` passed /
`2` skipped plus deterministic checks `29/29` and one real Qwen tool-call
trial. These are non-public engineering signals, not release evidence and not
DeepAgents ACP validation.

The separate derived orientation index artifact lifecycle remains independent
and proposed in
`2026-08-02-derived-orientation-index-artifact-lifecycle.md`.

## Context

`llmwiki-serve` is a read-only source projection service. It exposes the same
core behavior through HTTP, MCP-style JSON-RPC, MCP Streamable HTTP, CLI, and
Python service calls. The current public retrieval mode enum is
`lexical|literal|vector|hybrid`.

The strongest direct-agent product flow is different from adding another
server mode. Codex, Claude Code, Copilot, Cursor, and similar clients can first
inspect orientation and then create better lexical search terms from the task,
authored wiki pages, file names, exact identifiers, and compact source
evidence.

The feature is negotiated through capability metadata rather than through a new
search mode. The exact capability string for this workflow is
`llmwiki_agent_guided_lexical_v1`.

Native LLMWiki sources often contain authored orientation pages such as
`hot.md`, `index.md`, or `overview.md`. Generic Markdown folders often lack
those pages. The service can help generic sources by producing a transient,
zero-write projection-extractive sketch from the current immutable projection.
That is not the same thing as a persisted derived orientation index. The
derived orientation index lifecycle is governed by a separate ADR and remains
explicit build/load/require work for later.

## Decision

Make agent-guided lexical retrieval the default recommended workflow for direct
agent use:

1. The client checks runtime capabilities in health, manifest, source-bundle,
   or MCP metadata.
2. If `llmwiki_agent_guided_lexical_v1` is present, the client calls `/query`
   or `llmwiki_context`.
3. The server returns the normal context pack plus default structured
   `retrieval_guidance`.
4. The client treats guidance as untrusted source evidence.
5. The client creates at most two lexical variants from the user task,
   orientation, exact identifiers, and sketch cards.
6. The client calls `/search` or `llmwiki_search` with `mode=lexical`, the
   primary `query`, and optional caller-supplied `query_variants`.
7. The server deterministically fuses lexical result lists.
8. The client reads selected pages and escalates to literal, vector, hybrid,
   bridge, or chat only when needed.

Do not add a new `SearchMode`. The public mode enum remains
`lexical|literal|vector|hybrid`. Agent-guided lexical is a workflow over
existing context/search/read surfaces.

Advertise `llmwiki_agent_guided_lexical_v1` only when the full V1 contract is
available through the Python service, HTTP `/query` and `/search`, MCP
JSON-RPC compatibility tools, and MCP Streamable HTTP tools. CLI query-variant
flags are deferred and are not part of V1 capability support.

Add optional `retrieval_guidance` to `ContextPack` with schema version
`llmwiki.retrieval_guidance.v1`. New serve wire/model fields use snake_case.
The canonical field contract lives in
`specs/agent-guided-lexical-retrieval/spec.md`; this ADR intentionally does not
duplicate a second schema. That spec is the source of truth for the closed V1
field set:

- `schema_version`
- `orientation_source`
- `content_trust`
- `max_query_variants`
- `character_budget`
- `folder_cards`
- `page_cards`
- `suggested_terms`
- `exact_identifiers`
- `fallback_modes`

The schema has no `null` values, no unknown fields, and no extended-draft
fields such as projection digests, diagnostics, selection metadata, next-call
metadata, arbitrary frontmatter, or raw snippets beyond bounded `excerpt`.
`content_trust` is always `untrusted_source_evidence`. When
`llmwiki_agent_guided_lexical_v1` is advertised, context responses emit
`retrieval_guidance` by default with `orientation_source` set to `authored`,
`projection_extractive`, or `none`.

Guidance fallback modes are conservative. `literal` is always available, but
`hybrid` and `vector` appear only when this service instance has verified
vector availability through a valid injected provider or a successful explicit
manifest capability/vector search path that initializes/probes the provider and
cache. Context/guidance assembly itself must not initialize, download, probe,
or build vector providers, caches, or indexes. Vector configuration enablement
and model names alone are insufficient, and provider initialization failures
keep manifest capabilities and guidance fallback modes literal-only.

Bridge interop maps the canonical Serve snake_case schema to the bridge public
camelCase `retrievalGuidance` shape. Serve does not expose camelCase aliases or
bridge APIs, and the bridge must omit unknown or malformed guidance rather than
partially forwarding it.

Use authored orientation first. When projected role-based orientation pages are
present, guidance can point to or compactly summarize those pages, but
eligibility must be determined by a shared helper consistent with parser roles.
Pages whose projected role is `hot`, `index`, or `overview` are eligible. Root
or hub-root `quickstart.md` is eligible because the parser classifies it as
`index`; nested ordinary `quickstart.md` pages whose role is `topic` are not
authored orientation by name alone. These pages remain source-owned, read-only
evidence. Retrieval must not generate, replace, rewrite, or annotate them.

For `generic-markdown` sources with no authored orientation, generate only a
transient projection-extractive sketch. The sketch uses allowed current
projection data only to derive folder cards, page cards, suggested terms, exact
identifiers, and bounded excerpts. It does not run raw source shell scans, scan
outside the projection, use an LLM, use embeddings, write source files, write
external artifacts, or consume query-history hot state.

Add optional caller-supplied `query_variants` for lexical mode only. The
primary `query` is inserted first. Legacy empty-query overview behavior is
preserved when `query_variants` is omitted or empty; when at least one variant
is supplied, the primary query must trim to a non-empty string. Caller input
accepts at most two supplied variants before normalization, and each item must
trim to a non-empty string. Effective lexical channels are deduplicated by
Unicode NFC plus casefold comparison while preserving first original spelling.
Requests with an empty primary query plus supplied variants, empty variants,
more than two supplied variants, or variants supplied with `literal`, `vector`,
or `hybrid` are rejected actionably rather than ignored or silently rerouted.

Fuse variant channels with deterministic RRF and stable tie-breaks. Exact
identifier safeguards remain stronger than variant recall. Unicode identifiers,
Korean terms, code symbols, dotted packages, snake_case, kebab-case,
camelCase, path-like strings, version strings, and source refs must not be
destroyed by ASCII-only normalization.

Health, status, manifest, source-bundle metadata, MCP tool discovery, ordinary
lexical search, literal search, vector search, and hybrid search do not build
or persist a derived orientation index. A future ready explicit derived
orientation artifact may be read only under a separately specified policy. The
transient projection-extractive sketch itself is zero-write.

OpenAPI and MCP schemas must expose the additive fields. Documentation should
describe agent-guided lexical as the default direct-agent path and
vector/hybrid as explicit optional escalation.

## Consequences

The project can explain a simpler default story: serve an existing wiki, let
the agent inspect context, then search lexically with better terms. Users do
not need to install vector dependencies or build artifacts for the first useful
path.

The public retrieval mode enum stays stable. Existing clients that send one
lexical query keep current behavior, while newer clients can opt into variant
fusion without changing result payload shape.

New-capability context responses include additive guidance by default, so full
response bytes change only by adding the new field. Existing `/query`
orientation/evidence order and `/search` result order/payload shape stay
compatible when `query_variants` are absent.

Generic Markdown folders gain orientation help without source mutation. The
tradeoff is that projection-extractive sketches are shallow and bounded; they
are not a full hierarchy, not a learned hot cache, and not a semantic index.

Benchmarking becomes more honest. Reports can compare raw lexical,
authored-agent lexical, transient-sketch-agent lexical, vector, and hybrid
instead of implying that a single server mode is always best.

## Security And Privacy

Guidance content is untrusted evidence. Authored orientation and extracted
sketch excerpts may contain prompt-like text, poisoned prose, malicious links,
or stale claims. Clients must keep normal instruction hierarchy and tool policy
above this content.

Projection-extractive guidance must not expose local roots, private endpoints,
credentials, raw Redis keys, model cache paths, raw vectors, arbitrary
frontmatter, raw query history, or draft/private pages in approved-only
responses. It is transient response content and is invalidated by current
projection freshness.

The workflow keeps the source tree immutable. It creates no generated
orientation pages and no persisted derived orientation artifact.

## Rollout

1. Land the spec and this ADR.
2. Add guidance models and compatibility tests.
3. Add authored-orientation guidance.
4. Add generic Markdown projection-extractive sketching.
5. Add lexical-only `query_variants` and deterministic fusion.
6. Update OpenAPI, MCP schemas, README, and architecture.
7. Confirm CLI query-variant flags remain deferred.
8. Run focused HTTP/MCP/Python tests plus full local validation.
9. Run benchmark smoke reports and label them as engineering evidence until
   clean-commit public-report gates are accepted.

Rollback is additive: stop emitting `retrieval_guidance` and reject
`query_variants`. Existing lexical, literal, vector, hybrid, context, search,
read, graph, and manifest behavior remains available. No source cleanup is
needed.

## Tests

Required tests before release:

- No-variant lexical requests keep byte/order-compatible results.
- `query_variants` works only with lexical mode and rejects non-lexical modes.
- Variant requests with supplied variants require a non-empty primary query,
  reject empty variants, and accept at most two supplied variants.
- Variant fusion is deterministic and preserves exact identifiers.
- Configured but unprobed vector services keep context guidance literal-only
  without provider/cache initialization; verified injected or same-instance
  probed providers add vector/hybrid fallbacks.
- Provider initialization failures keep manifest capabilities and guidance
  fallback modes literal-only and aligned.
- Unicode/Korean/code/path/version/source-ref variants survive normalization.
- Authored orientation guidance leaves source pages and orientation output
  unchanged.
- Authored orientation eligibility matches parser roles.
- Generic Markdown sketching writes nothing and uses only allowed projection
  data.
- Approved-only guidance excludes drafts/private content.
- Health/status/manifest/source-bundle/tool discovery do not build or write
  derived orientation artifacts.
- Runtime metadata advertises exactly `llmwiki_agent_guided_lexical_v1`.
- OpenAPI and MCP schemas expose the additive fields.
- New serve fields serialize as snake_case.
- Guidance schema tests cover the exact closed Serve schema, non-nullability,
  unknown-field rejection, caps, and forbidden extended-draft fields.
- Bridge interop fixtures map every canonical Serve snake_case guidance field
  to the expected camelCase bridge public field.
- Prompt-injection fixtures preserve the `untrusted_source_evidence` boundary.
- Benchmarks report raw lexical, authored-agent, transient-sketch-agent,
  vector, and hybrid arms separately, with calls, latency, quality metrics,
  citation precision, negative diagnostics, platform, commit SHA, and
  dirty-state flag.

## Resolved Decisions

- CLI query-variant flags are deferred beyond V1.
- `retrieval_guidance` is returned by default on context responses when
  `llmwiki_agent_guided_lexical_v1` is advertised.
- The V1 query cap is the primary `query` channel plus at most two supplied
  `query_variants`; the primary query may be empty only when variants are
  omitted or empty to preserve legacy overview behavior.
- Serve is the canonical source of the guidance schema; bridge interop maps it
  to camelCase and omits unknown or malformed guidance.
- Clean-commit benchmark threshold policy and future persisted derived
  orientation artifact consumption remain separately specified follow-on work.

## References

- Spec: `../../specs/agent-guided-lexical-retrieval/`
- Derived orientation lifecycle ADR:
  `2026-08-02-derived-orientation-index-artifact-lifecycle.md`
- Semantic vector retrieval ADR:
  `2026-08-01-optional-source-owned-semantic-vector-retrieval-boundary.md`
- Managed generic Markdown sidecar ADR:
  `2026-07-30-managed-generic-markdown-sidecar-boundary.md`
- Architecture: `../architecture.md`
