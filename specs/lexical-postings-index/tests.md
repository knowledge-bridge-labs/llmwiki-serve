# Tests: Lexical Postings Index

## Acceptance

- Lexical search returns exactly the same serialized results before and after
  the optimization for representative queries.
- Score rounding, tie/path order, role and managed-context prior behavior,
  `exclude_page_ids`, and `min_score` remain unchanged.
- Literal search results are unchanged.
- Excluded documents remain present in document frequency, document length, and
  average document length calculations.
- Approved-only and all-drafts views each build correct independent postings.
- Normalized input and source bundle checksums, benchmark quality metrics,
  result payloads and their checksums, and payload byte quantiles are
  unchanged. The report file checksum changes as expected when latency or
  index-build telemetry changes.
- Warm repeated-run search p95 latency improves by at least 25% in the same
  environment, with index build time reported separately.

## Validation

- Focused unit tests for postings construction and corpus statistics.
- Focused service-level parity tests for lexical ranking and filtering options.
- Existing literal-mode tests.
- Full test suite after implementation.
- Windows full repository validation and temporary current-default SciFact run.
- Full SciFact benchmark repeat on DGX Spark Ubuntu.
- Final immutable-revision public Windows and DGX Spark Ubuntu report
  validation before release evidence is accepted.
