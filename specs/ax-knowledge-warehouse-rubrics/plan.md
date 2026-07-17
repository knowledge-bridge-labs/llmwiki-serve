# Plan: AX Knowledge Warehouse Rubrics

## Loop Strategy

Each loop should:

1. Run Loop 0 for feature, contract, architecture, or security work: review
   prior art and library fit from trusted sources, then record the
   adopt/wrap/defer/custom decision before implementation.
2. Pick the lowest-scoring rubric or a rubric with recent code churn.
3. Add or refine deterministic tests before broad implementation.
4. Prefer small compatibility-preserving changes.
5. Run focused tests plus contract/lint checks for touched repositories.
6. End with a briefing and rubric table.

## Current Loop Mapping

| Loop | Primary rubric | Validation artifact |
| --- | --- | --- |
| Loop 0 | Prior-art and library fit | `PA-010` gate in `specs/ax-knowledge-warehouse-rubrics/tests.md`; concise research note when the fit is non-obvious |
| Loop 1 | Freshness and snapshot integrity | `tests/test_freshness_loop_matrix.py` |
| Loop 2 | Evidence fidelity, safety | bridge evidence ID scoping and chat runtime token persistence tests |
| Loop 3 | Agent traversal utility | `tests/test_graph_traversal_loop_matrix.py` and bridge `llmwiki_graph_neighbors` tests |
| Loop 4 | Loop observability, safety governance | governance/redaction matrices for serve and bridge |
| Loop 5 | Connection setup and interoperability | chat direct/bridge connection tests, bridge readiness tests, serve discovery tests, and local e2e |

## Rollout

Keep these files in the repository and ingest them into the project LLMWiki after
validation. The LLMWiki projection should help agents recall the loop criteria,
but the tracked spec remains canonical.
