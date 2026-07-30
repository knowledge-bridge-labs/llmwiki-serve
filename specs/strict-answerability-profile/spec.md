# Spec: Strict Answerability Profile

## Status

Proposed / Future.

## Problem

Current search and context defaults are recall-oriented. That is the right
default for agent context gathering, but read-only benchmark analysis shows the
same posture is not a reliable answerability decision: hard negative queries can
still receive plausible context rows, producing negative-query false positives.
An exploratory measured failure mode reached negative FPR `1.0` for a
recall-oriented setting.

The direct fix is not a magic score cutoff. Simple global score thresholds can
remove low-confidence false positives, but they also drop legitimate answerable
evidence and destroy recall on real variants. The service needs an opt-in
profile that asks a different question: not "which pages might help?", but
"does the current context contain enough evidence to answer?"

## Goals

- Keep default search, query, and context behavior unchanged.
- Add an opt-in, context-first strict answerability profile as a future layer.
- Treat retrieval and answerability as separate decisions with separate metrics.
- Avoid qrel-tuned global score thresholds or hidden corpus-specific constants.
- Define a calibration and held-out evaluation protocol before implementation.
- Start with a lexical signal envelope and defer optional semantic and LLM
  verifiers to separately gated future layers.
- Preserve public API and configuration compatibility through additive opt-in
  controls only.
- Keep privacy, latency, token budget, failure modes, rollback, and orientation
  telemetry behavior explicit.

## Non-Goals

- Do not change default result ordering, default context packs, search scoring,
  or existing orientation behavior.
- Do not implement runtime code in this spec slice.
- Do not tune a threshold to the checked-in qrels or to one benchmark run.
- Do not add embeddings, vector search, semantic reranking, or model calls in
  the first strict-profile slice.
- Do not treat orientation rows as answer support.
- Do not make public benchmark claims from calibration data.

## Requirements

- `REQ-SAP-001`: The strict answerability profile is disabled by default. With
  no opt-in configuration or request option, search, query, context, HTTP, MCP,
  Streamable HTTP, OpenAPI, and CLI behavior remains unchanged.
- `REQ-SAP-002`: Strict answerability is context-first. It consumes the normal
  retrieved context candidates for a query, then decides whether those candidates
  provide sufficient answer evidence.
- `REQ-SAP-003`: Retrieval remains a candidate-generation step. A retrieved row
  is not automatically an answerable row, and answerability abstention must not
  be reported as retrieval failure by itself.
- `REQ-SAP-004`: The first profile layer uses a lexical signal envelope rather
  than a single `min_score` or qrel-tuned magic threshold.
- `REQ-SAP-005`: The lexical envelope may use pre-registered signals such as
  significant query-token coverage, exact phrase or numeric literal presence,
  top-result score separation, title/path/heading agreement, citation-bearing
  evidence density, and absence of required-token contradictions.
- `REQ-SAP-006`: Exact literals, numeric claims, identifiers, private-token
  style needles, versions, dates, and citation requests require literal or
  citation-bearing support in current served pages. Generic topical similarity
  alone is insufficient.
- `REQ-SAP-007`: The lexical envelope cannot be calibrated on the same query
  variants used for acceptance. Calibration data, holdout data, metrics, and
  intended gates must be recorded before holdout evaluation.
- `REQ-SAP-008`: Calibration may choose envelope parameters only from broad,
  documented ranges. It must not optimize per-query, per-source, or per-qrel
  constants.
- `REQ-SAP-009`: Held-out evaluation uses real source variants and query
  variants that were not used to tune envelope parameters.
- `REQ-SAP-010`: Optional semantic similarity, cross-encoder reranking, or LLM
  evidence verification are future layers. Each must have its own privacy,
  latency, cost, and held-out metric gate before becoming available.
- `REQ-SAP-011`: Future semantic or LLM verifier layers may inspect only served
  candidate evidence and public-safe metadata. They must not answer from model
  prior knowledge or unserved source files.
- `REQ-SAP-012`: Strict profile output must keep citations tied to returned
  served documents and source refs. A citation from another page cannot justify
  an answerability admit decision.
- `REQ-SAP-013`: Public API changes are additive only. Defaults and existing
  response schemas remain compatible unless a future spec explicitly defines an
  opt-in diagnostic extension.
- `REQ-SAP-014`: Configuration names are future implementation details, but the
  compatibility shape must include `default` or `recall` behavior as the
  startup/request default and `strict` as an explicit opt-in profile.
- `REQ-SAP-015`: Strict-profile diagnostics, if added later, must separate
  retrieval rows, answerability decisions, orientation rows, and telemetry-only
  bundle rows.
- `REQ-SAP-016`: Orientation rows remain orientation telemetry. They may help an
  agent navigate, but they do not count as answer evidence for negative FPR,
  citation precision, citation recall, or answerability admission.
- `REQ-SAP-017`: Strict answerability state and reports must not persist private
  local paths, private endpoint URLs, raw private query logs, credentials,
  tokens, raw request bodies, unredacted Redis keys, or private source content.
- `REQ-SAP-018`: The profile must define a per-query latency and token budget.
  If optional future verifier layers exceed budget or are unavailable, the
  decision falls back to lexical-only behavior or abstains according to
  documented policy.
- `REQ-SAP-019`: Failure modes fail closed for answerability admission and fail
  open for baseline retrieval availability. A broken strict profile must not
  break default search/context.
- `REQ-SAP-020`: Rollback is immediate: disable the profile and ignore any
  strict-profile calibration artifacts or runtime state without source cleanup.

## Lexical Signal Envelope

The first strict profile should make a conservative, explainable decision from
current lexical evidence:

1. Run normal context retrieval with unchanged candidate generation.
2. Classify query requirements: topical, known-item, citation, numeric/literal,
   multi-hop, or hard-negative style.
3. Check whether significant query terms appear in returned evidence, titles,
   headings, paths, snippets, or citations according to the pre-registered
   envelope for that query class.
4. Require exact or near-exact literal support for numbers, versions, dates,
   identifiers, quoted phrases, and private-token style needles.
5. Admit answerability only when enough current candidates satisfy the envelope.
6. Abstain when evidence is missing, contradictory, citationless for a citation
   query, or supported only by orientation rows.

The envelope is intentionally broader than `score >= X`. It can combine several
weak lexical signals, but each signal and its range must be specified before
holdout evaluation.

## Calibration And Holdout

Calibration is allowed only to choose general envelope parameters:

- use a calibration split with public-safe judged queries and qrels
- include hard negatives, paraphrases, citation queries, numeric/literal
  queries, broad topical queries, and multi-hop queries
- record parameter ranges, chosen values, and rationale before holdout
- freeze implementation, parameters, tokenizer policy, and metric code before
  holdout
- evaluate on held-out real source variants and query variants
- publish only holdout metrics, with calibration metrics labeled as calibration

Held-out variants should be grouped by source family and query class so a
profile cannot pass by helping only one corpus shape.

## Acceptance Metrics

The future implementation is acceptable only when held-out real variants meet
all of these gates:

- answerable-query recall@5 remains at least `0.90` and regresses no more than
  3 percentage points from the default evidence-retrieval baseline
- negative-query false positive rate is at most `0.05`
- citation precision is at least `0.95`
- citation recall is at least `0.85` for citation-required queries
- numeric/literal and private-token style negatives have zero admitted answers
  in the held-out hard-negative subset
- orientation-only rows are excluded from answerability, retrieval-quality, and
  citation-quality gates and reported as telemetry-only when present
- p95 strict-profile latency and token use stay within the pre-registered budget
  unless a future ADR accepts a larger budget for materially better quality

Source mutation, private data leakage, unsupported citations, missing qrels, or
using calibration data as holdout are hard failures.

## Compatibility

- CLI and environment: additive opt-in profile controls only.
- HTTP/MCP/Streamable HTTP: default request behavior unchanged.
- OpenAPI: unchanged until a future implementation adds explicit opt-in
  controls or diagnostics.
- Search: default search remains a retrieval surface and is not converted into
  an answerability surface.
- Context/query: default context remains recall-oriented; strict profile is a
  separate opt-in decision layer.
- Benchmarks: strict answerability runs must use distinct run ids and must not
  reuse telemetry-only bundle rows as retrieval-quality evidence.

## Data Safety

Strict answerability may inspect query text and candidate evidence in memory for
the current request. Persisted artifacts may include only public-safe aggregate
metrics, parameter names, parameter values, source-family labels, query-class
labels, artifact digests, and redacted environment buckets.

No strict-profile artifact may persist raw private query logs, private local
paths, private endpoint URLs, credentials, tokens, request bodies, Redis keys,
or private source content.

## Failure Modes

- Weak lexical support: abstain.
- Missing citations on citation-required queries: abstain.
- Future verifier timeout or budget exhaustion: use lexical-only fallback or
  abstain according to configured policy.
- Corrupt calibration/runtime state: ignore state and use frozen defaults or
  abstain.
- Strict profile bug or operator rollback: disable the profile and restore
  baseline recall-oriented retrieval.

## References

- Verified benchmark spec: `specs/verified-source-benchmarks/`
- Managed generic context boundary:
  `docs/decisions/2026-07-30-managed-generic-markdown-sidecar-boundary.md`
- Search relevance spec: `specs/korean-numeric-search-relevance/`
- Architecture: `docs/architecture.md`
