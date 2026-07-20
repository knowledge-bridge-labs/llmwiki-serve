# ADR: Default Serve I/O Logging

## Status

Accepted.

## Context

`llmwiki-serve` is commonly run by a local operator who is connecting an agent,
IDE, bridge, or chat workbench to a local wiki source. When those clients send a
surprising query, malformed MCP-style JSON-RPC call, or A2A-style compatibility
message, the operator needs to see the actual request and response quickly.

The project also has a strong safety posture: network responses already avoid
local root leakage, and MCP-style errors use controlled messages. Default
logging changes the local persistence boundary because approved wiki content,
queries, and request metadata can be written to disk.

## Decision

Enable local serve I/O logging by default.

The default sink is `.runtime-logs/llmwiki-serve-io.jsonl`, relative to the
server process working directory. The `serve` CLI accepts `--io-log off` to
disable logging or `--io-log <path>` to choose a path. The same behavior is
available through `LLMWIKI_SERVE_IO_LOG=off` or `LLMWIKI_SERVE_IO_LOG=<path>`.

The log is a best-effort local debugging artifact, not telemetry. It is not
uploaded, shipped, or treated as a stable public API. If the log path cannot be
created or written, the serve request still succeeds.

Each event records method, path, query string, status, duration, redacted
headers, selected request bodies, and bounded response bodies. Request body
capture covers `/query`, `/mcp`, `/mcp/stream`, and `/message:send`. Response
capture is bounded and records parsed JSON when possible.

Redaction is applied recursively to headers, JSON bodies, text summaries, and
the final event before writing. Authorization, cookies, sessions, tokens,
secrets, passwords, credentials, API keys, common bearer/basic/OpenAI/GitHub
token shapes, raw URLs, URL query secrets, and private local path shapes are
redacted.

## Consequences

- Local debugging is easier because the first failed client call leaves a
  nearby JSONL artifact.
- Operators must understand that approved wiki content and user queries are
  intentionally persisted unless logging is disabled.
- Log redaction reduces common credential and local-root exposure risk, but the
  JSONL file remains local sensitive output and should not be committed or
  pasted blindly into public issues.
- The HTTP/MCP/A2A-style response contract remains unchanged.

## Follow-Ups

- Add richer diagnostics if a future support workflow needs named trace IDs or
  request IDs returned to clients.
- Consider a size/retention policy if operators keep long-running servers open.
- Revisit JSONL schema stability if external tooling starts consuming these
  logs.
