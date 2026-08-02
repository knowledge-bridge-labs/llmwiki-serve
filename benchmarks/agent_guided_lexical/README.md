# Agent-Guided Lexical Benchmark Harness

This directory contains a tiny public-safe fixture for exercising the
agent-guided lexical retrieval workflow. It is an engineering harness, not a
release benchmark or public superiority claim.

The runner accepts externally generated agent plans in
`fixture/agent-plan.jsonl`. The runner does not call an LLM and does not use
qrels to produce variants.

The fixture has two separate served corpora:

- `fixture/authored/wiki`: includes root `hot.md` and `index.md` orientation
  pages and must report `retrieval_guidance.orientation_source=authored`.
- `fixture/projection/wiki`: contains only generic topic pages, with no
  `hot.md`, `index.md`, or `overview.md`, and must report
  `retrieval_guidance.orientation_source=projection_extractive`.

Fixture coverage:

- English authored-orientation style query
- Korean query and Korean source text
- Code identifier query preserving dotted and snake-case symbols
- Negative query with no qrels
- Prompt-injection-like source prose treated only as source text

Plan rows must include versioned provenance: generator kind/model or human
fixture identity, prompt/template revision and SHA-256, source-context/input
digests, timestamp or stable fixture marker, and token accounting source. The
runner rejects forbidden gold/citation/qrel fields and exact positive qrel
identifier/path/page-id matches after NFC, casefold, and path normalization.
That check cannot prove semantic leakage absent. Release evidence must use
independently generated source-only plans before qrels are available.

The runner verifies only in-process service-instance isolation by requiring
each factory call to return a unique service object. It does not claim cold
filesystem, vector, model, OS, or provider cache state; `cold_usage_cache` is
reported as unknown unless a future provider supplies direct evidence. Skipped
hybrid arms are not measured zeroes: they carry null metrics/distributions,
empty per-query rows, zero/not-applicable usage, and an explicit limitation.

Run locally:

```powershell
uv run python scripts/benchmark_adapters/agent_guided_lexical_runner.py --fixture-dir benchmarks/agent_guided_lexical/fixture
```

Reports must remain aggregate and public-safe. Do not publish dirty-worktree
numbers or this tiny fixture as release evidence.
