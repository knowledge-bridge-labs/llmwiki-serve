# Tests: Verified Source Benchmarks

## Acceptance Criteria

- `REQ-BENCH-001`: Compatibility smoke and quality benchmark results are stored
  and reported as separate evidence tracks.
- `REQ-BENCH-002`: Compatibility rows include product name, official link,
  pinned commit when applicable, license evidence, source kind, source path,
  adapter, page count, graph size, mutation check, and evidence type.
- `REQ-BENCH-003`: Synthetic generated samples cannot be reported as actual
  upstream product output.
- `REQ-BENCH-004`: Actual pinned OpenWiki coverage must record a full commit
  SHA, official link, setup/static-output evidence, license evidence, and clear
  separation from synthetic OpenWiki-style fixtures.
- `REQ-BENCH-005`: Quality reports require corpus, queries, qrels, runs, and
  report files with public-safe schemas.
- `REQ-BENCH-006`: Deterministic quality reports compute recall@5, hit@5, MRR,
  nDCG@10, citation precision/recall, and context tokens for retrieval
  surfaces, plus payload bytes, p50/p95 latency, and run counts.
- `REQ-BENCH-007`: Public token counts use a recorded Qwen tokenizer id and
  revision, not byte/4 proxy counts.
- `REQ-BENCH-008`: DGX agent-tier reports record vLLM Qwen environment,
  task success, citation support, unsupported claim rate, tool calls,
  input/output/total tokens, wall time, and variance.
- `REQ-BENCH-009`: Windows and DGX reports use public-safe hardware bucket
  labels and do not include raw hostnames, private endpoint URLs, or local
  filesystem paths.
- `REQ-BENCH-010`: Public quality claims include minimum run counts and paired
  bootstrap 95% confidence intervals.
- `REQ-BENCH-011`: Hard failures override metric averages.

## Unit Tests To Add

- Validate compatibility inventory rows reject missing official links, floating
  refs, missing license evidence, and ambiguous evidence type.
- Validate synthetic rows cannot use `source_kind: actual-pinned`.
- Validate qrels parse relevance levels `0` through `3` and reject missing
  query ids or doc ids.
- Validate `global-map` is accepted as its own query class and appears
  separately in `metrics.<run>.query_classes`.
- Validate run rows reject missing ranks, missing or inconsistent
  `payload_tokens`, negative latencies, missing tokenizer provenance, and
  unknown surfaces while accepting distinct `service-context`,
  `service-context-orientation`, and telemetry-only `service-context-bundle`
  served surfaces.
- Validate `service-context-bundle` reports only payload-token, payload-byte,
  and latency telemetry and cannot fail public retrieval/citation/negative
  thresholds.
- Validate per-query-class report breakdowns use canonical query class keys and
  reject unknown class names or inconsistent class totals.
- Validate checked-in verified-source case manifests use the canonical
  `deterministic-public-path-id` citation mode when relying on deterministic
  public path-ID citation fallback.
- Validate corpus `sha256` uses canonical UTF-8 Markdown bytes, including LF vs
  CRLF equivalence, UTF-8 BOM normalization, compatibility with pinned OpenWiki
  git blob bytes, and failure on actual content changes.
- Validate checked-in verified-source case JSON, JSONL, and Markdown artifacts
  use LF line endings and fail on CRLF or bare CR.
- Validate manifest `benchmark_artifacts` and report `input_artifacts` use the
  same canonical UTF-8 text digest algorithm as corpus Markdown hashes rather
  than platform working-tree raw bytes.
- Validate standard recall@5, hit@5, MRR, nDCG@10, citation precision, and
  citation recall on a tiny hand-checked corpus.
- Validate token counting uses the configured Qwen tokenizer path/id and records
  fallback failure instead of silently using byte/4 proxy.
- Validate report redaction rejects private Windows paths, POSIX home paths,
  private endpoint URLs, bearer tokens, API key-looking values, raw Redis keys,
  and private scratch directories.
- Validate confidence interval computation is paired by query id and uses a
  recorded random seed.

## Integration Tests To Add

- Run existing 10 pinned upstream smoke cases with metadata collection enabled
  and verify every row is `actual-pinned`.
- Run existing 11 generated candidate samples and verify every row is
  `synthetic-generated`.
- Run the actual pinned OpenWiki case separately and verify it is not confused
  with synthetic OpenWiki-style fixtures or provider-backed OpenWiki generation.
- Run a deterministic quality smoke over a tiny public-safe fixture with at
  least one known-item, one multi-hop, one negative, one Korean/numeric, and one
  citation-required query.
- Run the same quality smoke through CLI/in-process service and HTTP surfaces
  to confirm matching page ids, citation ids, and draft filtering.

## Manual / Environment Checks

### Windows Local

- Run deterministic compatibility and quality smoke with the installed package
  and source checkout.
- Confirm public reports label the environment `windows-local`.
- Confirm reports include package version, Python version, query count, run
  count, tokenizer id, and no private local paths.

### DGX Spark / Ubuntu

- Run deterministic quality benchmark against the same public-safe corpus.
- Run the optional vLLM Qwen agent tier only after deterministic gates pass.
- Confirm public reports label the environment `dgx-spark-ubuntu`.
- Confirm reports include Qwen model id, tokenizer id, vLLM version, tool schema
  revision, seeds, calls, input/output/total tokens, success, citation support,
  unsupported claims, latency, and variance without exposing private endpoints.

## Hard-Fail Tests

- Source tree hash changes during serve validation.
- Draft/private evidence appears without explicit draft permission.
- Public report contains private path, private endpoint URL, credential, token,
  raw private content, unredacted Redis key, or cached Redis payload.
- Actual and synthetic evidence are mixed in one row.
- Compatibility smoke is labeled as retrieval quality.
- Quality report is missing qrels, query provenance, tokenizer provenance, or
  run counts.
- Citation-required answer cites an unsupported or out-of-source document.
- Hard negative stress query returns any row on a retrieval-evaluated surface,
  even if the row is a nearby refusal-evidence page. This is a retrieval stress
  failure, not a serve runtime abstention contract.

## Threshold Gates

Deterministic public quality rows must meet:

- recall@5 >= 0.90
- MRR >= 0.75
- nDCG@10 >= 0.85
- citation precision >= 0.95
- citation recall >= 0.85 for citation-required queries
- negative-query false positive rate <= 0.05 as a retrieval stress threshold,
  not a final-answer abstention API
- context-token p95 within 20% of the raw selected-document baseline unless a
  measured recall, citation, or agent-success improvement justifies the cost
- served warm p95 latency regression <= 25% from the comparable prior report
  unless the report records a correctness or freshness reason

Agent public quality rows must meet:

- task success >= 0.80 on answerable tasks
- citation support >= 0.90 on citation-required tasks
- unsupported claim rate <= 0.05
- calls, tokens, or wall time increase <= 20% unless success or citation support
  improves by at least 5 percentage points

## Validation Commands For Future Implementation

```powershell
uv run pytest -q tests/test_upstream_candidate_smoke.py
uv run pytest -q tests/test_candidate_samples.py
uv run pytest -q tests/test_verified_source_benchmarks.py
uv run ruff check .
uv run ruff format --check .
```

The future DGX agent tier should record its exact command in the benchmark
report because the vLLM endpoint, Qwen model, and hardware bucket are external
to the normal local unit-test environment.
