# Tests: Semantic Vector Retrieval

## Acceptance Criteria

- `REQ-VEC-001`: Default install, default CLI, default HTTP/MCP requests, and
  default Python service calls do not import FastEmbed, require NumPy for vector
  logic, download models, build vectors, write sidecars, or change
  lexical/literal results.
- `REQ-VEC-002` and `REQ-VEC-003`: `vector` and `hybrid` are accepted mode
  values on existing HTTP, MCP JSON-RPC, MCP Streamable HTTP, CLI, and Python
  surfaces without adding endpoints or tools.
- `REQ-VEC-004` and `REQ-VEC-005`: `literal` and `lexical` behavior remains
  backward-compatible.
- `REQ-VEC-006`: Provider-disabled `vector` and `hybrid` requests return
  actionable HTTP 4xx, MCP, and CLI errors, unknown modes are rejected, and no
  request falls back to lexical through normalization.
- `REQ-VEC-007`: Health/manifest/source-bundle or equivalent capability/status
  output uses exact `llmwiki_retrieval_v1`,
  `llmwiki_search_mode_lexical`, `llmwiki_search_mode_literal`,
  `llmwiki_search_mode_vector`, and `llmwiki_search_mode_hybrid` strings,
  advertises vector/hybrid only when configured and usable, and does not expose
  local roots, model paths, cache paths, secrets, or raw keys.
- `REQ-VEC-008` through `REQ-VEC-011`: Optional extra, provider metadata, model
  override, explicit FastEmbed `model_name`, pinned model revision, tested
  bounded dependency ranges, license evidence, dimension, local-files-only
  default, and operator-only download behavior are verified before release.
- `REQ-VEC-012`: Korean quality claims are absent when based only on a
  multilingual model label. NoMIRACL-ko judged-pool reports may be used as
  limited Korean smoke evidence only when they state no full MIRACL-ko corpus
  recall, no headline Korean quality claim, and no abstention threshold.
- `REQ-VEC-013` and `REQ-VEC-014`: Provider protocol and exact cosine behavior
  are covered by fake-provider tests.
- `REQ-VEC-015` and `REQ-VEC-016`: Chunking is deterministic and maps vector
  hits back to page-level result rows, snippets, and existing page citations.
- `REQ-VEC-017` through `REQ-VEC-024`: Vector cache sidecars are outside the
  source root, contain no raw source text, use complete identity metadata,
  isolate drafts, publish atomically, treat corruption as misses, and refresh
  on source/provider/schema changes.
- `REQ-VEC-017A`: Explicit FastEmbed model cache directories are resolved and
  rejected when equal to or nested under the served source root, including
  relative paths and symlink resolution where supported by the platform.
- `REQ-VEC-025` and `REQ-VEC-026`: Public hybrid uses LLMWiki-aware
  orientation-seeded hybrid v1 with fixed weighted RRF channels and preserves
  exact identifier behavior. Plain lexical+vector RRF is retained only as an
  internal benchmark baseline and exact no-orientation fallback.
- `REQ-VEC-027` and `REQ-VEC-028`: Score semantics are documented and tests
  prove non-null public `min_score` is rejected for vector/hybrid and legacy
  `min_score` is never applied to hybrid RRF.
- `REQ-VEC-029`: Empty and stopword-like queries do not create unexpected
  semantic overview results.
- `REQ-VEC-030`: Generated OpenAPI and docs reflect the implementation without
  changing unrelated contracts.
- `REQ-VEC-031`: CI uses deterministic fake provider tests and performs no
  network/model download.
- `REQ-VEC-032` through `REQ-VEC-037`: Full SciFact Windows and Ubuntu/DGX
  candidate reports compare lexical-English, vector, plain-RRF baseline, and
  orientation-seeded hybrid with cross-OS quality tolerance, deterministic
  tie-breaking, aggregate-only orientation diagnostics, provider/index reuse
  evidence, provenance, `languages_evaluated: [en]`, and
  `korean_quality: not_evaluated` before public quality claims. They are not
  active release gates until threshold policy is implemented.
- `REQ-VEC-039` and `REQ-VEC-040`: Cache-hit NumPy-failure fallback and
  refresh/search torn-snapshot regressions pass before release.
- `REQ-VEC-041`: Near-full filtered exact scoring either uses no-copy/blockwise
  bounded top-k selection or enforces a tested supported-size guard with memory
  regression evidence.
- `REQ-VEC-042`: Cache identity behavior includes FastEmbed package/provider
  artifact/version evidence, or custom provider artifact stability remains an
  explicit tested non-goal.
- `REQ-VEC-043`: Required gates cover cold-build retry behavior, adversarial
  orientation, draft leakage, and Korean/English/Unicode identifiers.
- `REQ-VEC-044`: Negative/unanswerable reports are diagnostic and include no
  calibrated abstention or no-evidence-threshold claim.
- `REQ-VEC-045` and `REQ-VEC-046`: Supported-size and public report claims are
  limited to validated clean-commit Windows and Ubuntu/DGX runs.

## Unit Tests To Add

- `SearchMode` enum includes exactly `lexical`, `literal`, `vector`, and
  `hybrid`.
- CLI `--mode` accepts the four public modes and rejects unknown values.
- HTTP `QueryRequest.mode` rejects unknown values through request validation.
- MCP JSON-RPC and Streamable HTTP mode parsing rejects unknown values instead
  of normalizing to lexical.
- MCP JSON-RPC invalid non-null `min_score` values reject with an
  invalid-parameter error instead of silently becoming absent.
- Provider-disabled vector/hybrid requests produce redacted actionable errors.
- Missing `[vector]` optional extra produces an install hint naming
  `llmwiki-serve[vector]`.
- Unsupported local-path or file-URI FastEmbed model identifiers are redacted
  in public errors.
- Fake provider returns deterministic fixed-dimension vectors and records safe
  provider metadata.
- Inconsistent vector dimensions are rejected.
- Query/document exact cosine is computed directly with NumPy or equivalent
  tested arithmetic and not delegated to an approximate index.
- Chunker derives heading spans from `WikiPage.text` and does not rely on
  `WikiPage.headings` as locators.
- Chunker produces stable ids for LF and CRLF equivalents, long paragraphs,
  heading changes, page id changes, and content changes.
- Chunk text excludes local root, path, source refs, full front matter, graph
  metadata, and raw source-reference labels.
- Empty pages and pages with only headings do not crash vector indexing.
- Page-level result collapse chooses the best chunk per page with deterministic
  tie breaks.
- Snippets are generated from current page text and never stored as raw text in
  the vector cache.
- Draft-inclusive and approved-only chunk sets have distinct cache identities.
- Approved-only requests cannot read draft-inclusive cache records.
- Cache manifest identity changes on projection/content, provider, model,
  revision, dimension, text schema, index schema, visibility scope, or cache
  schema changes.
- Corrupt JSON, checksum mismatch, missing sidecar, malformed `.npy`, wrong
  vector shape/dtype, old cache schema, wrong dimension, wrong provider, wrong
  visibility, and wrong projection records are treated as misses.
- Binary sidecar round-trip tests verify float32 `.npy` vectors load with
  `allow_pickle=False`, JSON metadata stores no raw text/vector/norm fields,
  manifest sidecar identity matches cache identity, partial draft sidecars are
  ignored by readers, and concurrent builders/readers do not corrupt source
  roots or cache records.
- Atomic write tests observe either old valid or new valid records, not partial
  manifests or sidecars.
- Plain RRF baseline uses exactly `1 / (60 + rank)` for lexical and vector
  candidate lists.
- Plain RRF baseline and public hybrid use bounded candidate depth
  `min(total_docs, max(limit, min(1024, max(256, 4 * limit))))`, independent of
  qrels or benchmark labels.
- Production hybrid passes the bounded candidate depth to lexical and vector
  channels and passes that same value through diagnostics/telemetry; tests
  assert channel call limits rather than wall-clock timing.
- Benchmark-only plain RRF reuses the service-owned `SearchCorpus` cache for
  repeated unchanged-projection queries, rebuilds after refresh, and avoids
  dict-to-Pydantic revalidation of vector result payloads.
- Small-corpus equivalence tests prove bounded candidates match full-corpus
  candidates when the corpus fits inside the documented candidate depth.
- Public hybrid uses fixed weighted RRF channels with `k=60`: lexical/exact
  `1.0`, related-vector `1.0`, global-vector `0.75`, orientation-doc `0.35`,
  and graph-prior `0.25`.
- Query-relevant canonical orientation role pages are deterministic, bounded,
  capped to three, and selected from the orientation-page subset before global
  vector candidates are considered.
- Nested topic `quickstart.md` pages are not eligible orientation seeds by name
  alone.
- Public hybrid never prepends full orientation page content to the query.
- A relevant hot/index link lifts a paraphrase target above the plain RRF
  ordering.
- A safe related target outside global vector candidate depth is directly
  scored by related-vector retrieval, recovered by production hybrid, and uses
  only one provider query embedding.
- Orientation and related subset vector searches score only eligible chunk
  record positions in both NumPy and pure-Python fallback paths.
- Boilerplate and high-degree index relations do not dominate hybrid top-k
  results.
- When no safe related set exists, hybrid output is byte/order-equivalent to
  plain RRF fallback output.
- `source_refs` and tags present in the matched orientation evidence window can
  expand the related set under strict caps.
- Hybrid exact-identifier guard filters vector-only approximate matches for
  single exact compound/version/source-ref queries.
- Approved-only hybrid cannot leak draft-only related pages or draft vector
  chunks.
- Hybrid output order is deterministic when channel scores tie.
- Loaded vector indexes are reused across repeated unchanged-projection
  queries; tests assert cache/read/build counters rather than fixed wall-clock
  thresholds.
- Lazy vector provider initialization constructs exactly one provider under
  concurrent first vector calls.
- Non-null public `min_score` is rejected for vector and hybrid modes, and
  legacy `min_score` is never applied to hybrid RRF.
- Cache-hit fallback test loads a v2 `.npy` sidecar cache where records have
  empty in-memory vectors, forces NumPy scoring failure or disablement, and
  verifies valid search output or a controlled redacted `VectorSearchError`
  with no raw `ValueError`.
- Refresh/search snapshot test forces a refresh interleaving and proves each
  search observes one immutable pair of `WikiIndex` and projection signature.
- Exclude-one and near-full filtered exact-scoring tests prove the implementation
  avoids unbounded advanced-indexing matrix copies, or trips the documented
  supported-size guard before copying.
- Provider identity tests prove FastEmbed package/provider artifact/version,
  model id, resolved revision, dimension, text schema, index schema, visibility
  scope, and cache schema changes produce separate cache identities.
- Custom provider tests, if custom providers remain supported internally, prove
  artifact/version instability cannot silently reuse a stale cache. If not
  supported, tests assert the custom provider artifact-stability non-goal.
- Adversarial orientation tests cover poisoned prose, prompt-like text,
  malicious tags/source_refs, stale links, deleted targets, high-degree hubs,
  and unsafe related sets that must fall back to plain RRF.
- Korean, English, and Unicode identifier tests prove exact identifier guards
  are preserved in hybrid mode and are not satisfied by vector-only approximate
  matches.
- Negative/unanswerable diagnostics tests verify false-positive top-k,
  citation-precision, and score-separation fields exist in reports while an
  explicit `abstention_threshold_claim: false` or equivalent flag is present.

## Integration Tests To Add

- CLI default `query/search` on `examples/sample-wiki` matches current lexical
  output with no provider configured.
- HTTP and MCP default requests match current lexical output with no provider
  configured.
- HTTP `mode=vector` and `mode=hybrid` return actionable 4xx when provider is
  disabled.
- MCP JSON-RPC and MCP Streamable HTTP return actionable tool errors when
  provider is disabled.
- CLI `query/search --mode vector|hybrid` exits nonzero with recovery text when
  provider is disabled.
- A fake-provider configured service returns vector and hybrid page-level
  results through HTTP, MCP JSON-RPC, MCP Streamable HTTP, CLI, and Python.
- A fake-provider configured service returns orientation-seeded hybrid results
  through the same public result shape with `route="hybrid"` and no per-result
  schema expansion.
- Vector/hybrid result objects remain compatible with current `SearchResult`
  fields and field projection.
- `include_drafts=true` remains ignored on network surfaces unless draft access
  is enabled, including vector/hybrid.
- Source changes create a new vector cache identity and do not reuse stale
  chunks.
- Producer-manifest freshness composes with vector cache exactly like the
  projection store: a stale trusted marker can authorize a stale projection, so
  docs and tests preserve that boundary.
- Unknown `mode` values are rejected consistently by HTTP, MCP JSON-RPC, MCP
  Streamable HTTP, CLI, and Python call paths.
- Capability/status output advertises exact capability strings accurately and
  redacts local paths.
- Cross-service cold-build retry integration proves a caller that hits an
  in-progress vector cache build receives a retryable vector/provider state,
  not lexical fallback or an unredacted internal crash.
- Runtime refresh/search integration proves HTTP/MCP/Python search cannot pair
  a newly refreshed index with a stale projection signature during concurrent
  calls.
- Clean-commit report integration records the commit SHA, dirty-state flag,
  package version, Python version, platform, model revision, and vector cache
  identity before any report is eligible for public evidence.

## Benchmark And Manual Checks

### CI Fake Provider

- Run fake-provider tests on Windows and Linux CI without FastEmbed, model
  downloads, or network access.
- Confirm fake provider reports deterministic metadata and fixed vectors.
- Confirm no cache files are written inside the served source root.

### Local FastEmbed Smoke

- Install `llmwiki-serve[vector]` in a non-release scratch environment.
- Enable local FastEmbed provider explicitly.
- Confirm disabled-provider and missing-model paths are actionable.
- Confirm model cache behavior follows the documented download/offline policy.
- Inspect only non-sensitive fixture cache manifests and confirm no raw text,
  local roots, model paths, secrets, raw vectors, or snippets are copied into
  public artifacts.

### Full SciFact

- Run a 20-query warm performance smoke before full vector/hybrid reports and
  verify provider construction, document embedding, payload load, and index
  build counters remain stable after warmup.
- Run full BEIR SciFact official test split on Windows local for:
  - `lexical-english`
  - `vector`
  - `plain-rrf`
  - `hybrid`
- Run the same full split on Ubuntu/DGX.
- Confirm corpus/query/qrel checksums, deterministic tie-breaking, and quality
  metrics are comparable across environments within the accepted tolerance.
- Report nDCG@10, Recall@100, Recall@5, Hit@5, MRR@10, index/chunk build time,
  embedding build time, cache hit/miss status, vector cache bytes,
  aggregate-only orientation diagnostics, vector provider/index reuse evidence,
  resolved FastEmbed/NumPy/Python/package versions, model revision,
  chunk/text schema provenance, memory or resource telemetry, search latency
  p50/p95, payload bytes p50/p95, `languages_evaluated: [en]`, and
  `korean_quality: not_evaluated`.
- Keep external BEIR paper BM25 and Anserini/Pyserini reference rows unchanged
  and clearly separate from llmwiki-serve runs.
- Unit-level runner coverage must prove `plain-rrf` executes the same internal
  plain lexical+vector RRF function used for no-orientation fallback and records
  `retrieval_mode: plain-rrf` without adding `plain-rrf` to public HTTP/MCP/CLI
  search modes.
- Add negative/unanswerable rows to release-candidate reports. Required fields
  include negative query count, false-positive rate at configured k values,
  citation precision, score-separation summary, and an explicit statement that
  no calibrated abstention threshold is claimed.
- Add adversarial orientation fixture rows for stale/deleted/high-degree/
  prompt-like/poisoned relation behavior and draft/private non-leakage. These
  rows are mechanism gates, not external retrieval-quality evidence.
- Add supported-size envelope output for exact scoring: document count, chunk
  count, vector dimension, matrix bytes, scoring mode, memory guard if any,
  platform, and commit SHA.

## Hard-Fail Tests

- Any vector cache write under the served source root.
- Any explicit FastEmbed model cache directory under the served source root.
- Any raw source text, snippet, raw query, local root, model path, provider
  secret, or raw cache key in a vector cache manifest intended for publication,
  network response, or public report.
- Any default install path that imports FastEmbed or downloads a model.
- Any requested vector/hybrid mode silently falling back to lexical.
- Any draft page retrieved through approved-only vector/hybrid mode.
- Any hybrid result where a vector-only approximate match satisfies a guarded
  exact identifier query.
- Any public hybrid run that writes or rewrites `hot`, `index`, `overview`, or
  `quickstart` pages.
- Any public per-result response schema expansion for orientation diagnostics.
- Any repeated warm vector query over an unchanged projection that reconstructs
  the provider, reloads the vector payload from disk, rechunks, reindexes, or
  recomputes the full cache checksum.
- Any public quality claim for Korean retrieval based only on a multilingual
  model label.
- Any raw `ValueError`, sequence-length crash, or unredacted provider/cache
  exception from cache-hit vector search when NumPy scoring falls back.
- Any vector/hybrid search observing a `WikiIndex` and projection signature
  from different refresh generations.
- Any near-full filtered vector search that copies an unbounded matrix slice
  without blockwise scoring or an explicit supported-size guard.
- Any cache hit that ignores provider artifact/version, model revision,
  dimension, text schema, index schema, visibility scope, or cache schema
  identity changes.
- Any orientation relation from poisoned, prompt-like, stale, deleted,
  high-degree, malicious tag/source-ref, or draft/private evidence that
  overrides exact fallback or leaks private pages in approved-only mode.
- Any report that turns negative/unanswerable diagnostics into calibrated
  abstention, confidence, or no-evidence-threshold claims.
- Any public vector/hybrid quality or performance number produced from a dirty
  worktree or without a recorded clean commit SHA.
- Any NoMIRACL-ko judged-pool report that omits the no-full-corpus-recall,
  no-abstention-threshold, or no-orientation-pages limitations.
- Any NoMIRACL-ko report that omits `protocol: judged_pool`,
  `full_corpus: false`, official full Korean corpus counts, or deterministic
  evaluation pool counts/checksums.
- Any curated orientation mechanism report that omits the non-authoritative
  mechanism-benchmark label, includes raw query/document text or local paths,
  fails exact no-orientation fallback equivalence, leaks draft pages in
  approved-only mode, or presents the synthetic fixture as external retrieval
  quality evidence.

## Validation Commands For This Spec/ADR Slice

```powershell
git diff --check
git diff --name-only
```

Implementation validation commands must be added after code exists. Full
FastEmbed and SciFact checks should not run in CI until dependency, model-cache,
and public-report policy are accepted.

Curated orientation mechanism validation:

```powershell
uv run pytest -q tests/test_orientation_mechanism_runner.py
uv run python -m scripts.benchmark_adapters.orientation_mechanism_runner `
  --fixture-dir benchmarks/orientation_mechanism/fixture `
  --output-report .llmwiki-work/benchmark-adapters/orientation-mechanism/report.json `
  --vector-model-cache-root .llmwiki-work/benchmark-adapters/scifact/fastembed-model-cache
```

The command uses the already cached FastEmbed model only. It must not be run
with model download enabled for this validation item.
