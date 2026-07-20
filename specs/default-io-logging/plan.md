# Plan: Default Serve I/O Logging

## Approach

Add a pure ASGI middleware around the FastAPI application so request and
response body capture does not consume the request stream before route parsing.
The middleware writes one redacted JSONL event after each HTTP response finishes.

The default sink path is `.runtime-logs/llmwiki-serve-io.jsonl`, resolved
relative to the process working directory. Operators can pass `--io-log off`,
`--io-log <path>`, or set `LLMWIKI_SERVE_IO_LOG` to `off` or a path.

## Affected Areas

- Source modules: `src/llmwiki_serve/io_logging.py`,
  `src/llmwiki_serve/api.py`, `src/llmwiki_serve/cli.py`,
  `src/llmwiki_serve/service.py`
- Tests: dedicated I/O logging tests and source-signature runtime-output ignore
  coverage
- Docs: README, architecture, changelog
- Contracts: no OpenAPI response change

## Risks

- Risk: default logs contain approved wiki content that operators did not expect
  to persist.
  Mitigation: document the local log path, opt-out switch, and data-safety
  posture.

- Risk: credentials appear in request headers or bodies.
  Mitigation: recursively redact sensitive header/body keys and common bearer,
  OpenAI, GitHub, and key-assignment token shapes.

- Risk: request-body capture interferes with FastAPI parsing.
  Mitigation: use ASGI receive/send wrappers instead of `Request.body()` inside
  BaseHTTPMiddleware.

- Risk: logging path errors break serving.
  Mitigation: treat the sink as best-effort and preserve successful responses.

## Rollout

- Add focused pytest coverage for default logging, opt-out, configured path, and
  redaction.
- Run focused tests plus format/lint/type checks.
- Ingest this spec, ADR, README, architecture, and changelog into project
  LLMWiki after review if projection validation is requested.
