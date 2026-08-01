# Strict Answerability Held-Out Summary

## Status

Public-safe experiment summary. Not a public quality claim.

## Scope

This note records the aggregate result used for the strict answerability
boundary decision. It covers two held-out source-family experiments, OpenWiki
and Pratiyush, each evaluated with native and shadow profiles.

This note intentionally excludes raw query logs, private local paths, private
endpoint URLs, credentials, tokens, and private source content.

## Result

The tested query-level lexical strict gate achieved:

- negative-query FPR: `0`
- hard negatives admitted: `0`

The same gate reduced Recall@5 versus the recall-oriented baseline:

| Held-out profile | Recall@5 delta |
| --- | --- |
| OpenWiki native | `-23.6` percentage points |
| OpenWiki shadow | `-26.1` percentage points |
| Pratiyush native | `-31.0` percentage points |
| Pratiyush shadow | `-37.1` percentage points |

## Interpretation

The gate successfully blocked the held-out hard negatives, but the recall loss
is too large for `llmwiki-serve`, whose core responsibility is preserving useful
retrieval evidence for agents.

This result supports rejecting a strict answerability runtime profile in serve.
Negative answer abstention, model-backed verification, and final citation
selection should be handled by `llmwiki-agent-bridge` or the host agent/RAG
layer.

## Follow-Up

Future answerability work should continue as benchmark methodology rather than
as a serve runtime API unless a later ADR reopens the boundary. A likely
0.2.8-style follow-up should define stratified splits, per-query artifacts,
frozen gates, and public-safe reports before any new held-out evaluation.
