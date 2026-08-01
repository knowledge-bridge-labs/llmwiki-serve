# ADR: Strict Answerability Boundary

## Status

Proposed / Decision Candidate.

## Context

`llmwiki-serve` is optimized to serve read-only Markdown projections as useful
agent context. Its search, query, read, graph, and context surfaces should favor
recall: agents often need nearby pages, orientation pages, graph hints, and
citation candidates before deciding what to inspect next.

That recall-oriented posture is not an answerability guarantee. A service row
can be useful retrieval evidence without proving that a final answer should be
given. The question "what source material should the agent inspect?" is a
different product boundary from "should the final answer abstain?"

Exploratory strict answerability work tested a query-level lexical gate against
two held-out source families, OpenWiki and Pratiyush, with native and shadow
profiles. The strict gate achieved negative-query FPR `0` and admitted `0` hard
negatives. It also removed too much legitimate evidence, with Recall@5 losses
against the recall-oriented baseline:

| Held-out profile | Recall@5 delta |
| --- | --- |
| OpenWiki native | `-23.6` percentage points |
| OpenWiki shadow | `-26.1` percentage points |
| Pratiyush native | `-31.0` percentage points |
| Pratiyush shadow | `-37.1` percentage points |

The result is clear enough for a boundary decision: hard-negative abstention can
be forced lexically, but not without unacceptable retrieval loss for serve's
core job. These experiment results are not a public quality claim.

## Decision

Reject a strict answerability runtime profile for `llmwiki-serve` in this
candidate. Do not add public runtime controls, schemas, generated OpenAPI,
package metadata, version changes, or default behavior changes for strict
answerability in the serve package.

`llmwiki-serve` remains recall-oriented and source/retrieval evidence oriented.
It owns local projection, source-safe reads, search, context assembly,
orientation telemetry, source refs, citation-bearing retrieved rows, and
read-only service compatibility.

Negative answer abstention, model-backed evidence verification, synthesized
answer support, and final citation selection belong in `llmwiki-agent-bridge` or
the host agent/RAG layer. Those layers may call `llmwiki-serve` for candidate
evidence, then decide whether the evidence is enough to answer.

Negative-query FPR in verified-source benchmarks remains a retrieval stress
metric. It measures how often retrieval-evaluated serve surfaces return rows for
unanswerable or hard-negative queries. It does not make `llmwiki-serve` the
owner of final answer abstention, final citation selection, or model-backed
verification.

Future benchmark-methodology work is preserved as a separate follow-up, not a
runtime API. A likely 0.2.8-style follow-up may define stratified source/query
splits, per-query artifacts, frozen gates, public-safe run reports, and
pre-registered evaluation rules for answerability stress testing. That work
should remain benchmark/report methodology unless a later ADR explicitly
reopens the runtime boundary.

## Consequences

- Default search, query, context, HTTP, MCP, Streamable HTTP, CLI, and OpenAPI
  behavior remain unchanged.
- The service avoids shipping a low-recall abstention gate as a public API.
- Retrieval metrics, answerability stress metrics, orientation telemetry, and
  final-answer quality stay separate.
- Agent and RAG layers can implement stricter answer policies without forcing
  all serve clients through an abstention-oriented gate.
- Public docs must not claim strict answerability quality from the held-out
  lexical experiment.

## Follow-Ups

- Keep `specs/strict-answerability-profile/` as a boundary and deferred
  methodology spec, not an implementation spec for serve runtime behavior.
- Update verified-source benchmark wording so negative-query FPR is explicitly
  a retrieval stress metric.
- If answerability evaluation resumes, define a public-safe 0.2.8-style
  benchmark-methodology slice with stratified splits, per-query artifacts, and
  frozen gates before running or publishing results.
- Route runtime abstention, model-backed verification, and final citation
  selection design to `llmwiki-agent-bridge` or the host agent/RAG layer.

## References

- Spec: `specs/strict-answerability-profile/`
- Verified benchmark spec: `specs/verified-source-benchmarks/`
- Held-out summary:
  `docs/research/2026-07-30-strict-answerability-heldout-summary.md`
- Managed generic context ADR:
  `docs/decisions/2026-07-30-managed-generic-markdown-sidecar-boundary.md`
- Architecture: `docs/architecture.md`
