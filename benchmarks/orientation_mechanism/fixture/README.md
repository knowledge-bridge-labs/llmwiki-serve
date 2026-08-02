# Curated Orientation Fixture

This fixture is synthetic public-safe Markdown for the LLMWiki orientation
mechanism benchmark. It is intentionally small and hand-labeled. The qrels are
functional assertions about retrieval behavior, not a claim about external
retrieval quality.

The `wiki/` folder includes canonical root `hot.md`, `index.md`, and
`overview.md` orientation pages, approved target pages, semantically similar
distractors, and one draft page. The benchmark runner also creates a temporary
copy without orientation pages to verify exact plain-RRF fallback behavior.

The adversarial rows are synthetic public-safe fixtures. They are intended to
measure invariants such as draft suppression, cap enforcement, missing-target
handling, exact identifier preservation, and transparent orientation
diagnostics. Explicit malicious links or relation labels are reported as
residual risk when they steer approved results; the fixture does not tune or
claim production poisoning safety.
