# 2026-08-01 Decision Record: Optional Source-Owned Semantic Vector Retrieval Boundary

## Status

Proposed / implementation branch. This branch contains runtime implementation
work, but no commit, push, publish step, release, or public claim has occurred.

The 2026-08-02 implementation release blockers are closed by independent code
review plus hash-stable dirty-snapshot Windows and DGX engineering validation.
The opt-in semantic retrieval preview is a conditional PR/merge candidate once
all release-critical untracked files are intentionally included. Clean-commit
SHA Windows and DGX public benchmark reports and public performance claims
remain pending. Dirty-snapshot metrics are engineering evidence only and do not
support universal superiority, calibrated abstention, large-corpus/ANN, SOTA,
broad multilingual quality, or poisoning-safety claims.

## Context

`llmwiki-serve` is a local-first read-only source layer. Its default retrieval
is lexical, with literal exact-substring mode for precise checks. The current
search mode type is `lexical|literal`, and that value is already shared across
HTTP, MCP JSON-RPC, MCP Streamable HTTP, CLI, and Python service calls.

Semantic retrieval can improve paraphrase recall, but it adds a materially
different boundary: embedding dependencies, model download behavior, model
cache state, derived vector sidecars, score semantics, benchmark claims, and
privacy risk. Embeddings do not store raw text, but they are still derived
sensitive data. A vector cache must therefore be treated more like a local
sensitive derived cache than like harmless metadata.

The repo already has a Redis projection-store ADR. That ADR explicitly deferred
RedisVL and semantic/vector search because vector retrieval changes ranking and
privacy posture. This ADR reopens only a local, source-owned, optional vector
path and keeps hosted/vector-database designs out of scope.

Expert review on 2026-08-02 reproduced cache-hit fallback, refresh/search
snapshot, exact-scoring memory, adversarial orientation, and negative-query
gaps. Those implementation release blockers have now been closed by independent
code review plus hash-stable dirty-snapshot Windows and DGX engineering
validation. Clean-commit SHA public benchmark reports remain pending before any
public vector/hybrid quality or performance claim.

## Decision

Add semantic retrieval only as an optional source-owned mode extension to the
existing query/search surfaces.

The default install and default runtime behavior remain unchanged. Users who do
not install and configure a vector provider keep the existing lexical and
literal behavior. Installing `llmwiki-serve[vector]` adds local FastEmbed plus a
direct NumPy dependency, but it must not download a model, construct a provider,
build vectors, or write a cache unless the operator explicitly enables the
vector provider.

Extend the existing mode enum to `lexical|literal|vector|hybrid` across HTTP,
MCP JSON-RPC, MCP Streamable HTTP, CLI, and Python service calls. Do not add a
new endpoint, MCP tool, CLI command, or response shape. Results remain
page-level `SearchResult` payloads with compatible citations from the owning
page's `source_refs`. Public HTTP/OpenAPI and MCP/FastMCP tool schemas may
statically accept `vector` and `hybrid`; runtime capabilities and errors tell
clients whether those modes are configured and usable. Dynamic FastMCP schemas
are not required.

Define an `EmbeddingProvider` protocol and ship only a local FastEmbed provider
first. Remote OpenAI embeddings, vLLM embeddings, Redis vectors, RedisVL, ANN,
FAISS/HNSW, sentence-transformers runtime, cross-encoders, and rerankers are
follow-ups. The FastEmbed provider always receives an explicit `model_name`;
it must never rely on the FastEmbed package default. Runtime model access
defaults to local-files-only/offline behavior. Network model download is
allowed only by an explicit operator startup or service setting such as
`--vector-model-download allow`. Client query/search requests cannot choose
provider, model, cache path, or download policy. The implementation slice must
select and lock tested bounded FastEmbed and NumPy dependency ranges rather
than inventing unverified bounds in this spec-only phase.

Use exact cosine for initial vector retrieval. Use deterministic
heading/paragraph-aware chunks derived from current `WikiPage` fields and
`WikiPage.text` locators. The text schema excludes local roots, paths,
source-reference labels, full front matter, and graph metadata. Snippets are
generated from current page text; raw chunk text is not stored in the cache.

Use public hybrid retrieval as LLMWiki-aware orientation-seeded hybrid v1, not
plain lexical/vector RRF. Plain RRF remains an internal benchmark baseline and
the exact no-orientation fallback. Hybrid first identifies query-relevant
orientation seeds from read-only canonical `hot`, `index`, and `overview` role
pages, capped at three and derived deterministically from bounded lexical and
vector ranks computed over the orientation-page subset. Root `quickstart.md`
can participate only when projection classifies it as the canonical `index`
role. Hybrid never prepends whole orientation content to the query.

Hybrid expands a related candidate set only from trustworthy existing links,
`source_refs`, and tags visible in or near the matched orientation
snippet/chunk, with strict caps and approved/draft isolation. Generic
high-degree index boilerplate must not dominate results; if no safe related
set exists, hybrid returns the exact plain lexical+vector RRF fallback ordering
and payload shape. Hybrid embeds the query at most once per request, scores the
safe related-page subset directly against that reused query vector over only
the subset's eligible chunk record positions, then keeps one global vector
channel for recall safety. Related targets therefore remain eligible even when
they fall outside the global vector candidate depth.

Fuse public hybrid channels with fixed weighted RRF and constant `60`:
lexical/exact `1.0`, related-vector `1.0`, global-vector `0.75`,
orientation-doc `0.35`, and graph-prior `0.25`. These constants are documented
design constants, not qrel-tuned knobs. For normal non-identifier queries,
orientation seed pages contribute through the low-weight orientation-doc
channel rather than dominating lexical/global evidence. Literal mode remains
separate. For
exact single-token identifier, version, or metadata lookups guarded by the
lexical analyzer, hybrid applies the same exact-required document guard before
fusion so vector-only approximate matches cannot weaken exact lookup semantics.
Hybrid and the benchmark-only plain-RRF baseline use a documented bounded
candidate depth based only on requested `limit` and visible document count:
`min(total_docs, max(limit, min(1024, max(256, 4 * limit))))`. The `1024` cap
limits automatic overfetch beyond the requested result count while preserving
an explicitly larger Python benchmark/request limit. Candidate depth must not
depend on qrels, query ids, corpus ids, or quality labels.
Public per-request minimum-score semantics are not defined for vector or
hybrid in this first slice. If the existing request `min_score` remains, it is
legacy lexical/literal behavior; non-null `min_score` with vector/hybrid is
rejected and is never applied to hybrid RRF. A server-side vector threshold may
be reconsidered only as operator configuration after benchmark evidence exists.

Orientation pages are retrieval hints only. The service must never create,
write, rewrite, or modify `hot`, `index`, `overview`, or `quickstart` pages.

Store vector cache sidecars outside the served wiki root. The source root
remains immutable input. The cache stores no raw source text, snippets, raw
queries, local roots, model local paths, credentials, or raw provider responses.
Cache schema `llmwiki-vector-cache-v2` stores sensitive derived float32 vectors
in checksum-named `.npy` sidecars and compact JSON metadata containing stable
page/chunk ids, locators, schema ids, provider identity, and content hashes.
Vector norms are derived from the loaded matrix rather than persisted as JSON.
Cache identity includes source scope, projection/content hash, provider id,
model id, resolved revision, dimension, distance metric, text schema id, index
schema id, visibility scope, and cache schema version. If a root fingerprint is
needed to avoid cross-root collision, it must be local and redacted, such as a
salted digest, not a raw path.

Default sidecar roots are OS-specific per-user cache/state locations:
`%LOCALAPPDATA%\llmwiki-serve\vector-cache` on Windows,
`~/Library/Caches/llmwiki-serve/vector-cache` on macOS, and
`$XDG_CACHE_HOME/llmwiki-serve/vector-cache` with
`~/.cache/llmwiki-serve/vector-cache` fallback on Linux and other XDG
platforms. An explicit cache path is resolved to an absolute path and rejected
if it is equal to or nested under the served wiki root.

An explicit FastEmbed model cache path is resolved against the same source-root
boundary and rejected if it is equal to or nested under the served wiki root.
This validation runs before provider construction or any operator-approved
model download.

Draft-inclusive and approved-only vector indexes are separate cache identities.
A draft-inclusive vector cache must never satisfy an approved-only network
request.

Cache writes are atomic from the reader's perspective. Corrupt, partial,
old-schema, malformed `.npy`, schema-mismatched, provider-mismatched,
dimension-mismatched, shape/dtype-mismatched, projection-mismatched,
visibility-mismatched, or checksum-mismatched records are cache misses, not
source evidence. Writers publish vector/metadata sidecars with checksums first
and the manifest last with `os.replace`; readers trust only a valid manifest
whose referenced sidecar checksums, schema, shape, dtype, provider/content
identity, and visibility identity verify. Builders use per-cache identity
locking or an equivalent sidecar-local advisory lock, treat stale locks as
recoverable, and never create locks under the served root. If the provider is
available, rebuild; if not, return an actionable error.

If vector or hybrid mode is requested with no configured provider, the service
returns an actionable error: HTTP 4xx, MCP invalid-parameter/tool error, or CLI
nonzero exit. Unknown modes are rejected. MCP/JSON-RPC handlers and tools must
not silently normalize unknown or disabled modes to lexical.

Health, manifest, source-bundle, and MCP metadata use exact capability strings:
`llmwiki_retrieval_v1`, `llmwiki_search_mode_lexical`, and
`llmwiki_search_mode_literal` for baseline retrieval. They add
`llmwiki_search_mode_vector` and `llmwiki_search_mode_hybrid` only when the
configured provider and vector index are usable. Capability and status output
must not leak local source roots, local model paths, vector cache paths,
credentials, raw keys, raw vectors, or secrets.

Managed-context recording counts vector and hybrid hits with their actual
`route` value. Semantic hits are not relabeled as lexical for compatibility.

For repeated vector or hybrid queries over an unchanged projection/provider/
visibility identity, the service reuses the provider and loaded vector index
record in process. It must not reconstruct the provider, recompute the full
cache checksum, reload the vector payload from disk, rechunk, reindex, or make
repeated large vector copies unless refresh/content/provider/visibility
invalidation requires it. Thread safety and draft isolation remain part of the
cache boundary.
Hybrid query execution must also avoid full-corpus `SearchResult`
materialization. Lexical, vector, related-vector, orientation-doc, and
graph-prior channels use the shared bounded candidate depth or stricter
channel-local caps before fusion.

Cache-hit fallback was an implementation release blocker and is now closed by
dirty-snapshot engineering validation. Loaded cache records may omit per-record
in-memory vectors because the `.npy` matrix is authoritative. Any fallback path
used when NumPy scoring fails or is disabled must score from the loaded matrix
row, skip invalid rows, or fail through a controlled redacted vector error.
Raw `ValueError` or sequence-length crashes are not acceptable behavior.

Refresh/search consistency was an implementation release blocker and is now
closed by dirty-snapshot engineering validation. A server must publish and read
an immutable retrieval snapshot containing the `WikiIndex`, projection
signature, and vector-cache identity. Search must not read these fields from
separate mutable state where a concurrent refresh can create a torn snapshot.

Exact scoring remains the v1 implementation choice, but broad filtered scoring
must not rely on near-full NumPy advanced-indexing copies. The release path is
either no-copy/blockwise exact scoring with bounded top-k selection, or a
tested supported-size guard and documented envelope. Small orientation and
related subsets remain acceptable because their candidate sets are capped by
the hybrid design.

Cache identity for the public provider includes provider artifact/version
evidence for FastEmbed. If future custom providers cannot expose stable
artifact fingerprints, custom provider artifact stability remains outside v1
claims until a separate contract is accepted.

Negative and unanswerable query reports are diagnostics only. They may report
false-positive rate, citation precision, score separation, and top-k behavior,
but v1 does not provide calibrated abstention, confidence, or no-evidence
threshold semantics. Public docs and reports must say this directly.

Adversarial orientation gates are required before release. The fixture must
cover poisoned prose, prompt-like text, stale links, deleted targets,
high-degree hub pages, malicious tag/source-ref relations, draft/private
leakage, and Korean/English/Unicode identifiers. Passing these gates means the
bounded orientation hint mechanism follows its contract; it is not a claim that
the system is generally poisoning-safe.

Public vector/hybrid quality or performance reports require a clean-commit SHA
rerun on Windows and Ubuntu/DGX. Dirty worktree reports are useful engineering
evidence, but not public release proof.

The initial model candidate is
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, but it is only a
candidate until exact model revision, dependency version, artifact checksums
where available, license evidence, dimension, and benchmark results are pinned
and recorded. Operators can override the model explicitly. Do not claim Korean
quality based on the multilingual label. NoMIRACL-ko judged-pool smoke evidence
may be recorded only with explicit limitations: it is not a full MIRACL-ko
corpus run, not a headline Korean recall claim, and not an abstention-threshold
claim. The default NoMIRACL-ko materialization must be
`protocol: judged_pool`, `full_corpus: false`: all 213 `dev.relevant` queries
plus a deterministic 213-query `dev.non_relevant` sample, and only documents
referenced by those selected qrel rows. Reports must still include the official
full Korean corpus counts separately so readers can see the selected-pool
scope. Full MIRACL-ko evidence remains deferred until provenance, licensing,
and release-gate policy are resolved.

SciFact is a candidate/baseline evidence path, not an active release gate until
a threshold policy is implemented. SciFact reports must record deterministic
tie-breaking, cross-OS quality tolerance, benchmark-only plain lexical+vector
RRF baseline identity, aggregate-only orientation diagnostics, vector reuse
evidence, resolved dependency/model/cache/chunk provenance,
`languages_evaluated: [en]`, and `korean_quality: not_evaluated`.

## Consequences

- Existing users keep the same default dependency footprint and retrieval
  behavior.
- Vector and hybrid retrieval become explicit operator choices rather than
  ambient fallback behavior.
- Public hybrid benefits from LLMWiki orientation pages where they contain
  query-relevant links/source_refs/tags, while preserving exact plain-RRF
  fallback for generic Markdown folders or weak orientation pages.
- Search score semantics become mode-specific: lexical/literal scores keep
  existing meanings, vector score is exact cosine for the best page chunk, and
  hybrid score is the final RRF sum. Public docs must warn that scores are not
  calibrated across modes.
- The project gains a new sensitive derived cache boundary. Operators can
  delete the vector sidecar without source cleanup, but they should protect it
  as local sensitive state. Binary `.npy` sidecars reduce cold-load cost versus
  JSON float payloads while keeping raw text out of cache records.
- OpenAPI and docs have additive mode enum updates for the opt-in preview.
- Candidate release evidence has dirty-snapshot Windows/DGX engineering
  validation. Clean-commit SHA public benchmark reports remain pending before
  public quality or performance claims, and SciFact is not an active gate until
  thresholds are accepted.
- Korean semantic retrieval remains limited evidence, not a product claim:
  NoMIRACL-ko judged-pool smoke can describe measured behavior, while full
  MIRACL-ko/headline recall and abstention claims remain out of scope. The
  report must expose both official full-corpus counts and actual evaluation
  pool counts/checksums.
- The reproduced implementation blockers are closed for PR/merge candidacy
  once all release-critical untracked files are intentionally included.
- Exact local vector search can be marketed only within a tested small/team
  supported-size envelope unless blockwise/no-copy scoring evidence raises that
  envelope.
- Negative/unanswerable diagnostics improve trust in reports, but they do not
  authorize abstention or confidence claims in v1.
- Public reports now need a clean-commit SHA, dirty-state flag, platform,
  package version, model revision, and vector-cache identity before they can be
  treated as release evidence.

## Follow-Ups

- Intentionally include all release-critical untracked files before treating the
  opt-in semantic retrieval preview as a PR/merge candidate.
- Rerun clean-commit SHA Windows and DGX public benchmark reports before public
  quality or performance claims.
- Define threshold policy before making SciFact an active release gate.
- Define full MIRACL-ko provenance and evidence only after a separate
  release-gate decision is accepted.
- Reconsider ANN, Redis vectors, remote providers, and rerankers only after the
  exact-cosine local boundary is benchmarked and stable.

## References

- Spec: `../../specs/semantic-vector-retrieval/`
- Architecture: `../architecture.md`
- Redis projection-store ADR:
  `2026-07-22-redis-projection-store-derived-cache-boundary.md`
- Managed generic sidecar ADR:
  `2026-07-30-managed-generic-markdown-sidecar-boundary.md`
- Language-aware lexical analyzer ADR:
  `2026-08-01-language-aware-lexical-analyzer.md`
- Benchmark adapter layer spec:
  `../../specs/benchmark-adapter-layer/`
- Korean numeric search relevance spec:
  `../../specs/korean-numeric-search-relevance/`
