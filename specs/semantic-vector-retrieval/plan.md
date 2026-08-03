# Plan: Semantic Vector Retrieval

## Approach

Implement semantic retrieval as an optional provider-backed extension to the
existing search mode enum. The current surfaces remain the integration point:
`/query`, `/search`, MCP JSON-RPC `llmwiki_context` and `llmwiki_search`, MCP
Streamable HTTP tools, CLI `query/search --mode`, and Python
`LlmWikiService.context/search`.

The first implementation should be deliberately small:

- keep `lexical` and `literal` unchanged;
- add `vector` and `hybrid` mode values;
- add an internal `EmbeddingProvider` protocol;
- add only a local FastEmbed provider behind `llmwiki-serve[vector]`;
- always construct FastEmbed with an explicit `model_name`, default runtime
  model access to local-files-only, and allow network download only through
  explicit operator configuration;
- add exact cosine over deterministic page chunks;
- add a source-owned vector sidecar cache outside the served root;
- add LLMWiki-aware orientation-seeded hybrid v1 with fixed weighted RRF
  channels;
- keep plain lexical+vector RRF as an internal benchmark baseline and exact
  no-orientation fallback;
- validate behavior with a deterministic fake provider in CI before running
  real model benchmarks.

No remote provider, vector database, ANN index, sentence-transformers runtime,
Redis vector index, reranker, answer synthesis, or new public endpoint belongs
in this slice.

The 2026-08-02 implementation release blockers are now closed by independent
code review plus hash-stable dirty-snapshot Windows and DGX engineering
validation. The opt-in semantic retrieval preview is a conditional PR/merge
candidate once all release-critical untracked files are intentionally included.
Clean-commit SHA Windows and DGX public benchmark reports and public
performance claims remain pending. No commit, push, publish step, release, or
public claim has occurred. Dirty-snapshot metrics are engineering evidence only
and do not support universal superiority, calibrated abstention,
large-corpus/ANN, SOTA, broad multilingual quality, or poisoning-safety claims.

## Affected Areas

- Source modules:
  - `src/llmwiki_serve/models.py`
  - `src/llmwiki_serve/search.py`
  - `src/llmwiki_serve/service.py`
  - `src/llmwiki_serve/api.py`
  - `src/llmwiki_serve/cli.py`
  - a new provider/cache module if implementation proceeds
- Optional packaging:
  - `pyproject.toml` optional dependency extra `vector`
  - `THIRD_PARTY_NOTICES.md` after FastEmbed/NumPy/model license review
- Tests:
  - service/API/MCP/CLI mode contract
  - fake provider vector/hybrid ranking
  - vector cache identity, corruption, draft isolation, and refresh
  - no-model-download/default-install checks
- Docs/contracts:
  - README
  - `docs/architecture.md`
  - `docs/openapi.json`
  - release checklist
  - this spec set and ADR
- Benchmark tooling:
  - full BEIR SciFact runner/report extension for lexical-English, vector,
    plain-RRF baseline, and orientation-seeded hybrid comparisons

## Workstreams

### 1. Contract And Configuration

- Extend `SearchMode` and CLI choices to `lexical|literal|vector|hybrid`.
- Preserve omitted mode as `lexical`.
- Replace permissive MCP mode normalization that maps unknown modes to lexical
  with strict recognition of the four public modes.
- Add provider startup/library configuration without provider construction when
  disabled.
- Add actionable errors for vector/hybrid requests with no provider.
- Keep HTTP/OpenAPI and MCP/FastMCP schemas allowed to statically accept
  `vector` and `hybrid`; do not require dynamic FastMCP schemas.
- Advertise runtime support with exact health/manifest/source-bundle/MCP
  capability strings: `llmwiki_retrieval_v1`,
  `llmwiki_search_mode_lexical`, `llmwiki_search_mode_literal`, and
  `llmwiki_search_mode_vector`/`llmwiki_search_mode_hybrid` only when the
  provider and index are usable.

### 2. Provider And Dependency Boundary

- Add `llmwiki-serve[vector]` with FastEmbed plus direct NumPy dependency,
  using tested bounded dependency ranges selected during implementation and
  committed through the project lockfile.
- Keep default install and default dev behavior dependency-light.
- Define local FastEmbed provider metadata: provider id, model id, revision,
  dimension, distance metric, dependency versions, and safe display label.
- Require explicit model selection/override support and pass `model_name`
  explicitly to FastEmbed in all cases.
- Default to local-files-only model access. Permit network model download only
  through an operator startup/service setting such as
  `--vector-model-download allow`.
- Keep provider, model, cache path, and download policy out of client
  query/search request payloads.

### 3. Chunking And Text Schema

- Implement `llmwiki-vector-text-v1` over current `WikiPage` fields.
- Derive heading spans from `WikiPage.text` because parser headings are stored
  as strings without locators.
- Use page title, heading breadcrumb, and paragraph body text.
- Exclude paths, source refs, full front matter, graph metadata, and local root
  from embedding text.
- Keep chunk limits conservative and deterministic across platforms.
- Map best chunk hits back to page-level snippets and citations.

### 4. Source-Owned Vector Cache

- Store sidecars outside the wiki root.
- Use OS-specific per-user default sidecar roots and reject any explicit cache
  path that resolves equal to or under the served wiki root.
- Key cache records by source scope, projection/content hash, visibility scope,
  provider id, model id, revision, dimension, text schema, index schema, and
  cache schema.
- Store no raw source text, snippets, raw queries, roots, model paths, or
  credentials.
- Use payload/checksum-first writes and manifest-last `os.replace` publication
  with checksummed manifests.
- Add per-cache identity locking/concurrency behavior and stale-lock
  corruption handling in the sidecar directory.
- Treat corruption and schema mismatch as misses.
- Keep approved-only and draft-inclusive indexes isolated.

### 5. Retrieval And Fusion

- Implement exact cosine first, with no ANN index.
- Collapse chunks to page-level results by best cosine.
- Use existing `SearchResult` shape.
- Implement public hybrid as orientation-seeded hybrid v1:
  - identify query-relevant canonical `hot`, `index`, and `overview`
    orientation pages from bounded lexical/vector ranks computed only over the
    orientation-page subset;
  - use at most three orientation seeds;
  - extract related page ids only from trustworthy existing links,
    `source_refs`, and tags visible in or near the matched orientation
    snippet/chunk;
  - strictly cap graph/source/tag expansion and keep approved-only and
    draft-inclusive scopes isolated;
  - avoid generic high-degree index boilerplate by ignoring relations that are
    not present in the matched orientation evidence window;
  - embed the query no more than once and reuse the query vector for
    orientation-subset, related-subset, and global-vector scoring;
  - score the safe related-page subset directly so related targets outside the
    global vector candidate depth remain eligible, while evaluating only the
    subset's eligible chunk record positions;
  - keep one global vector channel for recall safety after related-subset
    scoring;
  - include orientation-doc and graph-prior channels only when usable;
  - keep orientation seed pages in the low-weight orientation-doc channel for
    normal queries so they orient retrieval without dominating target evidence;
  - fall back exactly to plain lexical+vector RRF when no safe related set
    exists.
- Fuse public hybrid with fixed weighted RRF channels and constant `60`:
  lexical/exact `1.0`, related-vector `1.0`, global-vector `0.75`,
  orientation-doc `0.35`, graph-prior `0.25`.
- Apply exact identifier guards before hybrid fusion so vector-only approximate
  matches cannot weaken exact identifier behavior.
- Document mode-specific score semantics and avoid cross-mode score claims.
- Do not define public per-request `min_score` semantics for vector/hybrid;
  reject non-null legacy `min_score` for vector/hybrid and never apply it to
  RRF.
- Ensure managed-context recording counts vector and hybrid hits with their
  actual route.

### 6. Benchmark Evidence

- Add fake-provider tests to CI for deterministic coverage.
- Extend SciFact full-run tooling to compare:
  - `lexical-english`
  - `vector`
  - `plain-rrf` baseline
  - `hybrid` orientation-seeded v1
- Record quality metrics and resource telemetry on Windows local and
  Ubuntu/DGX.
- Record aggregate-only orientation diagnostics and vector reuse evidence.
- Add a 20-query warm performance smoke before full 300-query vector/hybrid
  runs to confirm provider/index reuse and latency shape.
- Record deterministic tie-breaking, cross-OS quality tolerance, resolved
  dependency/model/cache/chunk provenance, `languages_evaluated: [en]`, and
  `korean_quality: not_evaluated` for SciFact.
- Treat SciFact vector/hybrid output as candidate/baseline reporting, not an
  active release gate, until threshold policy is implemented.
- Keep Korean MIRACL-ko deferred until ADR #37/provenance requirements are
  settled.

### 7. Expert-Review Release Gates

The implementation release gates for cache-hit fallback, immutable
refresh/search snapshots, bounded exact scoring, provider identity,
cross-service cold-build retry, adversarial orientation, draft leakage,
identifier preservation, and negative/unanswerable diagnostics are closed by
independent code review plus hash-stable dirty-snapshot Windows and DGX
engineering validation. These dirty-snapshot results are engineering evidence
only. Clean-commit SHA Windows and DGX public benchmark reports remain pending
before any public vector/hybrid quality or performance claim.

## Risks

- Risk: users think installing `[vector]` changes default behavior.
  Mitigation: keep provider disabled by default and add tests that no model is
  imported or downloaded without explicit configuration.

- Risk: vector cache leaks semantic content despite not storing raw text.
  Mitigation: treat embeddings as sensitive derived data, store sidecars
  outside the source root, redact paths, and keep public reports aggregate-only.

- Risk: hybrid weakens exact identifier lookup.
  Mitigation: apply lexical exact-required document guards before RRF and add
  explicit identifier/version/source-ref tests.

- Risk: high-degree `index` pages push generic boilerplate into hybrid results.
  Mitigation: select query-relevant orientation seeds only, extract relations
  from the matched snippet/chunk window, cap expansion, and fall back to plain
  RRF when there is no safe related set.

- Risk: repeated vector queries reload cache payloads or rebuild model/index
  state and make benchmarks unusably slow.
  Mitigation: cache provider and loaded vector index records per unchanged
  projection/provider/visibility identity and add reuse instrumentation tests.

- Risk: model revision or license is ambiguous.
  Mitigation: block documented default model claims until exact revision,
  dependency versions, checksums where available, license evidence, and
  benchmarks are recorded.

- Risk: FastEmbed first-use model download surprises offline users.
  Mitigation: pass explicit `model_name`, default runtime access to
  local-files-only, and require an explicit operator download setting.

- Risk: vector score thresholds are tuned without evidence.
  Mitigation: do not ship a default vector minimum score; use benchmark data
  before adding any threshold configuration.

- Risk: cache writers race across processes.
  Mitigation: use atomic publish, checksum validation, and miss/rebuild
  behavior rather than trusting partial records.

- Risk: cache-hit records drop per-record vectors and crash if NumPy scoring is
  unavailable.
  Mitigation: fallback scoring reads from the loaded matrix or fails through a
  controlled vector error, with a forced-fallback regression.

- Risk: refresh mutates index state and projection signature separately.
  Mitigation: publish/read an immutable retrieval snapshot and cover concurrent
  refresh/search interleavings.

- Risk: near-full filtered NumPy scoring copies hundreds of MiB through
  advanced indexing.
  Mitigation: use blockwise/no-copy exact scoring with bounded top-k selection,
  or ship only with a documented tested-size envelope and guard.

- Risk: negative/unanswerable queries are misread as abstention capability.
  Mitigation: publish diagnostics only and explicitly state that v1 has no
  calibrated no-evidence threshold.

## Rollout

The spec, ADR, implementation, focused tests, full test suites, local release
checks, and dirty-snapshot Windows/DGX engineering validation have been run for
the opt-in preview. This is a conditional PR/merge candidate once all
release-critical untracked files are intentionally included.

Clean-commit SHA Windows and DGX public benchmark reports remain pending before
publication or public performance claims. ANN/vector-database scale, calibrated
abstention, universal superiority, SOTA, and broad multilingual claims remain
out of scope for this v1 preview.

## Rollback

Because lexical remains the default and vector/hybrid require explicit provider
configuration, rollback should remove or hide provider configuration, reject
`vector` and `hybrid` modes, and leave existing lexical/literal behavior
unchanged. Vector sidecars are disposable derived state and can be ignored or
deleted without source-tree cleanup.

## LLMWiki Ingestion Candidates

- `specs/semantic-vector-retrieval/`
- `docs/decisions/2026-08-01-optional-source-owned-semantic-vector-retrieval-boundary.md`
- Future sanitized SciFact vector/hybrid reports after data-safety review
