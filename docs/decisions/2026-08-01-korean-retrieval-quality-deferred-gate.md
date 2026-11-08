# 2026-08-01 Decision Record: Korean Retrieval Quality Deferred Gate

## Status

Accepted as an ADR-only future-work boundary. This change does not add a Korean
runtime analyzer, API, dependency, benchmark implementation, test, spec,
README, version, package metadata, or default behavior change. `legacy` remains
the product default, and analyzer selection remains explicit.

## Context

The current `english` analyzer optimization is English-specific: it applies
English token splitting, possessive handling, stopword removal, and stemming.
Language-neutral work such as postings/index-speed improvement may be evaluated
separately, but English retrieval gains do not establish Korean or multilingual
retrieval quality.

The current and legacy tokenizer recognizes contiguous Hangul syllable runs and
adds short Hangul bigrams for longer runs. That is useful token recognition, but
it is not a Korean morphological analyzer and is not evidence of Korean
retrieval quality.

The current representative retrieval evidence is SciFact for the English
opt-in path. It uses nDCG@10 and Recall@100 as primary metrics, with Recall@5,
Hit@5/10, MRR@10, latency, and payload telemetry as secondary diagnostics. The
benchmark adapter layer already separates tiny synthetic CI fixtures from full
authoritative benchmark reports.

## Decision

Defer Korean retrieval quality claims until Korean evidence exists. Do not
generalize English analyzer or SciFact results into Korean, multilingual, or
language-neutral quality claims.

Future Korean evaluation should use an authoritative, provenance-pinned Korean
IR dataset. MIRACL-ko is the preferred representative candidate only if
licensing, provenance, redistribution/reporting policy, and operational
feasibility are verified. Do not claim MIRACL-ko support or implementation now.

Use the same retrieval metric family as SciFact: nDCG@10 and Recall@100 as
primary metrics, plus useful product-secondary Hit, MRR, and Recall cutoffs
such as Recall@5, Hit@5/10, and MRR@10. Quality metrics should be deterministic
across OSes; latency remains environment telemetry and is tracked separately.

Establish the current baseline first, then evaluate optional OSS analyzer
candidates such as Kiwi/kiwipiepy and a character n-gram fallback. MeCab-ko is
eligible only if its portability and operating burden are acceptable for the
supported release environments. Preserve explicit profile selection and the
legacy default until measured Korean evidence supports a change.

Add a two-tier benchmark gate policy for future Korean work:

1. PR CI may use deterministic, provenance-pinned small Korean fixtures or
   subsets for fast correctness and gross quality regression detection. These
   scores must never be advertised as headline benchmark performance.
2. Release or deployment gates require a full authoritative representative
   benchmark report tied to the exact candidate commit, analyzer/config,
   dataset revision/checksums, and threshold policy before package or docs
   deployment. A workflow may verify an immutable externally generated report
   instead of rerunning a large corpus on every job, but it must reject stale,
   mismatched, mutable, or wrong-configuration artifacts.

SciFact remains the current English representative evidence and release-gate
candidate/baseline; it does not imply CI or publish workflows currently enforce
quality thresholds. The English gate candidate does not activate any Korean
gate. Future Korean release gating requires baseline establishment and explicit
thresholds before enforcement: at minimum, an absolute floor plus allowed
regression policy for the selected representative dataset and analyzer/config.
Korean claims require Korean benchmark evidence on Windows and Ubuntu/DGX where
feasible.

## Non-Goals

- Do not add or expose a Korean analyzer profile in this task.
- Do not change HTTP, MCP, MCP Streamable HTTP, A2A-style, CLI, Python, or
  OpenAPI contracts.
- Do not add benchmark corpora, downloaded archives, materialized Korean data,
  generated reports, dependencies, tests, or CI workflows in this task.
- Do not change specs, README files, package metadata, release version, or
  public documentation behavior in this task.
- Do not claim MIRACL-ko, Kiwi/kiwipiepy, MeCab-ko, character n-grams, or any
  Korean benchmark gate as implemented.
- Do not use tiny synthetic fixtures or subsets as marketing, release, or
  headline benchmark evidence.

## Consequences

- English analyzer gains stay scoped to English evidence.
- Existing Hangul token recognition remains an implementation detail of the
  current tokenizer, not a Korean retrieval quality claim.
- General multilingual and Korean performance claims are prohibited until an
  accepted Korean benchmark report exists.
- Korean support will require license/provenance review, baseline reports,
  explicit thresholds, and cross-environment evidence before release gating.
- CI can guard future Korean plumbing cheaply, but release or docs claims need
  a full representative report or strict verification of an immutable report.
- The default ranking behavior remains stable until evidence justifies a new
  explicit profile or default change.

## Follow-Up Work

- Verify MIRACL-ko license, provenance, checksums, report policy, and local
  operating requirements before selecting it as the Korean representative gate.
- Define Korean benchmark materialization/reporting work under the benchmark
  adapter layer without committing corpus text or local run manifests.
- Add tiny synthetic Korean fixtures only for deterministic CI correctness and
  gross-regression checks.
- Run baseline measurements for the current runtime configuration before
  testing candidate analyzers.
- Evaluate Kiwi/kiwipiepy, character n-gram fallback, and MeCab-ko only after
  portability and release-environment constraints are explicit.
- Set Korean release thresholds after baseline acceptance, including an
  absolute floor and allowed regression policy.
- Add release/deployment validation that rejects stale or mismatched Korean
  benchmark reports.

## References

- English analyzer ADR:
  `docs/decisions/2026-08-01-language-aware-lexical-analyzer.md`
- English analyzer spec: `specs/english-lexical-analyzer/spec.md`
- Benchmark artifact ADR:
  `docs/decisions/2026-07-31-cross-repo-benchmark-artifact-contract.md`
- Benchmark adapter spec: `specs/benchmark-adapter-layer/spec.md`
- Benchmark adapter tests: `specs/benchmark-adapter-layer/tests.md`
- SciFact report README: `benchmarks/verified_sources/reports/README.md`
- MIRACL: https://github.com/project-miracl/miracl
