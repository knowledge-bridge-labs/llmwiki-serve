# Tests: Default Serve I/O Logging

## Acceptance Criteria

- `REQ-001`: A default app writes a JSONL event file under `.runtime-logs/`.
- `REQ-002`: Opt-out suppresses file creation and event writes.
- `REQ-003`: Environment-configured paths receive events.
- `REQ-004`: Events include method, path, status, duration, and headers.
- `REQ-005`: `/query`, `/mcp`, and `/message:send` request bodies are captured.
- `REQ-006`: Query/MCP/A2A responses include captured response canaries.
- `REQ-007`: Body summaries include byte counts and truncation metadata.
- `REQ-008`: Authorization, API key, token, credential, and common secret values
  are redacted.
- `REQ-009`: The served local root path is redacted.
- `REQ-010`: Existing HTTP, MCP-style, and A2A-style requests still succeed.

## Unit / Integration Tests

- `tests/test_io_logging.py::test_default_io_log_captures_http_mcp_and_a2a_canaries`
- `tests/test_io_logging.py::test_io_log_off_suppresses_file_and_events`
- `tests/test_io_logging.py::test_env_io_log_path_and_auth_token_redaction`
- `tests/test_io_logging.py::test_cli_io_log_off_is_accepted_without_starting_uvicorn`

## Manual Checks

- Run a local server and inspect `.runtime-logs/llmwiki-serve-io.jsonl` after
  curl calls to `/query` and `/mcp`.
- Run with `--io-log off` and confirm no JSONL file appears.

## Skipped Or Deferred

- Remote log shipping is intentionally out of scope.
- Stable parser/versioning guarantees for JSONL consumers are deferred until a
  user workflow depends on them.
