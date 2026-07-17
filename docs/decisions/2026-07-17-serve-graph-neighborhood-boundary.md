# Serve Graph Neighborhood Boundary

## Status

Draft.

## Context

CKG-like workflows are useful when a host agent needs stable dependency,
prerequisite, ownership, policy, architecture, or source-lineage relationships
without reading a large wiki or asking a model to rediscover those
relationships from raw text.

`llmwiki-serve` already projects Markdown pages, wikilinks, source references,
tags, headings, and optional `graph/graph.json` facts into a read-only graph.
The gap is a compact neighborhood primitive that agent-facing skills can call
after initial context discovery.

## Decision

Add graph-neighborhood lookup to `llmwiki-serve` as an additive read-only
serving surface.

`llmwiki-serve` remains responsible for one source-owned projection. It does not
generate CKG data, own multi-source orchestration, run models, or execute DB/RAG
tools. Host agents and `llmwiki-agent-bridge` use neighborhood lookup as a
follow-up tool after `/query`, source-bundle inspection, or search.

Agent-facing skills should teach clients when to call neighborhood lookup. The
skills should not embed the graph itself.

## Consequences

- Agents can fetch compact relationship neighborhoods instead of full graphs.
- The operation can reduce serialized payload and prompt size for CKG-like
  follow-up inspection.
- The public API grows by one HTTP endpoint and one MCP tool.
- Neighborhood completeness depends on producer-authored wiki links and sidecar
  facts; it is not a replacement for search or exact reads.

## Follow-Ups

- Update `llmwiki-agent-bridge` integration skills to mention graph-neighborhood
  lookup once this surface is available there.
- Consider source-bundle guidance fields for recommended seed nodes.
- Consider richer sidecar metadata only after public-safe schema rules are
  defined.
