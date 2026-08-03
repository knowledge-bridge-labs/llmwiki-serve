# LLMWiki Orientation Mechanism Benchmark

This directory contains a curated functional benchmark for the production
orientation-seeded hybrid retrieval mechanism. It verifies that root
`hot.md` and `index.md` orientation pages can lift related target pages through
visible links, source refs, and tags before the global vector channel is fused.

This is not an external retrieval-quality benchmark and must not be used as a
language-quality headline. The fixture is synthetic, public-safe Markdown
written only to exercise the mechanism contract:

- query-relevant orientation snippets may lift linked paraphrase targets;
- high-degree boilerplate links outside the matched snippet do not dominate;
- exact identifier results are preserved;
- a missing-orientation copy falls back exactly to plain lexical/vector RRF;
- draft-linked targets do not leak in approved-only mode;
- adversarial orientation cases report hard invariant failures separately from
  measured residual risk where explicit visible malicious relations can steer
  retrieval.

The adversarial cases cover high-degree generic hubs, stale or deleted link
targets, prompt-injection-like prose, explicit malicious distractor links,
malicious source_ref/tag relations, exact identifiers under poisoned hints,
approved orientation links to draft targets, duplicate/alias links, and Korean
NFC/NFD labels. These are public-safe regression gates only; they do not claim
poisoning safety.

Run it from a source checkout with the vector extra installed and an already
cached FastEmbed multilingual model:

```powershell
uv run python -m scripts.benchmark_adapters.orientation_mechanism_runner `
  --fixture-dir benchmarks/orientation_mechanism/fixture `
  --output-report .llmwiki-work/benchmark-adapters/orientation-mechanism/report.json `
  --vector-model-cache-root .llmwiki-work/benchmark-adapters/scifact/fastembed-model-cache
```

The runner always uses `model_download=never`. The report is sanitized: it
contains aggregate metrics, query IDs, target ranks, timing percentiles, and
cache/provider telemetry, but not raw query text, document text, local paths,
private endpoints, or credentials.
