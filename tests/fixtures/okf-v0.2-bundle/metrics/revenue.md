---
id: revenue-metric
type: Metric
title: Revenue Metric
description: Recognized revenue metric with policy provenance.
resource: metrics/revenue
tags:
  - finance
  - revenue
generated:
  by: agent:metric-bot
  at: 2026-06-30T14:00:00Z
verified:
  by: human:finance-lead
  at: 2026-07-01T09:00:00Z
status: stable
stale_after: 2026-12-31T00:00:00Z
usage_window:
  from: 2026-06-01T00:00:00Z
  to: 2026-06-30T00:00:00Z
sources:
  - id: rev-policy
    resource: policies/revenue-recognition.md
    title: Revenue Recognition Policy
    author: human:finance-lead
    usage_count: 42
    last_modified: 2026-06-15T00:00:00Z
    excerpt_hash: sha256:revpolicyfixture
x-llmwiki:
  project: finance-analytics
  relation: metric-contract
---
# Revenue Metric

zzokfrevenueapproved is an approved metric page.

The metric is consumed by [[../computations/revenue-ytd.md]].
