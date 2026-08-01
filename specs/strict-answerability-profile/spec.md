# Spec: Strict Answerability Boundary

## Status

Deferred / Boundary Only.

This spec records why `llmwiki-serve` should not add a strict answerability
runtime profile in the 0.2.7 RC candidate. It preserves benchmark-methodology
follow-up work without defining a serve API, CLI flag, environment variable,
OpenAPI extension, or runtime implementation.

## Problem

Current search and context defaults are recall-oriented. That is the right
default for agent context gathering, but the same posture is not a final
answerability decision: hard negative queries can still receive plausible
context rows.

The attempted strict lexical fix failed the product boundary. A query-level
lexical gate reached negative-query FPR `0` and admitted `0` hard negatives on
held-out experiment data, but it also lost `23.6` to `37.1` percentage points of
Recall@5 across OpenWiki and Pratiyush native/shadow profiles. That tradeoff is
not acceptable for a service whose core job is to preserve source evidence for
agents.

## Goals

- Keep `llmwiki-serve` recall-oriented and source/retrieval evidence oriented.
- Document that strict answerability is not a serve-owned runtime profile for
  this candidate.
- Keep negative answer abstention, model-backed verification, synthesized
  answer support, and final citation selection in `llmwiki-agent-bridge` or the
  host agent/RAG layer.
- Preserve future answerability benchmark methodology as a separate follow-up.
- Clarify that negative-query FPR in verified-source benchmarks is a retrieval
  stress metric, not serve answerability ownership.
- Keep all docs public-safe and free of raw private logs, private endpoint URLs,
  private local paths, credentials, tokens, or private source content.

## Non-Goals

- Do not implement runtime strict answerability in `llmwiki-serve`.
- Do not add or reserve public request parameters, config names, CLI flags,
  environment variables, OpenAPI fields, or MCP diagnostics for strict
  answerability.
- Do not change default result ordering, default context packs, search scoring,
  source refs, citation row shape, or orientation behavior.
- Do not tune a threshold to checked-in qrels, held-out queries, source family,
  profile name, or one benchmark run.
- Do not add embeddings, vector search, semantic reranking, cross-encoders, or
  model calls to serve for answerability verification.
- Do not make public benchmark or quality claims from the held-out lexical
  experiment.

## Held-Out Experiment Record

Two held-out source-family experiments were reviewed: OpenWiki and Pratiyush.
Each had native and shadow profiles. The record kept here is aggregate and
public-safe; it does not include raw query logs, local paths, private endpoint
URLs, or private source content.

The query-level lexical strict gate achieved:

- negative-query FPR: `0`
- hard negatives admitted: `0`

The same gate caused these Recall@5 regressions versus the recall-oriented
baseline:

| Held-out profile | Recall@5 delta |
| --- | --- |
| OpenWiki native | `-23.6` percentage points |
| OpenWiki shadow | `-26.1` percentage points |
| Pratiyush native | `-31.0` percentage points |
| Pratiyush shadow | `-37.1` percentage points |

Interpretation: the lexical gate proved that hard-negative admission can be
suppressed, but only by dropping too much answerable evidence. The result
supports rejecting a serve runtime strict profile and does not support a public
strict answerability quality claim.

## Requirements

- `REQ-SAP-001`: `llmwiki-serve` remains a recall-oriented source/retrieval
  evidence service by default.
- `REQ-SAP-002`: This spec does not define a strict answerability runtime
  profile for serve. Any runtime implementation requires a later ADR and a
  separate implementation spec.
- `REQ-SAP-003`: Search, query, context, read, graph, HTTP, MCP, Streamable
  HTTP, CLI, generated OpenAPI, package metadata, and version files remain
  unchanged by this boundary.
- `REQ-SAP-004`: Retrieved rows are candidate evidence. A row may be useful to
  inspect without proving that a final answer should be emitted.
- `REQ-SAP-005`: Orientation rows remain orientation telemetry. They may help an
  agent navigate, but they do not count as final answer support or final
  citation selection.
- `REQ-SAP-006`: Negative answer abstention belongs in
  `llmwiki-agent-bridge` or the host agent/RAG layer.
- `REQ-SAP-007`: Model-backed evidence verification belongs in
  `llmwiki-agent-bridge` or the host agent/RAG layer and should inspect only
  served candidate evidence and public-safe metadata unless another project
  boundary explicitly allows more.
- `REQ-SAP-008`: Final citation selection belongs in the answer-producing layer.
  Serve can expose citations attached to served rows, but it does not choose the
  final cited answer set.
- `REQ-SAP-009`: The held-out lexical experiment is recorded as boundary
  evidence only. It must not be presented as public strict answerability
  quality.
- `REQ-SAP-010`: Negative-query FPR in verified-source benchmarks is a
  retrieval stress metric for retrieval-evaluated serve surfaces. It is not an
  ownership claim for final-answer abstention.
- `REQ-SAP-011`: Future answerability benchmark methodology must be separate
  from runtime API design and must not require serve to emit an abstention
  decision.
- `REQ-SAP-012`: Public-safe benchmark methodology may include stratified
  source/query splits, per-query artifacts, frozen gates, public-safe reports,
  and pre-registered evaluation rules.
- `REQ-SAP-013`: Future methodology should be treated as a likely 0.2.8-style
  follow-up unless a later release plan explicitly reschedules it.
- `REQ-SAP-014`: Public artifacts must not persist private local paths, private
  endpoint URLs, raw private query logs, credentials, tokens, raw request
  bodies, unredacted Redis keys, or private source content.

## Boundary Model

`llmwiki-serve` owns:

- read-only projection of local Markdown-like source folders
- source-safe search, query, read, graph, and context surfaces
- citation/source-ref metadata attached to served rows
- orientation and payload telemetry
- compatibility smoke and retrieval benchmark surfaces

`llmwiki-agent-bridge` or the host agent/RAG layer owns:

- deciding whether retrieved evidence is enough to answer
- negative answer abstention
- model-backed verification over served evidence
- answer synthesis
- final citation selection
- unsupported-claim policy and answer-level quality reporting

## Future Benchmark Methodology

Future answerability stress work can continue as benchmark methodology, not as a
serve runtime API. The next methodology slice should define:

- stratified source-family and query-class splits
- separate calibration, validation, and held-out partitions if parameters are
  tested
- per-query public-safe artifacts for queries, qrels, retrieved rows, decisions,
  and redacted diagnostics
- frozen metric code and gates before held-out evaluation
- distinct reporting for retrieval stress, bridge/agent abstention, final
  citation support, and unsupported claims
- public-safe artifact digests rather than raw local paths or logs

Any future runtime answerability behavior must be proposed in the owning
answer-producing layer first, or return to this repository through a new ADR.

## Compatibility

- CLI and environment: unchanged.
- HTTP/MCP/Streamable HTTP: unchanged.
- OpenAPI: unchanged.
- Search/query/context/read/graph: unchanged.
- Benchmarks: may continue to include negative-query FPR as a retrieval stress
  metric, with wording that avoids treating serve as the final answerability
  owner.

## References

- ADR: `docs/decisions/2026-07-30-strict-answerability-profile-boundary.md`
- Held-out summary:
  `docs/research/2026-07-30-strict-answerability-heldout-summary.md`
- Verified benchmark spec: `specs/verified-source-benchmarks/`
- Managed generic context boundary:
  `docs/decisions/2026-07-30-managed-generic-markdown-sidecar-boundary.md`
- Architecture: `docs/architecture.md`
