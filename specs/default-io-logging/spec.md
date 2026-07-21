# Spec: Default Serve I/O Logging

## Status

Implemented.

## Problem

Local operators currently have to reproduce client requests manually when HTTP,
MCP-style JSON-RPC, MCP Streamable HTTP, or A2A-style compatibility calls do not
return the context they expected. The served projection is local-first, so a
small local request/response event log is the fastest debugging artifact, but it
must not leak bearer tokens, credentials, private headers, or the local source
root.

## Goals

- Enable serve request/response I/O logging by default for local debugging.
- Write newline-delimited JSON events to an easy-to-find local path.
- Allow operators to disable logging or choose a different path with CLI or
  environment configuration.
- Capture enough data to debug request routing, request bodies, response status,
  duration, and bounded response payloads.
- Preserve redaction for Authorization, cookies, API keys, tokens,
  credentials, secret-looking body fields, raw URLs, URL query secrets, and
  private local path shapes.

## Non-Goals

- Do not add hosted log shipping, telemetry, analytics, or remote upload.
- Do not add authentication or access-control policy in this slice.
- Do not make JSONL logs a stable public API contract for third-party parsers.
- Do not log CLI `manifest`, `query`, `source-refs`, or `source-bundle` output.

## Requirements

- `REQ-001`: A default `serve` app writes JSONL events to
  `.runtime-logs/llmwiki-serve-io.jsonl`.
- `REQ-002`: `LLMWIKI_SERVE_IO_LOG=off` or `--io-log off` disables event writes.
- `REQ-003`: `LLMWIKI_SERVE_IO_LOG=<path>` or `--io-log <path>` writes to the
  configured local path.
- `REQ-004`: Every HTTP request event includes method, path, query string,
  status, duration, and redacted headers.
- `REQ-005`: Request bodies are captured for `/query`, `/mcp`, `/mcp/stream`,
  and `/message:send`.
- `REQ-006`: Response bodies are captured as parsed JSON when possible or as a
  bounded text summary.
- `REQ-007`: Captured bodies are bounded so unusually large payloads are
  summarized instead of written unbounded.
- `REQ-008`: Authorization, cookie, token, API-key, credential, password, and
  secret fields are redacted in headers and JSON bodies.
- `REQ-009`: Raw URLs, URL query secrets, Windows user paths, UNC paths, POSIX
  private paths, and the served local root path are redacted in logged strings.
- `REQ-010`: Logging failures must not make the read-only serve request fail.

## Compatibility

- CLI: additive `--io-log` serve option.
- Environment: additive `LLMWIKI_SERVE_IO_LOG` configuration.
- HTTP/MCP/A2A-style responses: no response shape change.
- OpenAPI: unchanged because logging is middleware and CLI configuration only.

## Data Safety

The log is local process output and is ignored by the repository via
`.runtime-logs/`. Operators should still treat the file as potentially sensitive
because approved wiki content and user queries are intentionally captured for
debugging. Redaction is a safety boundary for common credentials and local root
paths, not a guarantee that arbitrary private wiki content is removed.

## References

- ADR: `docs/decisions/2026-07-20-default-serve-io-logging.md`
