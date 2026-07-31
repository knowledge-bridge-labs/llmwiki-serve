# Spec: Lexical Postings Index

## Status

Implemented locally for this feature branch. Derived in-memory postings are in
the search corpus path, literal mode remains unchanged, and Windows repository
validation plus SciFact runs have passed. Final immutable-revision Windows and
DGX Spark Ubuntu public reports for
`git:9f03f39666edf0d2516cf1f6d9c7171802eabd2c` pass public report validation
with identical deterministic quality metrics. Release validation, merge,
package publish, and hosted docs deployment remain pending.

## Problem

Lexical search currently scans every `SearchDocument` and evaluates every query
token against every document. This preserves correctness but makes repeated
warm searches slow on larger corpora such as full benchmark projections.

## Goals

- Add a derived in-memory postings index for lexical search.
- Preserve lexical search output exactly, including score rounding, tie/path
  order, role and managed-context prior behavior, exclusions, and `min_score`.
- Keep literal search mode unchanged.
- Keep excluded documents in corpus-level statistics.
- Reduce warm search p95 latency by at least 25% on repeated runs in the same
  environment, with index build time reported separately.

## Non-Goals

- No persistence, disk index, cache file, database, Redis, or background writer.
- No HTTP, MCP, CLI, OpenAPI, or public model/schema change.
- No quality, tokenizer, analyzer, stemming, ranking, snippet, payload, or
  benchmark metric change.
- No change to literal exact-substring search.

## Requirements

- `REQ-LPI-001`: `SearchCorpus` may hold derived in-memory postings keyed by
  lexical tokens and document indexes.
- `REQ-LPI-002`: `_IndexViews` may reuse the derived postings through its
  existing cached `SearchCorpus` lifecycle.
- `REQ-LPI-003`: lexical search must evaluate only candidate documents from
  query-token postings while computing the same scores as the current all-doc
  scan.
- `REQ-LPI-004`: lexical result lists and serialized results must be exactly
  preserved for the same input corpus and query options.
- `REQ-LPI-005`: score rounding, tie ordering, path ordering, role boosts,
  managed-context priors, `exclude_page_ids`, and `min_score` semantics must be
  byte-for-byte compatible with the current behavior.
- `REQ-LPI-006`: excluded documents remain included in corpus statistics such
  as document count, document frequency, document length, and average document
  length.
- `REQ-LPI-007`: both approved-only and all-drafts corpus views must build and
  use equivalent derived postings without sharing mutable state incorrectly.
- `REQ-LPI-008`: normalized input and source bundle checksums, quality metrics,
  result payloads and their checksums, and payload byte quantiles must remain
  unchanged after the optimization. The report file checksum is expected to
  change when latency or index-build telemetry changes.
- `REQ-LPI-009`: warm repeated-run search p95 latency must improve by at least
  25% in the same environment. Index build time is measured and reported
  separately from search latency.

## Compatibility

This is an internal implementation optimization. There is no public contract
change for HTTP, MCP, CLI, OpenAPI, configuration, benchmark artifact schemas,
or persisted files.

## Data Safety

The implementation must not write corpus text, query text, postings, local
paths, private hosts, credentials, provider configuration, benchmark archives,
or run manifests into tracked files. Public reports may include only sanitized
aggregate metrics and checksums already permitted by the benchmark reporting
spec.

## ADR Assessment

No ADR is required while the index remains a derived in-memory implementation
detail inside `SearchCorpus` and `_IndexViews`. Create or update an ADR before
implementation only if the design crosses a persistence, public contract,
runtime ownership, or cache-invalidation boundary.
