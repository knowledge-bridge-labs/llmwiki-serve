# Spec: Semantic Vector Retrieval

## Status

Draft / implementation branch. Runtime vector work exists in this worktree, but
no commit, push, publish step, release, or public claim has occurred.

The 2026-08-02 implementation release blockers are closed by independent code
review plus hash-stable dirty-snapshot Windows and DGX engineering validation.
The opt-in semantic retrieval preview is a conditional PR/merge candidate once
all release-critical untracked files are intentionally included. Clean-commit
SHA Windows and DGX public benchmark reports and public performance claims
remain pending. Dirty-snapshot metrics are engineering evidence only and do not
support universal superiority, calibrated abstention, large-corpus/ANN, SOTA,
broad multilingual quality, or poisoning-safety claims.

## Problem

`llmwiki-serve` currently serves local Markdown projections through lexical and
literal retrieval. That behavior is intentionally local-first, dependency-light,
and read-only. Operators also need an optional source-owned semantic retrieval
path for recall cases where lexical matching misses paraphrases, but adding
embeddings changes dependency, privacy, offline, cache, and benchmark posture.

The current code exposes `SearchMode = Literal["lexical", "literal"]` through
HTTP `QueryRequest.mode`, MCP JSON-RPC and Streamable HTTP tool arguments, CLI
`--mode`, and `LlmWikiService.context/search`. Any semantic retrieval work must
extend that existing mode enum instead of creating a parallel endpoint or tool.

## Goals

- Keep existing lexical, literal, default install, default runtime behavior, and
  result payload shape unchanged unless an operator explicitly enables a vector
  provider.
- Add `vector` and `hybrid` values to the current search mode enum across HTTP,
  MCP JSON-RPC, MCP Streamable HTTP, CLI, and Python service calls.
- Use the existing `/query`, `/search`, `llmwiki_context`, `llmwiki_search`,
  `query`, and `search` surfaces. Do not add a new endpoint or MCP tool.
- Keep result and citation shape compatible with current `SearchResult` fields:
  `page_id`, `title`, `path`, `score`, `snippet`, `role`, `source_refs`, and
  `route`.
- Add an optional `llmwiki-serve[vector]` extra for local FastEmbed support with
  a direct NumPy dependency for deterministic exact cosine scoring.
- Require no model download, model import, embedding index build, or vector
  cache write unless a vector provider is explicitly configured.
- Define an `EmbeddingProvider` protocol and ship only a local FastEmbed
  provider in the first implementation slice.
- Use deterministic paragraph and heading-aware chunks with stable identity,
  page-level citations, and page-compatible snippets.
- Store vector cache sidecars outside the served wiki root, without raw source
  text, and treat embeddings and chunk metadata as sensitive derived data.
- Validate any explicit FastEmbed model cache directory as outside the served
  wiki root before provider construction or model download.
- Extend the full SciFact benchmark path to compare lexical-English, vector,
  plain-RRF baseline, and LLMWiki-aware orientation-seeded hybrid retrieval on
  Windows and Ubuntu/DGX before any public quality claim.

## Non-Goals

- Do not change default `lexical` ranking or default `literal` exact-substring
  behavior.
- Do not add hosted RAG, a vector database, ingestion jobs, model answer
  synthesis, chat memory, prompt cache, or cross-source orchestration.
- Do not add a new HTTP endpoint, MCP tool, or CLI command for semantic search.
- Do not add remote OpenAI embeddings, vLLM embeddings, Redis vectors,
  RedisVL, ANN/HNSW/FAISS, sentence-transformers runtime, cross-encoders, or
  rerankers in the first implementation.
- Do not claim Korean retrieval quality from a model name containing
  `multilingual`; Korean evidence requires a separate benchmark path. The
  NoMIRACL-ko judged-pool adapter may provide limited Korean smoke evidence,
  but it is not a full MIRACL-ko corpus run, not a headline recall result, and
  not an abstention-threshold claim.
- Do not expose local model cache paths, source roots, secrets, raw embeddings,
  raw cache keys, or private text in network status, diagnostics, reports, or
  public docs.
- Do not create, write, rewrite, or modify `hot`, `index`, `overview`, or
  `quickstart` pages. Orientation pages are read-only retrieval hints.
- Do not claim calibrated abstention, reliable no-evidence thresholds, ANN
  scale, reranker quality, GraphRAG behavior, source-file rewriting, or
  large-corpus/vector-database performance in this v1 architecture.

## Requirements

- `REQ-VEC-001`: Default `pip install llmwiki-serve`, default
  `uv sync --extra dev`, default `serve`, default CLI `query/search`, default
  HTTP/MCP requests, and default Python `LlmWikiService` calls continue to use
  existing lexical/literal behavior with no vector dependency or model access.
- `REQ-VEC-002`: `SearchMode` is extended from `lexical|literal` to
  `lexical|literal|vector|hybrid` across `models.py`, API request schemas,
  MCP JSON-RPC argument parsing, MCP Streamable HTTP tools, CLI
  `SearchModeChoice`, and Python service methods. Public HTTP/OpenAPI and
  MCP/FastMCP tool schemas may statically accept all four mode values; runtime
  capability metadata and actionable disabled-provider errors indicate whether
  vector and hybrid are configured and usable for a given server instance.
- `REQ-VEC-003`: No new HTTP endpoint, MCP tool, or result schema is added for
  semantic retrieval. Existing query/search surfaces carry the selected mode.
- `REQ-VEC-004`: Existing `mode=literal` remains exact substring lookup and is
  never routed through embeddings, hybrid fusion, or semantic fallback.
- `REQ-VEC-005`: Existing `mode=lexical` remains unchanged unless a separate
  future lexical spec changes it.
- `REQ-VEC-006`: If `mode=vector` or `mode=hybrid` is requested while no vector
  provider/index is usable, HTTP returns an actionable 4xx error, MCP returns
  an actionable invalid-parameter/tool error, and CLI exits nonzero with
  recovery instructions. Unknown modes are rejected. MCP/JSON-RPC handlers and
  tools must not silently normalize unknown or disabled modes to lexical.
- `REQ-VEC-007`: Health, manifest, source-bundle, and MCP metadata use exact
  capability strings. Base retrieval advertises `llmwiki_retrieval_v1`,
  `llmwiki_search_mode_lexical`, and `llmwiki_search_mode_literal`. Vector and
  hybrid add `llmwiki_search_mode_vector` and `llmwiki_search_mode_hybrid` only
  when the configured provider and vector index are usable. Advertised fields
  must not include local roots, local model paths, raw cache locations,
  credentials, or secrets.
- `REQ-VEC-008`: `llmwiki-serve[vector]` installs local FastEmbed support plus
  a direct `numpy` dependency. FastEmbed and NumPy remain absent from the
  default install unless another already-approved dependency path adds them.
  The implementation slice must choose tested bounded dependency ranges and
  lock them; this spec does not invent unverified version bounds.
- `REQ-VEC-009`: Installing `llmwiki-serve[vector]` alone does not download a
  model. The implementation must avoid provider construction and model-cache
  access until an operator explicitly enables the vector provider. Runtime
  model access defaults to `local_files_only`; network model download is
  allowed only through an explicit operator setting such as
  `--vector-model-download allow`.
- `REQ-VEC-010`: The first provider is local FastEmbed only. The provider
  contract records provider id, model id, resolved model revision, dimension,
  distance metric, text schema id, and embedding dtype/normalization evidence.
- `REQ-VEC-011`: The initial local model candidate is
  `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, but a release
  implementation must pin and record the exact model revision, dependency
  version, artifact checksums when available, license evidence, dimension, and
  benchmark results before making it a documented default. The FastEmbed
  provider always receives an explicit `model_name` and never relies on a
  FastEmbed library default. Operators can override the model explicitly
  through startup/service configuration only; client search requests cannot
  select provider, model, cache path, or download policy.
- `REQ-VEC-012`: Korean quality must not be claimed from the model's
  multilingual label. A pinned NoMIRACL-ko judged-pool adapter may be used for
  Korean smoke evidence with explicit limitations. Full MIRACL-ko corpus
  recall, NoMIRACL abstention thresholds, and public headline Korean quality
  claims remain deferred until provenance, licensing, and release-gate policy
  are accepted.
- `REQ-VEC-013`: The embedding interface is a protocol, not a hard dependency
  on FastEmbed internals. It supports embedding document chunk texts and query
  text, returns fixed-dimension numeric vectors, and exposes safe metadata.
- `REQ-VEC-014`: Vector search uses exact cosine over the current source-owned
  vector cache in the first implementation. ANN and approximate indexes are
  follow-ups only.
- `REQ-VEC-015`: Chunking is deterministic, paragraph-aware, and
  heading-aware. The same projection, text schema, and provider metadata produce
  the same chunk ids and vector-cache identity on Windows and Ubuntu.
- `REQ-VEC-016`: Public results remain page-level. The best matching chunk may
  choose the snippet, but citations remain the owning page's existing
  `source_refs` and no public chunk id field is required in the initial
  response shape.
- `REQ-VEC-017`: The vector cache is a sidecar outside the served wiki root.
  The source folder remains read-only: no generated embeddings, marker files,
  chunk manifests, or model metadata are written under the wiki root.
- `REQ-VEC-017A`: An explicit FastEmbed model cache directory is resolved with
  the same source-root boundary as the vector sidecar and rejected when it is
  equal to or nested under the served wiki root. This validation runs before
  provider construction or any operator-approved model download.
- `REQ-VEC-018`: The vector cache stores no raw source text, no snippets, no
  raw queries, no local roots, no model local paths, no credentials, and no
  raw provider responses. Cache schema `llmwiki-vector-cache-v2` stores
  float32 embeddings in checksum-named `.npy` sidecars and compact JSON
  metadata containing only stable page/chunk ids, locators, schema ids,
  provider identity, and content hashes required for retrieval. Vector norms
  are derived from the loaded matrix instead of persisted as JSON.
- `REQ-VEC-019`: Vector cache identity includes at least source scope,
  projection/content hash, embedding provider id, model id, resolved revision,
  embedding dimension, distance metric, text schema id, index schema id,
  visibility scope, and cache schema version.
- `REQ-VEC-020`: The cache identity prevents cross-root collisions without
  exposing raw local paths. If a root fingerprint is needed, it must be local
  and redacted, such as a salted digest stored in sidecar state.
- `REQ-VEC-021`: Draft-inclusive and approved-only indexes are isolated by
  visibility scope. A draft-inclusive cache record must never satisfy an
  approved-only network request.
- `REQ-VEC-022`: Cache writes are atomic from readers' perspective. Readers see
  either the previous valid record or the next valid record, never a partial
  manifest/vector file pair.
- `REQ-VEC-023`: Corrupt, incomplete, schema-mismatched, provider-mismatched,
  dimension-mismatched, projection-mismatched, or visibility-mismatched vector
  cache records are misses. The service rebuilds when the provider is available
  or returns an actionable provider/cache error when it cannot rebuild.
- `REQ-VEC-024`: Refresh and invalidation follow the existing projection
  freshness boundary. Source changes, adapter marker changes, graph sidecar
  changes, producer-manifest-approved projection changes, text schema changes,
  provider/model changes, and index schema changes produce a new cache identity.
- `REQ-VEC-025`: Public hybrid mode is LLMWiki-aware orientation-seeded hybrid
  v1, not plain lexical/vector RRF. Plain lexical+vector RRF remains available
  only as an internal benchmark baseline. Public hybrid uses fixed weighted
  RRF-style channels with constant `60`: lexical/exact `1.0`,
  related-vector `1.0`, global-vector `0.75`, orientation-doc `0.35`, and
  graph-prior `0.25`. These constants are documented design constants, not
  qrel-tuned knobs in this implementation slice.
- `REQ-VEC-025A`: Public hybrid and the benchmark-only plain-RRF baseline use
  a bounded candidate depth computed only from requested `limit` and visible
  document count: `min(total_docs, max(limit, min(1024, max(256, 4 * limit))))`.
  The `1024` cap limits automatic overfetch beyond the requested result count;
  it does not reduce an explicitly larger Python benchmark/request limit below
  the requested value. Candidate depth must not depend on qrels, benchmark
  labels, query ids, corpus ids, or evaluation metrics.
- `REQ-VEC-026`: Exact and literal identifier behavior must not be weakened.
  Literal mode stays separate. For single-token exact compound or metadata
  identifier queries under the English lexical analyzer, hybrid must apply the
  same exact-required document guard before fusion, so vector-only approximate
  matches cannot satisfy or outrank exact identifier evidence.
- `REQ-VEC-027`: Score semantics are mode-specific and documented. Lexical and
  literal scores keep current meanings, vector score is exact cosine for the
  best page chunk, and hybrid score is the final RRF sum. Callers must not
  compare scores across modes as calibrated probabilities.
- `REQ-VEC-028`: Do not expose public per-request minimum-score semantics for
  vector or hybrid in the first implementation slice. If the existing request
  `min_score` field remains, it is legacy lexical/literal behavior; non-null
  `min_score` with vector/hybrid must be rejected as unsupported and must never
  be applied to hybrid RRF. A server-side vector threshold may exist only as
  operator configuration if retained, with no default gate until benchmark
  evidence justifies it.
- `REQ-VEC-029`: Empty-query behavior remains compatible. Vector and hybrid
  mode must not use embeddings to invent broad overview results for a
  non-empty query whose provider cannot produce valid candidates.
- `REQ-VEC-030`: All HTTP, MCP, CLI, Python, OpenAPI, README, architecture, and
  release documentation impacts must be updated in the implementation slice.
  Generated OpenAPI must reflect the expanded mode enum.
- `REQ-VEC-031`: CI uses a deterministic fake embedding provider for chunking,
  cache, exact cosine, error handling, draft isolation, and hybrid RRF tests.
  CI must not download models. Clean base-install smoke must prove FastEmbed
  and NumPy are not default dependencies; vector tests run in declared
  dev+vector environments.
- `REQ-VEC-032`: Full SciFact benchmarking is extended to compare
  lexical-English, vector, benchmark-only plain-RRF, and hybrid modes on
  Windows local and Ubuntu/DGX using the same official full SciFact
  materialization and public-safe report rules.
- `REQ-VEC-033`: Hybrid orientation seeds come only from query-relevant pages
  whose approved canonical page role is `hot`, `index`, or `overview`. Root
  `quickstart.md` is eligible only when the parser classifies it as the
  canonical `index` role; nested topic `quickstart.md` pages are not eligible
  by name alone. Select at most three seeds deterministically from lexical
  and/or vector ranks computed over the bounded orientation-page subset, not
  from global vector top-N membership. Never prepend full orientation page
  content to the query.
- `REQ-VEC-034`: Related candidate page ids are expanded only from trustworthy
  existing links, `source_refs`, and tags visible in or near the matched
  orientation snippet/chunk, with strict caps and approved/draft isolation. Do
  not arbitrarily take the first links from a high-degree index page. If no
  safe related set exists, public hybrid falls back exactly to the existing
  plain lexical+vector RRF ordering and bytes.
- `REQ-VEC-035`: Public hybrid embeds the query at most once per request and
  reuses that query vector for orientation-page scoring, related-subset
  scoring, and the global vector recall channel. A safely related target
  remains eligible even when it is outside the global vector candidate depth.
  Orientation and related-subset vector searches must score only eligible
  chunk record positions for their requested page subset, not score the full
  corpus and filter afterward. Orientation-doc and graph-prior channels are
  included only when usable.
- `REQ-VEC-036`: Vector provider instances and loaded vector cache records are
  reused across repeated queries for an unchanged projection. Query execution
  must not reconstruct the provider, recompute the full cache checksum, reload
  the vector payload from disk, rechunk, reindex, or make repeated large vector
  copies unless refresh/content/provider/visibility invalidation requires it.
- `REQ-VEC-036A`: Hybrid query execution must not materialize full-corpus
  `SearchResult` lists just to return top-k results. Lexical, vector, related,
  orientation, and graph-prior channels must use the bounded candidate depth or
  stricter channel-local caps before fusion.
- `REQ-VEC-037`: Benchmark diagnostics for orientation and vector reuse are
  aggregate-only. Public per-result response schema is not expanded.
- `REQ-VEC-038`: A separate curated LLMWiki orientation mechanism benchmark
  uses synthetic public-safe Markdown to validate hot/index/overview-first
  related-vector behavior, boilerplate resistance, exact identifier
  preservation, exact no-orientation plain-RRF fallback, and approved-only
  draft isolation. It is labeled as a non-authoritative functional mechanism
  benchmark, not an external retrieval-quality or language-quality headline.
- `REQ-VEC-039`: Release blocker: cache-hit vector search must not crash when
  a loaded cache record has an empty in-memory `record.vector` value and the
  NumPy scoring path is unavailable or deliberately forced to fall back. The
  fallback scorer must use the loaded matrix row, skip invalid rows, or raise a
  controlled redacted `VectorSearchError`; raw `ValueError` or sequence-length
  crashes are hard failures.
- `REQ-VEC-040`: Release blocker: refresh and search must use an immutable
  retrieval snapshot. A vector/hybrid request must never pair a newly refreshed
  `WikiIndex` with a stale projection signature or vector-cache identity.
  Refresh publishes the index and projection signature together, and search
  reads them together.
- `REQ-VEC-041`: Required before performance claims: exact scoring over
  filtered or near-full candidate sets must avoid unbounded NumPy advanced
  indexing copies of the vector matrix. The implementation must either use
  no-copy/blockwise exact scoring with bounded top-k selection, or enforce and
  document a tested supported-size envelope with an explicit guard and memory
  regression. Small orientation/related subsets may continue using bounded
  subset scoring.
- `REQ-VEC-042`: Cache identity must include provider artifact/version
  stability evidence for the shipped public provider. The v1 public provider is
  pinned local FastEmbed only. If custom embedding providers are not fully
  fingerprinted in this slice, custom provider artifact stability is an
  explicit non-goal and tests must prove identity changes for provider/model/
  revision/version metadata changes.
- `REQ-VEC-043`: Required release gates cover cache-hit NumPy-failure fallback,
  refresh/search concurrency, cross-service cold-build retry behavior,
  exclude-one and near-full filtered memory paths, poisoned orientation text,
  stale/deleted related targets, high-degree hub orientation pages,
  prompt-like orientation content, malicious tag/source-ref relations, draft
  leakage, negative/unanswerable diagnostics, and Korean/English/Unicode
  identifiers.
- `REQ-VEC-044`: Negative and unanswerable query evaluation is diagnostic only
  in v1. Reports may show false-positive, top-k, score-separation, and
  citation-precision diagnostics, but public docs and release notes must not
  claim calibrated abstention or a reliable no-evidence threshold.
- `REQ-VEC-045`: Supported-size claims are limited to validated small/team
  corpora and the exact document/chunk counts, vector dimensions, memory
  envelope, and platforms recorded in release reports. Corpus-scale claims
  beyond that envelope are deferred to a separate ANN or vector-database
  experiment.
- `REQ-VEC-046`: Any public quality or performance report for vector/hybrid
  must be rerun from a clean commit SHA on Windows and Ubuntu/DGX. Dirty
  worktree reports remain engineering evidence only and must not be used as
  public release proof.

## Provider Contract

The implementation should define an internal protocol equivalent to:

```python
class EmbeddingProvider(Protocol):
    provider_id: str
    model_id: str
    model_revision: str
    dimension: int
    distance_metric: Literal["cosine"]

    def embed_documents(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...
    def embed_query(self, text: str) -> Sequence[float]: ...
```

The concrete protocol can be synchronous at first because current service
retrieval is synchronous. If batching or async provider calls are needed later,
that is a provider-level follow-up and not a public surface change.

Provider metadata used in cache identities and reports must be normalized and
safe. If an operator uses a local model path override, network responses and
public reports must show only a sanitized label or digest, not the path.

The FastEmbed implementation must always pass an explicit `model_name`. It
must not rely on FastEmbed's package default model. Startup/service
configuration owns provider id, model id, cache location, and download policy;
HTTP, MCP, CLI query/search, and Python per-call request payloads do not accept
provider/model/cache/download overrides.

The vector extra must be implemented with bounded dependency ranges selected
from actual compatibility testing and committed through the project lockfile.
Release and benchmark reports record resolved FastEmbed, NumPy, Python,
platform, package, model, and cache-schema versions, but this spec-only slice
does not assert version bounds before they are tested.

## Capability Metadata

Health, manifest, source-bundle, and MCP metadata use a common capability
vocabulary:

- `llmwiki_retrieval_v1`
- `llmwiki_search_mode_lexical`
- `llmwiki_search_mode_literal`
- `llmwiki_search_mode_vector`
- `llmwiki_search_mode_hybrid`

The first three capabilities describe the baseline retrieval surface and are
present when normal lexical/literal retrieval is available. The vector and
hybrid capability strings are present only when the configured provider is
usable and the matching vector index is valid or rebuildable. Capability output
is runtime status, not the public schema definition; FastMCP and OpenAPI
schemas may statically list `vector` and `hybrid`, and disabled requests still
fail actionably.

## Text Schema And Chunking

The current parser stores `WikiPage.text`, `title`, `summary`, `role`,
`source_refs`, `tags`, and a list of heading strings. It does not store section
spans. Therefore text schema `llmwiki-vector-text-v1` must derive chunk spans
from `WikiPage.text` itself instead of assuming `WikiPage.headings` contains
enough locator information.

Initial text schema:

- Embed approved page chunks by default; draft chunks are built only for the
  draft-inclusive visibility scope.
- Use `page.title`, the active Markdown heading breadcrumb, and paragraph body
  text as the embedding input.
- Exclude local root, path, `source_refs`, full front matter, graph metadata,
  and raw source-reference labels from embedding text in v1.
- Treat tags as excluded in v1 unless implementation evidence shows they
  materially improve recall without identifier noise; any tag inclusion changes
  the text schema id.
- Normalize line endings to LF, strip front matter through the existing parser,
  collapse repeated whitespace in the chunk input, and preserve Unicode text.
- Split on Markdown headings and blank-line paragraphs. A heading prefix is
  applied to following paragraph chunks until the next heading.
- Keep each chunk input at or below `1,200` Unicode characters and a target of
  `180` whitespace-delimited terms before provider tokenization. The
  implementation may tighten these limits after inspecting the selected model's
  maximum sequence length, but it must not loosen them without a text schema
  revision.
- Long paragraphs are split deterministically at whitespace boundaries when
  possible and by Unicode character boundary otherwise.
- No semantic summaries, model-generated expansions, hidden orientation text,
  path terms, or source-reference terms are injected into chunk text.

Stable chunk identity includes text schema id, page id, page content hash,
heading breadcrumb hash, chunk ordinal, and chunk span locator. Public result
payloads do not need to expose chunk ids. Snippets are generated from the
current `WikiPage.text` using the best chunk locator and fall back to existing
snippet behavior when the locator is unavailable.

## Ranking And Scores

Vector mode:

- Embed the query text with the configured provider.
- Compare against all eligible chunk vectors with exact cosine.
- Collapse chunk hits to page-level results using the best chunk cosine per
  page, with deterministic tie-breaks by score, page role rank, path, and
  `page_id`.
- Return current `SearchResult` objects with `route="vector"` or another
  documented string value inside the existing `route` field.

Hybrid mode:

- Build lexical candidates using the current lexical search path.
- Select query-relevant orientation seeds first from only canonical `hot`,
  `index`, and `overview` role pages. Seed ranking is deterministic and
  bounded by lexical and/or vector scoring over the orientation-page subset,
  independent of global vector top-N membership; at most three seeds are used.
- Extract related page ids from trustworthy existing links, `source_refs`, and
  tags present in or near the matched orientation snippet/chunk. Expansion is
  strictly capped and visibility-scoped. High-degree orientation boilerplate
  must not dominate candidate selection.
- Embed the query no more than once, then score the safe related-page subset
  directly against the reused query vector over only the subset's eligible
  chunk record positions.
- Build global vector candidates as a separate recall-safety channel using the
  same query vector.
- Fuse fixed channels with `1 / (60 + rank)`: lexical/exact `1.0`,
  related-vector `1.0`, global-vector `0.75`, orientation-doc `0.35`, and
  graph-prior `0.25`.
- For normal non-identifier queries, orientation seed pages contribute through
  the low-weight orientation-doc channel rather than dominating lexical/global
  evidence. For guarded exact identifier queries, exact lexical semantics still
  take priority.
- If no safe related set exists, return byte/order-equivalent plain
  lexical+vector RRF results. Plain RRF is not the final public hybrid design;
  it is the no-orientation fallback and benchmark baseline.
- Record aggregate diagnostics for orientation availability/use, seed count,
  related count, fallback reason, source relation capabilities, and vector
  reuse. Do not expand the per-result public response schema for these
  diagnostics.
- Apply exact-required identifier filtering before fusion when the lexical path
  classifies the query as an exact single compound or metadata lookup.
- Return page-level `SearchResult` objects with final RRF score and a snippet
  from the highest-ranked contributing route, preferring exact lexical evidence
  for guarded identifier queries.
- Apply deterministic final tie-breaks by fused score, exact lexical evidence
  presence, page role rank, path, and `page_id`.

Scores are not probabilities. A lexical score, literal score, cosine score, and
RRF score are comparable only within their own mode and implementation
revision. Public docs and OpenAPI examples must say this directly.

Managed-context recording must include vector and hybrid hits with their
actual `route` value, just as lexical and literal hits are recorded today. A
recorded semantic hit must not be relabeled as lexical for compatibility.

## Vector Cache Boundary

The vector cache is a local derived sidecar, not a source page, not a projection
store, not Redis, not a vector database, and not host-agent memory. It must live
outside the served wiki root and be safe to delete. Deleting it can slow the
next vector request but cannot delete source data.

Default sidecar roots are per-user, OS-specific cache/state locations:

- Windows: `%LOCALAPPDATA%\llmwiki-serve\vector-cache`
- macOS: `~/Library/Caches/llmwiki-serve/vector-cache`
- Linux and other XDG platforms:
  `$XDG_CACHE_HOME/llmwiki-serve/vector-cache`, falling back to
  `~/.cache/llmwiki-serve/vector-cache`

An explicit cache path must be resolved to an absolute path and rejected when
it is equal to or nested under the served wiki root. This rejection is a
configuration error; the service must never quietly move vector cache writes
under the source tree.

Cache records use an index manifest plus checksum-named sidecars. The manifest
stores cache schema, provider metadata, text schema, index schema, visibility
scope, projection/content identity, chunk count, dimension, creation time,
package version, and a per-sidecar record for each vector/metadata file. The
vector sidecar stores only a float32 `.npy` matrix. The metadata sidecar stores
stable page/chunk ids, locators, and hashes. Neither sidecar stores raw source
text.

Atomicity requirements:

- Write to a temporary path in the same sidecar directory.
- Write and flush/close vector and metadata sidecars first.
- Publish sidecars with checksum-derived filenames, then write a temporary
  manifest that references sidecar checksum, shape, dtype, schema,
  provider/content identity, and visibility identity.
- Publish the manifest last with `os.replace`; readers trust only a valid
  manifest whose referenced sidecars validate.
- Treat stale lock files, partial writes, missing sidecars, checksum mismatch,
  malformed `.npy`, wrong shape/dtype, old cache schema, and JSON parse errors
  as cache misses.

Concurrency requirements:

- Use a per-cache identity lock or equivalent advisory lock in the sidecar
  directory for builders; locks are never created under the served root.
- Multiple processes may race to build the same vector cache. The winner
  publishes a complete record; losers can reuse or replace only after validating
  identity and checksums.
- Readers must not hold the source tree mutable and must never write under the
  served root.
- A long-running server refresh that detects a new projection generation uses a
  new cache identity and does not mix old vectors with new pages.

## Failure Behavior

- Provider disabled plus `vector` or `hybrid`: actionable user error, no
  silent lexical fallback.
- Optional extra missing: actionable install error naming
  `pip install "llmwiki-serve[vector]"`.
- Model unavailable/offline: actionable provider error explaining whether the
  model must be pre-cached or explicit operator download must be allowed. The
  default is local-files-only/offline; client requests cannot relax it.
- Dimension mismatch: cache miss if cached, provider error if newly produced
  vectors have inconsistent dimensions.
- Empty corpus or no eligible chunks: return an empty result set with existing
  context limitations, not a crash.
- Corrupt cache: ignore and rebuild if possible; otherwise return an actionable
  error without exposing local paths or cache keys.
- Provider exception during a request: 4xx only for user/configuration errors;
  controlled 5xx/MCP internal error for unexpected runtime failures, redacted
  of paths and secrets.

## Benchmark Extension

The benchmark adapter layer already publishes full BEIR SciFact Windows and
DGX Spark Ubuntu reports for lexical-English. This feature extends that path,
without changing the corpus, queries, qrels, or external reference rows, to
compare:

- `lexical-english`: current `mode=lexical` with `analyzer_profile=english`.
- `vector`: `mode=vector` with the pinned local FastEmbed provider/model.
- `plain-rrf`: an internal lexical+vector fixed-RRF baseline.
- `hybrid`: `mode=hybrid` with LLMWiki-aware orientation-seeded hybrid v1 and
  fixed weighted RRF channels.

Reports must include primary BEIR-comparable nDCG@10 and Recall@100,
product-secondary Recall@5, Hit@5, MRR@10, index/chunk build time, embedding
build time, vector cache hit/miss status, model id/revision, dimension, chunk
count, chunking/text schema id and parameters, vector cache identity digest,
vector cache bytes, aggregate orientation diagnostics, vector reuse evidence,
resolved dependency versions, package version, Python
version, platform, memory/resource telemetry where available, per-query search
latency p50/p95, and payload bytes p50/p95. Windows and Ubuntu/DGX
deterministic quality metrics should match within absolute tolerance `1e-9`
for the same corpus/query/qrel/model/cache/chunk artifacts; resource telemetry
may differ by environment. Reports for SciFact must set
`languages_evaluated: [en]` and `korean_quality: not_evaluated`.

CI must use a deterministic fake provider that generates stable vectors without
network, model downloads, or FastEmbed. Full FastEmbed/SciFact runs are manual
candidate/baseline reports until dependency, model-cache behavior, and a
threshold/gating policy are accepted; SciFact vector/hybrid metrics are not an
active release gate in this first slice.

NoMIRACL-ko judged-pool evidence is allowed as a limited Korean smoke path when
the adapter verifies the official `miracl/nomiracl` revision
`ecd08778d0426a5ca28ac99763b0c9ddc2c78e68`, Apache-2.0 metadata, file
digests, and count invariants. The default materialized evaluation scope is all
213 `dev.relevant` queries plus a deterministic 213-query `dev.non_relevant`
sample, with only the unique documents referenced by those selected qrels.
Reports must label the result as `protocol: judged_pool`,
`full_corpus: false`, include both official full Korean corpus counts and
actual evaluation pool counts/checksums, use `languages_evaluated: [ko]`, and
state that there is no full MIRACL-ko corpus/headline recall claim and no
abstention-threshold claim. Full MIRACL-ko remains a deferred full-evidence
benchmark.

The curated orientation mechanism benchmark is separate from SciFact and
NoMIRACL-ko. It lives under `benchmarks/orientation_mechanism/fixture`, uses
fixed English/Korean query IDs and qrels over synthetic public-safe Markdown,
runs deterministic fake-provider unit tests, and can produce a real cached
FastEmbed runtime report with `model_download=never`. Its metrics are for
mechanism regression only.

The adversarial orientation extension of that fixture must include poisoned
orientation prose, prompt-like content, stale links, deleted targets,
high-degree hub pages, malicious tag/source-ref relations, draft/private pages,
and Korean/English/Unicode identifiers. The expected outcome is robust
candidate selection, draft isolation, exact no-orientation fallback when the
related set is unsafe, and diagnostic recording rather than a claim that
orientation is poisoning-safe.

Negative/unanswerable benchmark rows are required as diagnostics before
release. They must measure false-positive top-k behavior, citation precision,
score separation, and no-evidence wording, but they must explicitly state that
v1 has no calibrated abstention threshold.

## Compatibility

- HTTP request shape remains `QueryRequest`; only the `mode` enum expands.
- MCP JSON-RPC and Streamable HTTP tool names stay the same. Tool schemas may
  statically accept `vector` and `hybrid`; capability metadata and runtime
  errors communicate whether those modes are configured and usable. Dynamic
  FastMCP schemas are not required for this slice.
- CLI `query` and `search` keep `--mode` and add choices. CLI `serve` and
  Python app/service configuration add provider/cache options only in the
  implementation slice.
- Search result response shape remains compatible. New `route` string values
  are allowed because `route` is already a string field.
- Existing clients that omit `mode` or request `lexical`/`literal` keep current
  behavior.
- Generated OpenAPI changes are additive for enum values and any new
  capability/status fields.
- A release version bump and docs updates are required when implementation
  lands; this spec phase does not edit package metadata.

## Data Safety

Vector sidecars and model caches are local sensitive derived state. Embeddings
can leak semantic information even without raw text. Operators should store the
sidecar in a per-user state directory or another access-controlled local cache
location and should not publish raw vectors, raw cache manifests, local cache
paths, or private model-cache diagnostics.

Public reports may include sanitized provider id, model id, resolved revision,
dimension, dependency versions, aggregate metrics, aggregate resource telemetry,
and cache byte counts. Public reports must not include raw source text, raw
queries beyond dataset policy, private paths, model local paths, endpoint URLs,
secrets, raw vectors, or snippets from private corpora.

## Acceptance Criteria

- Default lexical/literal behavior and default dependency footprint are proven
  unchanged.
- Public schemas may accept `vector` and `hybrid`; runtime execution succeeds
  only where provider configuration and a usable index exist.
- Provider-disabled vector/hybrid requests fail loudly and actionably.
- Health, manifest, source-bundle, and MCP metadata use the exact
  `llmwiki_retrieval_v1` and `llmwiki_search_mode_*` capability strings.
- FastEmbed is constructed only with an explicit `model_name`; runtime defaults
  to local-files-only model access, and network download requires explicit
  operator configuration.
- Vector cache records never store raw source text and never live inside the
  served root. Cache schema `llmwiki-vector-cache-v2` uses `.npy` vector
  sidecars plus compact JSON metadata.
- Explicit cache paths under the served root are rejected, and cache publish is
  sidecar/checksum first, manifest last with `os.replace`.
- Explicit model cache paths under the served root are rejected before provider
  startup.
- Draft-inclusive and approved-only vector indexes cannot be confused.
- Corrupt cache records are misses, not source evidence.
- Exact identifier and literal lookup behavior is preserved.
- Public hybrid uses LLMWiki-aware orientation-seeded hybrid v1 with fixed
  weighted RRF channels, documented score semantics, and exact no-orientation
  fallback to plain lexical+vector RRF.
- Orientation pages are read only. No retrieval path creates, writes, or
  modifies `hot`, `index`, `overview`, or `quickstart` files.
- Vector provider and loaded vector records are reused across repeated queries
  for an unchanged projection.
- Public per-request `min_score` is not defined for vector/hybrid, and legacy
  `min_score` is never applied to hybrid RRF.
- Vector/hybrid hits are recorded in managed context with their actual route.
- Fake-provider CI validates chunking, exact cosine, cache identity, cache
  invalidation, errors, and RRF in dev+vector environments without network or
  model downloads, while base-install smoke proves no default FastEmbed/NumPy
  dependency.
- Full SciFact Windows and Ubuntu/DGX candidate reports compare
  lexical-English, vector, benchmark-only plain-RRF, and hybrid with cross-OS
  tolerance, provenance, and `languages_evaluated: [en]` before any public
  quality claim.
- Korean evidence is limited to NoMIRACL-ko judged-pool smoke until full
  MIRACL-ko provenance and release-gate policy are resolved.
- Cache-hit NumPy-failure fallback, refresh/search immutable snapshot,
  exclude-one or near-full filtered exact scoring, adversarial orientation,
  draft leakage, and Korean/English/Unicode identifier regressions pass before
  release.
- Provider artifact/version identity behavior is tested for the shipped
  FastEmbed provider; custom provider artifact stability is either implemented
  or explicitly excluded from v1 public claims.
- Exact-scoring performance claims are limited to a tested supported-size
  envelope unless blockwise/no-copy top-k scoring is implemented and measured.
- Negative/unanswerable reports are diagnostic only and make no calibrated
  abstention claim.
- Public vector/hybrid quality and performance reports are rerun from a clean
  commit SHA on Windows and Ubuntu/DGX before publication.

## Open Questions

- Whether the first public model default should be withheld until the exact
  candidate revision/license/checksum/benchmark package is accepted.
- Whether a future server-side vector threshold is justified after SciFact
  threshold policy exists.
- Whether future diagnostics need a separate redacted vector-cache endpoint or
  whether health/manifest capabilities are enough.
- Whether tags should be included in a future text schema after benchmark
  evidence, given identifier noise risk.

## References

- ADR: `../../docs/decisions/2026-08-01-optional-source-owned-semantic-vector-retrieval-boundary.md`
- Architecture: `../../docs/architecture.md`
- Redis projection-store ADR:
  `../../docs/decisions/2026-07-22-redis-projection-store-derived-cache-boundary.md`
- Managed generic context ADR:
  `../../docs/decisions/2026-07-30-managed-generic-markdown-sidecar-boundary.md`
- Language-aware lexical analyzer ADR:
  `../../docs/decisions/2026-08-01-language-aware-lexical-analyzer.md`
- Benchmark adapter layer: `../benchmark-adapter-layer/`
- English lexical analyzer: `../english-lexical-analyzer/`
- Korean numeric search relevance: `../korean-numeric-search-relevance/`
