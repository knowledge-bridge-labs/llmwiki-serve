# Tasks: Semantic Vector Retrieval

- [x] Create this semantic vector retrieval spec package.
- [x] Record the ADR for optional source-owned semantic/vector retrieval and
  vector-cache/privacy boundary.
- [x] Confirm current runtime mode enum is `lexical|literal` before writing the
  extension requirements.
- [x] Inspect parser/model constraints for chunking: `WikiPage.text`,
  `headings`, page-level `source_refs`, and current `SearchResult` shape.
- [x] Record that existing lexical/literal behavior and default install must
  remain unchanged.
- [x] Record that vector/hybrid extend the existing HTTP/MCP/CLI/Python mode
  enum instead of adding endpoints or tools.
- [x] Record optional `llmwiki-serve[vector]` dependency shape: local FastEmbed
  plus direct NumPy dependency.
- [x] Record provider-disabled vector/hybrid requests as actionable errors with
  no silent fallback.
- [x] Record local FastEmbed as the first provider and remote/OpenAI/vLLM,
  Redis vectors, ANN, and sentence-transformers runtime as non-goals.
- [x] Record exact cosine first and fixed RRF constant `60` for initial plain
  RRF baseline.
- [x] Record LLMWiki-aware orientation-seeded hybrid v1 as the final public
  hybrid design, with plain RRF retained only as a benchmark baseline and exact
  no-orientation fallback.
- [x] Record vector sidecar cache privacy, identity, atomicity, corruption,
  draft isolation, refresh, concurrency, and failure boundaries.
- [x] Record that the initial model candidate requires exact revision, license,
  dimension, and benchmark evidence before any documented default claim.
- [x] Record SciFact benchmark extension requirements and Korean MIRACL-ko
  full-evidence deferral.
- [x] Record that limited Korean NoMIRACL-ko judged-pool smoke evidence is
  allowed only with explicit no-full-corpus-recall and no-abstention-threshold
  limitations.

## 2026-08-02 Expert-Review Follow-Up Tasks

### Release Blockers

- [x] Record the 2026-08-02 expert-review follow-up in the canonical spec,
  plan, tests, and ADR.
- [x] Fix public normal hybrid fallback so no-safe-related-set plain RRF
  suppresses `hot`, `index`, and `overview` answer pages unless the query
  explicitly names the orientation page.
- [x] Fix the cache-hit NumPy-failure fallback crash: v2 cache-loaded records
  may have empty in-memory `record.vector`, and fallback scoring must use the
  loaded matrix row, skip invalid rows, or raise a controlled redacted
  `VectorSearchError` instead of a raw `ValueError`.
- [x] Add a regression that loads vectors from cache, forces NumPy scoring to
  fail or be disabled, then proves vector/hybrid search does not crash.
- [x] Replace refresh/search mutable state pairing with an immutable retrieval
  snapshot that publishes and reads `WikiIndex`, projection signature, and
  vector-cache identity together.
- [x] Add a concurrency regression for the reproduced torn snapshot where a
  new `WikiIndex` was paired with an old projection digest.
- [ ] Rerun public-quality/performance candidate reports only after a clean
  commit SHA exists; dirty worktree reports remain engineering evidence only.

### Required Gates

- [x] Fix or bound the near-full filtered NumPy advanced-indexing memory path.
  Preferred implementation is no-copy/blockwise exact scoring with bounded
  top-k selection; acceptable fallback is an explicit tested supported-size
  guard and release-note envelope.
- [x] Add an exclude-one or equivalent near-full filtered search memory
  regression.
- [x] Add provider artifact/version fingerprinting to cache identity for the
  shipped FastEmbed provider, or explicitly exclude custom provider artifact
  stability from v1 and test provider/model/revision/version identity misses.
- [x] Add cross-service cold-build retry behavior coverage for callers that
  request vector/hybrid while the first cache build is still in progress.
- [x] Add adversarial orientation gates for poisoned prose, prompt-like
  content, stale links, deleted targets, high-degree hub pages, malicious
  tag/source-ref relations, and exact fallback when the related set is unsafe.
- [x] Add draft/private leakage regressions for vector/hybrid orientation and
  related-subset scoring.
- [x] Add Korean, English, and Unicode identifier regressions for exact
  identifier preservation across lexical, vector, and hybrid paths.
- [x] Add negative/unanswerable diagnostics for false-positive top-k behavior,
  citation precision, and score separation, with no calibrated abstention
  claim.

### Supported-Size Envelope And Post-Release Experiments

- [x] Record the validated exact-scoring supported-size envelope: document
  count, chunk count, dimension, platform, memory path, latency, and whether
  blockwise/no-copy scoring or a guard is in effect.
- [ ] Keep ANN/HNSW/FAISS, vector databases, rerankers, GraphRAG, calibrated
  abstention thresholds, and large-corpus claims as post-release experiments
  until separate specs and benchmark gates are accepted.

Reconciliation evidence for completed 2026-08-02 implementation gates:

- Independent post-fix review reported no blocking code defects and verified
  cache-hit fallback, immutable retrieval snapshots, exact vector reranking,
  blockwise/no-copy public scoring, FastEmbed runtime fingerprinting, cold-build
  retry behavior, and orientation fallback suppression.
- Frozen Windows dirty-snapshot validation held the candidate manifest stable,
  ran the full pytest suite twice (`602 passed, 7 skipped` each), focused vector
  and orientation tests (`77 passed, 1 skipped`), ruff format/check, mypy,
  OpenAPI validation, notices validation, release smoke/build checks, base
  install smoke, cached FastEmbed smoke with model download disabled, adversarial
  orientation fixture (`9` cases), and 10k/100k exact-scoring memory checks with
  no copy-like matrix calls.
- Frozen Ubuntu/DGX dirty-snapshot validation held the candidate manifest
  stable, ran focused regressions (`6 passed`), focused vector/benchmark tests
  (`98 passed`), the full pytest suite (`607 passed, 2 skipped`), ruff
  format/check, mypy, offline cached FastEmbed checks with model download
  disabled, public report validators, sanitized privacy scanning with zero
  blockers, adversarial orientation (`19` total queries, `9` adversarial,
  `0` production blocker failures, `2` malicious-relation residual-risk
  diagnostics), and exact-scoring envelope checks at 10k and 100k rows,
  dimension `384`, including 90 percent filtered subsets.
- Retrieval-quality numbers remain engineering evidence only. In the frozen
  Ubuntu/DGX run, SciFact lexical had higher nDCG@10/MAP than vector or hybrid,
  while hybrid improved Recall@100 over lexical (`0.9287` to `0.9520`).
  NoMIRACL-ko judged-pool hybrid improved nDCG@10, Recall@100, and MAP over
  lexical in that run. These dirty-snapshot measurements are not clean-commit
  public-report evidence and do not support universal superiority, calibrated
  abstention, poisoning safety, broad multilingual quality, or unsupported-size
  claims.

## Implementation Tasks

- [x] Extend `SearchMode` to `lexical|literal|vector|hybrid`.
- [x] Update CLI `SearchModeChoice` and help text.
- [x] Update HTTP `QueryRequest` generated schema and mode validation.
- [x] Update MCP JSON-RPC mode parsing to reject unknown modes instead of
  silently mapping them to lexical.
- [x] Update MCP Streamable HTTP tool argument schemas/descriptions so schemas
  may statically accept `vector` and `hybrid`, while runtime capabilities and
  errors communicate configured availability.
- [x] Add vector provider configuration for CLI, environment, Python
  `create_app`, and `LlmWikiService`; do not expose provider/model/cache path
  or download policy as client request parameters.
- [x] Add actionable errors for missing optional extra, disabled provider,
  missing model, offline model cache, unknown/disabled modes, provider failure,
  and corrupt cache.
- [x] Add `EmbeddingProvider` protocol.
- [x] Add local FastEmbed provider behind `llmwiki-serve[vector]` and always
  pass an explicit `model_name`.
- [x] Add FastEmbed runtime model policy: default `local_files_only` behavior
  and network download only through explicit operator setting such as
  `--vector-model-download allow`.
- [x] Add direct NumPy dependency to the vector extra.
- [x] Select tested bounded FastEmbed/NumPy dependency ranges during
  implementation and commit them through the project lockfile; record resolved
  versions in validation reports.
- [x] Add model metadata normalization and redaction.
- [x] Pin and record the candidate model revision, license, dimension, and
  dependency versions before documenting a default model.
- [x] Add deterministic text schema `llmwiki-vector-text-v1`.
- [x] Add heading and paragraph-aware chunker with stable chunk ids.
- [x] Exclude paths, source refs, full front matter, graph metadata, and local
  roots from chunk text.
- [x] Add vector sidecar cache outside the wiki root.
- [x] Add OS-specific per-user vector sidecar defaults and reject configured
  cache paths that resolve equal to or under the served wiki root.
- [x] Add cache identity with source scope, projection/content hash, provider,
  model, revision, dimension, text schema, index schema, visibility scope, and
  cache schema.
- [x] Add approved-only and draft-inclusive cache isolation.
- [x] Add binary sidecar/checksum-first cache writes, manifest-last
  `os.replace`, checksum validation, shape/dtype validation, sidecar
  locking/concurrency, stale-lock handling, old-schema invalidation, and
  corruption fallback.
- [x] Add exact cosine vector retrieval.
- [x] Collapse chunk hits to page-level `SearchResult` rows and snippets.
- [x] Add plain lexical+vector RRF over lexical and vector candidates with
  constant `60` as the current fallback/baseline implementation.
- [x] Replace public hybrid final ranking with orientation-seeded hybrid v1:
  query-relevant canonical `hot|index|overview` seeds, max three seeds, no
  whole-page query prepending, safe relation extraction from matched evidence,
  strict caps, approved/draft isolation, direct related-subset vector scoring
  over only eligible subset records with one reused query embedding, one global
  vector recall channel, optional orientation-doc and graph-prior channels, and
  exact fallback to plain RRF when no safe related set exists.
- [x] Preserve exact identifier guards in hybrid mode.
- [x] Preserve exact identifier guards after orientation-seeded hybrid fusion.
- [x] Reuse provider and loaded vector index records across repeated unchanged
  projection queries; avoid per-query provider construction, sidecar checksum,
  disk reload, rechunk, reindex, and large vector copies.
- [x] Bound public hybrid and benchmark-only plain-RRF lexical/vector candidate
  depth from requested `limit` and visible document count instead of
  materializing full-corpus `SearchResult` lists.
- [x] Make benchmark-only plain RRF reuse the service-owned `SearchCorpus`
  cache for unchanged projections and rebuild after refresh.
- [x] Reject non-null public `min_score` for vector/hybrid; keep any existing
  `min_score` behavior legacy lexical/literal only and never apply it to RRF.
- [x] Count vector and hybrid hits in managed-context recording with their
  actual `route`.
- [x] Add supported-mode capability/status advertisement using exact
  `llmwiki_retrieval_v1`, `llmwiki_search_mode_lexical`,
  `llmwiki_search_mode_literal`, `llmwiki_search_mode_vector`, and
  `llmwiki_search_mode_hybrid` strings without leaking local roots, cache
  paths, model paths, or secrets.
- [x] Redact unsupported FastEmbed model identifiers when they are local paths
  or file URIs before returning public errors.
- [x] Reject explicit FastEmbed model cache directories equal to or nested
  under the served source root before provider construction or model download.
- [x] Make lazy vector provider initialization thread-safe for concurrent first
  vector/hybrid calls.
- [x] Regenerate and check `docs/openapi.json`.
- [x] Update README, architecture, release checklist, and notices after
  implementation.

## Test And Benchmark Tasks

- [x] Add fake embedding provider tests in CI with no model download.
- [x] Add default-install tests proving vector dependencies and provider
  construction are absent unless configured.
- [x] Add API/MCP/CLI tests for `vector` and `hybrid` mode acceptance only when
  configured.
- [x] Add disabled-provider and unknown-mode 4xx/MCP/CLI error tests proving
  no lexical normalization or fallback.
- [x] Add capability metadata tests for exact health/manifest/source-bundle/MCP
  strings and vector/hybrid omission until provider/index is usable.
- [x] Add FastEmbed configuration tests proving explicit `model_name`, default
  local-files-only behavior, operator-only download allow, and no
  provider/model/cache/download request parameters.
- [x] Add chunking determinism tests on headings, paragraphs, long paragraphs,
  empty pages, Unicode text, and Windows/Unix line endings.
- [x] Add exact cosine tests with fixed fake vectors.
- [x] Add vector cache identity, binary sidecar round-trip, atomic write,
  corrupt record, malformed shape/dtype/hash, old schema, partial sidecar,
  mismatch, and refresh/invalidation tests.
- [x] Add vector cache OS-default, under-served-root rejection,
  manifest-last publish, locking/concurrency, stale-lock, checksum, and
  concurrent reader/draft-sidecar isolation tests.
- [x] Add draft isolation tests for approved-only and draft-inclusive vector
  indexes.
- [x] Add hybrid RRF tests with fixed constant `60`.
- [x] Add exact identifier/version/source-ref guard tests for hybrid mode.
- [x] Add orientation-seeded hybrid tests: relevant hot/index link lifts a
  paraphrase target, boilerplate/high-degree index does not dominate, no
  orientation is byte/order-equivalent to plain RRF, exact identifiers are
  preserved, source_ref/tag relation works, draft pages do not leak, and order
  is deterministic.
- [x] Add orientation-first regression proving a safe related target outside
  global vector candidate depth is recovered by production hybrid with one
  query embedding.
- [x] Add subset-scoring regressions proving orientation/related vector
  searches evaluate only eligible chunk record positions for NumPy and Python
  fallback paths.
- [x] Add vector provider/index reuse instrumentation tests without brittle
  wall-clock assertions.
- [x] Add bounded candidate-depth, channel limit, benchmark corpus reuse,
  refresh rebuild, no revalidation, and small-corpus equivalence tests.
- [x] Add tests rejecting non-null public `min_score` for vector/hybrid and
  proving legacy `min_score` is not applied to RRF.
- [x] Add MCP/JSON-RPC tests proving invalid non-null `min_score` values return
  invalid-parameter errors instead of silently becoming absent.
- [x] Add managed-context tests proving vector/hybrid hits are recorded with
  their route.
- [x] Extend SciFact full benchmark runner/reporting for lexical-English,
  vector, benchmark-only plain-RRF baseline, and orientation-seeded hybrid.
- [x] Add tests proving benchmark-only `plain-rrf` executes the existing plain
  lexical+vector RRF baseline and records a distinct report mode without
  expanding the public search-mode enum.
- [x] Add aggregate-only benchmark diagnostics for orientation seed count,
  related-set use/fallback count, provider/index reuse, and vector search
  timing breakdown without public per-result schema expansion.
- [x] Run a 20-query warm vector/hybrid performance smoke and report p50/p95
  plus available breakdown before any full 300-query vector benchmark.
- [ ] Run full SciFact comparison on Windows local.
- [x] Run full SciFact comparison on Ubuntu/DGX.
- [x] Add SciFact report checks for cross-OS quality tolerance, deterministic
  tie-breaking, resolved versions, model/cache/chunk provenance,
  `languages_evaluated: [en]`, and `korean_quality: not_evaluated`.
- [x] Keep SciFact vector/hybrid as candidate/baseline reporting, not an active
  release gate, until threshold policy is implemented.
- [x] Keep full MIRACL-ko and headline Korean quality claims deferred until
  provenance/release-gate requirements are satisfied.
- [x] Add and run the NoMIRACL-ko judged-pool adapter smoke path for limited
  Korean evidence only.
- [x] Add a curated LLMWiki orientation mechanism benchmark fixture and runner
  with synthetic public-safe Markdown, English/Korean qrels, real cached
  FastEmbed runtime reporting, and deterministic fake-provider tests. Label it
  as non-authoritative mechanism validation, not an external retrieval-quality
  or language-quality headline.

## Validation For Current Implementation Slice

- [x] Audit `src/llmwiki_serve/vector.py`, `errors.py`, and integrations in
  `search.py`, `service.py`, `api.py`, `cli.py`, `models.py`, and
  `pyproject.toml` against the finalized spec and ADR.
- [x] Verify FastEmbed 0.8.0 `TextEmbedding` accepts explicit `model_name`,
  `cache_dir`, and `local_files_only`, and that the configured candidate model
  appears in `TextEmbedding.list_supported_models()` with dimension `384` and
  license `apache-2.0`.
- [x] Verify an empty model cache with `local_files_only=True` fails without a
  model download and surfaces a redacted `VectorSearchError`.
- [x] Add regression coverage for local model path redaction in provider
  metadata and vector cache identity.
- [x] Add regression coverage so FastEmbed/provider stderr does not expose raw
  model cache paths through llmwiki-serve provider errors.
- [x] Add regression coverage so stale-lock cleanup does not remove a live
  current-owner lock during long vector cache builds.
- [x] Run targeted vector tests:
  `uv run pytest -q tests/test_vector_retrieval.py`
  (`14 passed` on Windows).
- [x] Run public API and service tests:
  `uv run pytest -q tests/test_public_api.py tests/test_service.py`
  (`119 passed, 1 skipped` on Windows).
- [x] Run the full test suite:
  `uv run pytest -q` (`534 passed, 6 skipped` on Windows).
- [x] Run configured formatting, lint, and type checks:
  `uv run ruff format --check .`, `uv run ruff check .`, and `uv run mypy`.
- [x] Run `uv build` and regenerate `dist/llmwiki_serve-0.2.9-py3-none-any.whl`
  plus `dist/llmwiki_serve-0.2.9.tar.gz`.
- [x] Run release smoke against the built wheel and sdist:
  `uv run python scripts/release_smoke.py --wheel ... --sdist ... --allow-network-install`.
- [x] Run base wheel install smoke in a clean venv and verify FastEmbed is
  absent while lexical search works.
- [x] Run vector extra wheel install/import smoke in a clean venv and verify
  FastEmbed `0.8.0`, NumPy `2.4.6`, explicit model id, and offline missing-model
  error redaction without permitting a model download.
- [x] Run `git diff --check`.
- [x] Run targeted review-fix tests:
  `uv run pytest -q tests/test_vector_retrieval.py tests/test_scifact_runner.py`
  (`53 passed, 1 skipped` on Windows).
- [x] Run targeted runner type check:
  `uv run mypy scripts/benchmark_adapters/scifact_runner.py`
  (`Success: no issues found in 1 source file` on Windows).
- [ ] Run a real FastEmbed model download/index/search smoke only when the
  operator explicitly approves `--vector-model-download allow` for that smoke.
- [x] Run targeted orientation-seeded hybrid and vector reuse tests.
- [x] Run a 20-query warm SciFact vector/hybrid performance smoke with the
  already downloaded model/cache.
- [ ] Extend and run full SciFact Windows local candidate reports for
  `lexical-english`, `vector`, `plain-rrf`, and `hybrid`.
- [x] Extend and run full SciFact Ubuntu/DGX candidate reports for
  `lexical-english`, `vector`, `plain-rrf`, and `hybrid`.
- [x] Run NoMIRACL-ko 5 relevant + 5 non-relevant Windows smoke with
  `--vector-model-download never`; stop before vector modes if the configured
  FastEmbed model is not already cached.
