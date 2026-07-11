# Chat and Agent Bridge Query E2E Notes

Date: 2026-07-11

This note captures a local check of `llmwiki-chat -> llmwiki-agent-bridge -> llmwiki-serve` behavior. It is an investigation record for later design discussion, not a product decision.

## Local Setup Shape

- `llmwiki-chat`: local Vite development server
- `llmwiki-agent-bridge`: local loopback bridge
- Bridge runtime profile: Hermes profile backed by an OpenAI-compatible chat completions gateway
- Bridge protocol surface:
  - A2A-compatible: `/.well-known/agent-card.json`, `/message:send`
  - MCP-compatible: `/mcp`
- Bridge source policy: private HTTP sources allowed for local/private-network development
- Registered bridge sources: 6 local fixture sources

The checked source set included sample, native markdown, Obsidian-style, Logseq-style, compiler-output, and 20-topic wiki fixtures.

## What Worked

### Direct `llmwiki-serve` Query

Direct query against a single 20-topic wiki source worked.

- Query: `What is this wiki about and what are the main agent topics?`
- Latency: 78 ms
- Citations: 8
- Graph nodes: 93
- First evidence titles:
  - `Project Overview`
  - `Wiki Index`
  - `Host Agent`
  - `Direct Source Mode`
  - `Host Agent RAG Orchestration`

This confirms that the source projection layer can answer both overview-style and topic-oriented queries.

### Bridge Source Discovery

Bridge source discovery worked through MCP.

- Tool: `llmwiki_list_sources`
- Latency: 2 ms
- Sources returned: 6

This is also the source list surfaced in `llmwiki-chat` as bridge-managed sources.

### Bridge Global Evidence-Only Query

Bridge registry fallback worked when no explicit `knowledgeSources` array was supplied.

- Tool: `llmwiki_agent_run`
- Mode: `evidence-only`
- Query: `What is in these wikis? Give a concise global map with citations.`
- Latency: 520 ms
- Citations: 15
- Graph: 150 nodes, 174 edges
- Trace shape:
  - `Plan source calls`
  - one `Call <source>` step per registered source
  - one `Read <source> source bundle` step per registered source
  - `Prepare evidence`
  - `Build evidence-only answer`
  - `Return A2A artifact`

This is the intended fast global-map behavior. It does not call the configured synthesis runtime.

### Bridge Local Evidence-Only Query

Single-source bridge query also worked.

- Mode: `evidence-only`
- Latency: 185 ms
- Citations: 9
- Graph: 93 nodes, 120 edges
- Trace shape:
  - `Plan source calls`
  - `Call <source>`
  - `Read <source> source bundle`
  - `Prepare evidence`
  - `Build evidence-only answer`
  - `Return A2A artifact`

This is a good fit for quick local inspection or for a host agent that wants evidence before deciding whether to ask for synthesis.

### Bridge Local Delegated Runtime Query

Single-source delegated runtime worked through the configured OpenAI-compatible chat completions gateway.

- Mode: `delegated-runtime`
- Latency: 37.7 seconds
- Citations: 9
- Graph: 93 nodes, 120 edges
- Trace shape:
  - `Plan source calls`
  - `Call <source>`
  - `Read <source> source bundle`
  - `Prepare evidence`
  - `Call chat completions`
  - `Return A2A artifact`

The answer was a synthesized markdown response with citation anchors. This is the intended path when the user wants a polished grounded answer rather than a deterministic evidence report.

## Gaps Observed

### Chat UI Does Not Expose Evidence-Only Mode

`llmwiki-agent-bridge` supports `evidence-only`, `delegated-runtime`, and `hybrid`, but `llmwiki-chat` currently defaults to the selected runtime path. In practice this means a global multi-source query from chat tends to call the runtime, even when an evidence-only overview would be much faster.

Observed result:

- UI query over six selected bridge sources did not complete within 180 seconds.
- The same global question in bridge `evidence-only` mode completed in 520 ms.

Improvement candidate:

- Add an answer mode control in chat:
  - `Fast evidence map`
  - `Grounded answer`
  - `Hybrid`
- Default may need to depend on selected source count. For many sources, evidence-first may be a better default.

### Chat Multi-Turn Is UI-Level, Not Runtime-Level

Follow-up behavior is not truly conversation-aware at the runtime boundary.

Current shape:

- `chat` stores previous messages in the UI.
- Runtime calls send the current `query`, selected ready sources, runtime context, and tool descriptors.
- Prior user/assistant turns are not passed as a conversation history contract.

Observed bridge follow-up without history:

- Query: `Which of those topics should we improve first, and why?`
- Mode: `evidence-only`
- Source: one selected topic wiki
- Latency: 121 ms
- Citations: 8

The bridge can still retrieve useful evidence from standalone wording, but pronouns like "those topics" are not grounded in the previous answer unless the user repeats enough context.

Improvement candidate:

- Define a compact `conversationContext` contract for chat-to-runtime calls.
- Include recent question, answer summary, selected citations, and current source scope.
- Keep this optional to preserve stateless host-agent usage.

### Source Selection UI Has Hit-Target Problems

While trying to reduce selected bridge sources from six to one through the UI, Playwright hit repeated pointer interception by the sidebar/section summary.

Observed failure:

- A source checkbox was visible but click/uncheck was intercepted by `.sidebar` or `.sidebar-section-summary`.
- Force click also did not reliably change checkbox state.

Improvement candidate:

- Rework source cards so checkbox labels have stable hit areas.
- Avoid collapsible summary overlays intercepting source card controls.
- Add `Use only this source` or `Select all / clear all` at the section level for bridge-managed source groups.
- Consider grouping:
  - `From Agent Bridge`
  - `Direct Sources`

### Bridge-Managed Source Graphs Are Not Loaded Into Chat Map

The bridge-managed sources were visible and selected, but the current knowledge map summary showed zero pages, links, and source refs until direct source graph discovery is available.

This is expected with the current implementation because bridge-managed sources are trusted as ready from bridge registry metadata, not individually discovered by the browser.

Improvement candidate:

- Let chat fetch graph metadata through the bridge instead of direct source URLs.
- For bridge-managed sources, call bridge MCP source tools:
  - `llmwiki_graph`
  - `llmwiki_read`
  - `llmwiki_source_bundle`
- This would make chat independent of whether the browser can reach every source URL directly.

### Global Delegated Runtime Needs Budgeting

The direct bridge global evidence-only path is fast, but global delegated synthesis can be expensive when it includes several source bundles and many citations.

Improvement candidate:

- Add runtime budget controls:
  - max citations passed to synthesis
  - max source bundle metadata
  - max graph nodes in runtime prompt
  - timeout per mode
- Add automatic fallback:
  - if delegated runtime exceeds a threshold, return evidence-only result with a diagnostic suggesting narrowed scope.

### Hybrid Mode Is Not Product-Defined Enough

`hybrid` exists as a bridge mode, but its UX meaning is not clear from the chat surface.

Improvement candidate:

- Define product semantics:
  - evidence-only: deterministic evidence report
  - delegated-runtime: runtime produces final answer from prepared evidence
  - hybrid: return fast evidence immediately, then optionally synthesize or refine
- Decide whether hybrid is streaming/progressive or just a different orchestration label.

## Suggested Next Test Matrix

| Area | Test | Expected Signal | Open Question |
| --- | --- | --- | --- |
| Global bridge registry | Omit `knowledgeSources`; run `evidence-only` | One tool call per registered source; merged citations and graph | Should chat ever omit inline sources and let bridge registry decide? |
| Direct source test | Register one `llmwiki-serve` URL directly in chat | Browser can discover manifest/query/graph directly | Keep this first-class for debugging |
| Bridge-managed source test | Connect bridge only, no direct sources | Sources appear from bridge registry | Need bridge-mediated graph/read for full map |
| Local evidence-only | One selected source, `evidence-only` | Sub-second deterministic answer | Chat needs mode control |
| Local delegated | One selected source, `delegated-runtime` | `Call chat completions`, cited markdown answer | Latency depends on configured model |
| Global delegated | Several selected sources, `delegated-runtime` | Should complete or gracefully fall back | Current UI path timed out at 180 seconds |
| Multi-turn follow-up | Ask overview, then pronoun follow-up | Runtime should receive compact conversation context | No current history contract |
| Zero-evidence source | Ask source that returns graph but no citations | Done step plus clear "0 evidence" state | Avoid making 0 citations look like a failure |
| Diagnostics | Blocked source/runtime timeout | Redacted diagnostic, partial evidence if any | Needs stricter policy/error fixtures |

## Current Recommendation

For the next design round:

1. Add chat-level orchestration mode selection before optimizing deeper retrieval.
2. Make bridge-managed source graph/read go through bridge, not direct browser-to-source URLs.
3. Add source group controls for bridge-managed sources.
4. Define a small multi-turn context contract.
5. Add runtime budgeting and fallback for global delegated synthesis.

The current system is functionally sound for direct source queries, bridge source discovery, fast global evidence, and local delegated answers. The main gaps are chat UX/control surfaces and multi-turn/runtime budgeting rather than the basic projection/query mechanics.
