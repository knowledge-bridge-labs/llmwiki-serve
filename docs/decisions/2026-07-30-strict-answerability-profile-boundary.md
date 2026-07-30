# ADR: Strict Answerability Profile Boundary

## Status

Proposed / Future.

## Context

`llmwiki-serve` is optimized to provide useful local context from read-only
Markdown projections. Its default search and context behavior should favor
recall because agents often need nearby pages, orientation pages, graph hints,
and citation candidates before they can decide what to read next.

That same recall-oriented posture is not an answerability guarantee. Read-only
benchmark analysis found that hard negative queries can still receive plausible
rows, including a measured negative FPR of `1.0` for a recall-oriented setting.
Trying to repair that with a single score cutoff is the wrong boundary: simple
thresholds can suppress false positives, but they also remove legitimate
answerable evidence and destroy recall on real variants.

The service needs an opt-in strict profile for workflows that prefer abstention
over speculative context. That profile must not redefine baseline retrieval or
make orientation rows look like answer evidence.

## Decision

Keep default search, query, and context behavior unchanged. Add any strict
answerability behavior only as a future opt-in profile with additive
configuration or request controls.

Strict answerability sits above retrieval. The service first produces candidates
through the existing context/search path, then the strict profile decides whether
those candidates support answering the query. Retrieval answers "what might be
useful?"; answerability answers "is there enough current served evidence to
answer?"

The first strict profile layer uses a documented lexical signal envelope rather
than a global qrel-tuned threshold. The envelope may consider significant query
term coverage, exact literal matches, title/path/heading agreement, score
separation, citation-bearing evidence density, and required-token misses. It
must require literal support for numbers, versions, dates, identifiers, quoted
phrases, and private-token style needles. It must require citation-bearing
support for citation-required queries.

Calibration and acceptance are separate. Envelope parameters may be calibrated
only on a calibration split, chosen from documented broad ranges, and frozen
before holdout evaluation. Public quality claims require held-out real source
variants and query variants. Tuning per-query, per-source, or per-qrel constants
is rejected.

Semantic reranking, embeddings, cross-encoders, and LLM evidence verification
are not part of the initial boundary. They may be added only as future opt-in
layers with their own privacy, latency, token, cost, and held-out metric gates.
An LLM verifier, if added later, may inspect only served candidate evidence and
must not answer from model prior knowledge or unserved source files.

Orientation rows remain orientation telemetry. They can help an agent navigate,
and context-bundle reports may include them for payload telemetry, but they do
not count as answer support, citation support, retrieval recall, or negative
false positives for strict answerability. Benchmark run ids and report fields
must distinguish default retrieval, strict answerability, orientation-only rows,
and telemetry-only bundles.

Public API compatibility is preserved by default. Existing clients continue to
receive the same default schemas and behavior. Any future strict-profile public
controls or diagnostics must be additive, opt-in, and separately reviewed for
OpenAPI, HTTP, MCP, Streamable HTTP, CLI, and environment compatibility.

Strict answerability artifacts and diagnostics must be public-safe. They must
not persist private local paths, private endpoint URLs, credentials, tokens, raw
request bodies, raw private query logs, unredacted Redis keys, or private source
content. If a strict layer fails, times out, exceeds budget, or sees corrupt
state, it fails closed for answerability admission while preserving default
retrieval availability.

Rollback is configuration-first: disable the strict profile and ignore any
strict-profile runtime state, calibration artifacts, or diagnostics. No source
cleanup is required because this boundary does not write to served source
folders.

## Consequences

- Default agent context remains recall-oriented and backward-compatible.
- Strict workflows get an abstention-oriented path without weakening normal
  search/context.
- Retrieval metrics, answerability metrics, and orientation telemetry stay
  separate.
- The design resists overfitting by requiring frozen calibration and held-out
  real-variant gates.
- Future semantic or LLM layers have a clear boundary and cannot silently become
  default behavior.

## Follow-Ups

- Define exact opt-in config and request names in
  `specs/strict-answerability-profile/` before implementation.
- Add benchmark/report support for strict answerability run ids.
- Build public-safe calibration and held-out real-variant datasets.
- Implement lexical-only strict answerability before considering semantic or LLM
  verifier layers.
- Document public usage only after held-out recall, negative FPR, citation,
  privacy, latency, and rollback gates pass.

## References

- Spec: `specs/strict-answerability-profile/`
- Verified benchmark spec: `specs/verified-source-benchmarks/`
- Search relevance spec: `specs/korean-numeric-search-relevance/`
- Managed generic context ADR:
  `docs/decisions/2026-07-30-managed-generic-markdown-sidecar-boundary.md`
- Architecture: `docs/architecture.md`
