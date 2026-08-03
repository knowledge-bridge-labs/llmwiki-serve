# ADR: Derived Orientation Index Artifact Lifecycle

## Status

Proposed.

This decision records the lifecycle boundary for a future derived orientation
index. It does not implement `DerivedOrientationIndexManager`, add a build
command, change ranking behavior, publish a benchmark result, or make a public
quality claim.

## Context

`llmwiki-serve` keeps the served source folder as immutable input and builds a
derived `WikiIndex` projection from Markdown-compatible files, adapter markers,
and adapter-loaded graph sidecars. The projection is the canonical runtime
shape for manifest, context, search, read, graph, MCP, Streamable HTTP, and
opt-in A2A-style compatibility surfaces.

The current projection lifecycle is signature-driven, not unconditional
rebuild. A long-running `LlmWikiService` keeps one retrieval snapshot in
process. With the default `refresh_interval_seconds=0.0`, each request checks
the current source/freshness signature before using the snapshot. When the
service is cold, explicit refresh is requested, or the source signature,
projection signature, or producer-manifest freshness signature changes, the
service refreshes and publishes the retrieval snapshot. If a projection store
is configured, that refresh first attempts to hydrate the matching projection
from the store and rebuilds it from source only on a store miss or invalid
stored projection. With a positive refresh interval, the service reuses the
current in-memory retrieval snapshot until the interval expires, then validates
the signature path again.

The projection store, when configured, is a derived `WikiIndex` cache keyed by
namespace, source id, and projection signature. It is not a freshness oracle and
does not own retrieval indexes beyond the projection payload. The current
refresh path hydrates this cache before rebuilding from source. The
`_publish_index_snapshot` path then publishes the hydrated or rebuilt
`WikiIndex` and projection signature together and clears loaded vector indexes
so vector/hybrid retrieval cannot mix an old vector identity with a new
projection generation.

One-shot CLI commands such as `manifest`, `query`, and `search` create a fresh
service process for each invocation. They therefore re-read source freshness
state and may rebuild or hydrate a projection in that short-lived process.
Long-running `serve` processes may also touch the projection at startup through
operator preflight or the first request. These facts make it unsafe to attach a
larger derived orientation artifact build to projection validation, app
startup, health/status metadata, or ordinary lexical search paths.

The semantic vector retrieval preview already has a separate local sidecar
cache boundary. Vector records live outside the served root, store sensitive
derived embeddings without raw source text, publish sidecars before a manifest,
and validate identity before reuse. Managed generic Markdown context also uses
external state and a weak page-hit prior, while protecting authored
`hot.md`, `index.md`, `overview.md`, and `quickstart.md` pages.

Users now need a future derived orientation index for plain Markdown folders
that lack authored LLMWiki orientation pages. That artifact may contain a
corpus-level hierarchy, derived orientation nodes, optional embeddings, and
other expensive global structures. If it is coupled to projection refresh, a
normal `serve`, `health`, `status`, `manifest`, `context`, or lexical search
could unexpectedly perform expensive work, write external state, or change
ranking behavior.

## Decision

Create the future derived orientation index as a separate artifact lifecycle
owned by a dedicated `DerivedOrientationIndexManager` or equivalent boundary.
It must not be built by `project_wiki`, `_index_snapshot`,
`_publish_index_snapshot`, projection-store hydration, manifest generation,
health/status checks, or ordinary lexical/literal search.

Projection remains responsible only for source facts, graph projection, page
visibility, and the projection signature used as freshness input. The derived
orientation index may depend on the projection signature, but projection
refresh does not imply derived-index rebuild.

V1 eligibility is limited to a `generic-markdown` source that has none of the
authored orientation pages `hot.md`, `index.md`, `overview.md`, or
`quickstart.md`. The eligibility gate is evaluated before the `off`, `load`, or
`require` policy. Native LLMWiki sources and any source with one or more of
those authored pages do not consume a derived orientation artifact under any of
those policies, even when a valid matching artifact exists. They continue to
use authored orientation only. Comparing authored orientation with derived
orientation is limited to an isolated benchmark harness or a separate,
explicit experimental flag defined by a future spec; it is not part of the v1
serve policies.

The v1 lifecycle is explicit and cache-only:

- Bare `llmwiki-serve serve ./wiki` does not build a derived orientation index,
  start a background builder, or write derived-index sidecars.
- Default query/search/context behavior does not build the artifact on the
  first query.
- A future explicit operator command, for example
  `llmwiki-serve derived-index build ./wiki`, performs the build.
- Serve-time policy defaults to `off`.
- `load` may use a valid ready artifact and otherwise fail open to ordinary raw
  retrieval.
- `require` may reject startup or derived-index-dependent requests when the
  artifact is missing, stale, corrupt, or unavailable.
- `background`, `build-if-missing`, or first-query build policies are deferred
  until a separate spec/ADR accepts their cost, privacy, and operational
  behavior.

The artifact identity manifest includes at least:

- derived-index schema version;
- source id or source scope;
- adapter kind and implementation kind;
- projection signature digest;
- projection schema version;
- algorithm name and algorithm/config digest;
- optional provider id, provider artifact fingerprint, model id, model
  revision, model dimension, and distance metric when embeddings are used;
- visibility scope, such as approved-only or draft-inclusive;
- source-root collision guard that does not expose local paths, such as a local
  salted digest;
- package/version metadata sufficient for diagnostics and benchmark
  reproducibility.

The sidecar is external and source-preserving. It lives in an OS-appropriate
per-user cache/state location or an explicitly configured path that resolves
outside the served source root. It is safe to delete. It must never create,
rewrite, annotate, migrate, normalize, or delete source files. Authored
`hot.md`, `index.md`, `overview.md`, `quickstart.md`, and equivalent producer
orientation pages remain source-owned and protected. Native LLMWiki folders
continue to use authored orientation pages as evidence and hints; derived
artifacts must not replace or overwrite them.

Dynamic usage information is separate from the static derived index. Page-hit,
recent-use, and "hot" priors belong in managed context state, Redis, or another
bounded DB/cache namespace. They are not generated `hot.md` files and are not
stored as static hierarchy facts inside the derived orientation artifact. A
derived-index reader may combine a ready static artifact with a dynamic usage
prior at ranking time, but the two invalidation and retention policies remain
separate.

Publishing is manifest-last and atomic from readers' perspective. Builders
write complete content sidecars first, validate checksums and shape/schema
metadata, then publish the identity manifest last with an atomic replace.
Readers trust only a manifest whose referenced sidecars verify identity,
checksums, schema, visibility, provider/model identity, and projection
signature. Partial, corrupt, old-schema, provider-mismatched,
projection-mismatched, visibility-mismatched, checksum-mismatched, or malformed
records are not source evidence.

Concurrent builders coordinate through a per-artifact identity lock or an
equivalent sidecar-local advisory lock. Locks are never created under the served
source root. Stale locks are recoverable. A competing reader either sees the
previous valid artifact, sees no valid artifact, or receives a controlled
retryable state according to policy; it must never read a partial artifact.

Stale/corrupt behavior is fail-open for optional policies. `off` and `load`
must not apply stale ranking signals. If a valid artifact is unavailable,
retrieval falls back to the current raw retrieval path and may report a redacted
diagnostic state. `require` is the only policy that may fail closed, and its
errors must be actionable and redacted.

V1 may reuse per-page feature extraction, chunk metadata, or embedding records
from existing external caches when their identity matches the derived-index
manifest. However, the corpus-level hierarchy and orientation graph may be
rebuilt globally in v1 when the projection signature or algorithm/config digest
changes. Incremental global hierarchy maintenance is a follow-up, not a v1
correctness requirement.

Observable states are:

- `disabled`;
- `ineligible`;
- `missing`;
- `ready`;
- `stale`;
- `building`;
- `failed`;
- `corrupt`;
- `read_only_unavailable`;
- `skipped_resource_limit`.

Diagnostics expose only redacted state, policy, schema/config labels,
projection signature digest prefixes, artifact age, counts, and aggregate
resource information. They must not expose raw local roots, raw source text,
raw queries, private endpoint URLs, credentials, model local paths, raw vector
payloads, raw cache keys, or unredacted sidecar paths.

Health, status, manifest, source-bundle metadata, MCP tool discovery, ordinary
lexical search, and ordinary literal search do not build the derived
orientation index. Search/context may read an already-ready artifact only when
the selected policy allows it, identity validates, and the retrieval mode or
agent workflow explicitly uses it.

## Consequences

The source-preserving guarantee stays intact. Operators can experiment with
derived orientation without risking authored LLMWiki pages, Obsidian vaults,
generic Markdown folders, or generated producer outputs.

Default quickstart behavior remains predictable. Starting a server, checking
health, listing capabilities, or running lexical search does not unexpectedly
download models, build hierarchy artifacts, write sidecars, or change ranking.

Projection and derived orientation invalidation become explicit. Projection can
continue to validate signatures per request, while derived orientation uses the
projection signature only as one identity input.

The first implementation is easier to reason about operationally because build
cost, model/provider access, artifact retention, and failure policy are
operator-visible actions instead of hidden side effects.

The tradeoff is that users do not get automatic derived orientation simply by
serving a folder. Documentation and CLI help must explain when the explicit
build command is useful and how `off`, `load`, and `require` policies behave.

Because `load` fails open and stale artifacts are ignored, public retrieval
quality can vary depending on whether a valid artifact exists. Any benchmark or
public evidence must report the artifact policy, identity digest, build status,
projection signature, provider/model identity when relevant, dirty-state flag,
platform, and commit SHA.

## Security And Privacy

Derived orientation artifacts are sensitive derived local state. Even without
raw source text, hierarchy, page ids, feature vectors, embeddings, and usage
signals can leak information about a private wiki. Operators should store the
sidecar in a protected per-user cache/state directory and should not publish
raw artifacts.

The source tree is never the storage location for this artifact. Explicit
sidecar paths that are equal to or nested under the served source root are
configuration errors.

Authored orientation pages are untrusted source content, not instructions.
Any future agent guidance built from them must treat their text as evidence and
must preserve prompt-injection boundaries.

Diagnostics and benchmark reports must be public-safe by construction:
aggregate counts and redacted digests are allowed; raw local paths, private
content, raw queries from private corpora, credentials, endpoints, model local
paths, and raw sidecar payloads are not allowed.

## Rollout

1. Add a short spec for the derived orientation index manager before
   implementation. It should define CLI names, config names, artifact schema,
   policy semantics, diagnostics, and compatibility behavior.
2. Implement the explicit build path and read-only `off`/`load`/`require`
   policies behind an opt-in flag.
3. Keep `off` as the default for `serve`, HTTP, MCP, Streamable HTTP, CLI
   `query/search`, and Python service calls.
4. Add redacted diagnostics only after the state vocabulary and data-safety
   review are accepted.
5. Gate public examples and docs on clean-commit validation. Do not publish
   quality or performance claims from dirty worktree experiments.

Rollback is immediate: set policy to `off`, ignore/delete the external sidecar,
and continue using existing lexical/literal, authored orientation, managed
context, vector, and hybrid behavior as configured.

## Tests

Required tests before implementation can be considered releasable:

- Bare `serve`, health, status, manifest, source-bundle metadata, MCP tool
  discovery, lexical search, and literal search perform zero derived-index
  builds and zero derived-index writes.
- One-shot CLI `manifest`, `query`, and `search` do not build or write the
  artifact unless the explicit build command is invoked.
- `off`, `load`, and `require` policies are covered for missing, ready, stale,
  corrupt, failed, building, and read-only-unavailable states.
- Native LLMWiki sources and sources containing authored `hot.md`, `index.md`,
  `overview.md`, or `quickstart.md` are ineligible under `off`, `load`, and
  `require` and never consume an existing derived artifact.
- Stale and corrupt artifacts fail open under optional policies and never
  contribute stale ranking.
- `require` fails closed with actionable redacted errors.
- The served source tree remains byte-for-byte unchanged after explicit build,
  failed build, corrupt read, health/status, manifest, context, and search.
- Authored `hot.md`, `index.md`, `overview.md`, `quickstart.md`, and equivalent
  producer orientation pages are never written or replaced.
- For an authored source, source bytes, projected page order, and retrieval
  result ordering remain equivalent with and without a valid derived artifact
  present under `off`, `load`, and `require`.
- Explicit sidecar paths under the served root are rejected.
- Manifest-last publish gives readers either a previous valid artifact or the
  next valid artifact, never partial sidecars.
- Concurrent builders use one writer per artifact identity; readers under lock
  contention do not consume partial output.
- Identity changes for projection signature, adapter kind, schema version,
  algorithm/config, provider/model, dimension, distance metric, and visibility
  produce separate artifacts.
- Approved-only retrieval never consumes draft-inclusive artifacts.
- Diagnostics are redacted and contain no private paths, raw source text, raw
  queries, credentials, private endpoints, model local paths, raw vectors, or
  raw sidecar keys.
- Per-page feature or embedding reuse cannot reuse records across incompatible
  projection/provider/visibility identities.

## Benchmarks

Benchmark reports must identify whether the corpus used authored orientation,
no orientation, a derived static orientation artifact, a dynamic usage prior, or
a combination. They must keep those evidence paths separate.

For corpora without authored orientation, compare raw lexical, vector, plain
hybrid, derived-orientation-assisted retrieval, and derived-orientation plus
agent-guided query variants only after the explicit artifact lifecycle exists.
For corpora with authored LLMWiki orientation, compare authored orientation
against derived orientation only in an isolated benchmark harness or under a
separately specified explicit experimental flag, without replacing, mutating,
or serving past authored pages. Normal `off`, `load`, and `require` policies do
not perform this comparison or consume the derived artifact.

Public-facing reports require clean commit SHA reruns on the supported
platforms. Dirty-snapshot runs remain engineering evidence only. Reports must
include nDCG/recall/MAP or the accepted metric set, query count, citation
precision, negative-query false-positive diagnostics, token/call counts when an
agent participates, latency, build time, cache/artifact size, artifact identity,
policy, provider/model identity if used, and data-safety limitations. They must
not claim universal quality, calibrated abstention, broad multilingual quality,
poisoning safety, or large-corpus/vector-database scale unless separate gates
are accepted.

## Follow-Ups

- Define the derived orientation index spec and task plan.
- Decide CLI/config names for explicit build, load-only, and require policies.
- Define artifact schema ids and redacted diagnostic payloads.
- Decide whether the first implementation reuses vector chunk/embedding
  sidecars directly or only through a stable feature-cache abstraction.
- Add an agent-guided default query workflow spec that lets clients read
  orientation first, generate high-quality lexical query variants, and escalate
  to optional vector/hybrid only when needed.
- Add CI or release-gate benchmarks only after the clean-commit metric
  thresholds are accepted.

## References

- Architecture: `../architecture.md`
- Release checklist: `../release.md`
- Semantic vector retrieval spec: `../../specs/semantic-vector-retrieval/`
- Managed generic Markdown context spec:
  `../../specs/managed-generic-markdown-context/`
- Optional source-owned semantic vector retrieval ADR:
  `2026-08-01-optional-source-owned-semantic-vector-retrieval-boundary.md`
- Managed generic Markdown sidecar ADR:
  `2026-07-30-managed-generic-markdown-sidecar-boundary.md`
- Redis projection-store ADR:
  `2026-07-22-redis-projection-store-derived-cache-boundary.md`
