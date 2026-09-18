---
id: revenue-ytd-computation
type: Attested Computation
title: Revenue YTD Computation
description: Attested year-to-date revenue computation.
resource: computations/revenue-ytd
runtime: python
parameters:
  - name: fiscal_year
    type: integer
computation: sum(recognized_revenue where fiscal_year = fiscal_year)
executor:
  image: ghcr.io/example/revenue-ytd:fixture
attester:
  kind: fixture
  statement: deterministic-test-attestation
generated:
  by: agent:analytics-bot
  at: 2026-07-01T10:00:00Z
verified:
  - by: agent:ci-attester
    at: 2026-07-01T10:10:00Z
status: stable
sources:
  - resource: metrics/revenue
    title: Revenue Metric
    usage_count: 3
---
# Revenue YTD Computation

zzokfattestedapproved should remain readable as structured metadata only.
