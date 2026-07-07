# Security Policy

## Supported Versions

`llmwiki-serve` is in early development. Security fixes are applied to the
main development line unless a supported release branch is documented.

## Reporting a Vulnerability

Please report suspected vulnerabilities through GitHub Security Advisories for
this repository when available. The organization private reporting route is:

<https://github.com/knowledge-bridge-labs/llmwiki-serve/security/advisories/new>

If private reporting is unavailable, open a public issue only to request a
private security contact path, then stop. Do not include exploitable details,
private data, credentials, private wiki content, request logs, screenshots, or
proof-of-concept payloads in a public issue, pull request, or discussion.

Maintainers aim to acknowledge private reports within 7 calendar days, assess
impact, and publish fixes or mitigations through normal release notes. Public
credit is opt-in and should be requested by the reporter during disclosure
coordination.

## Security Model

`llmwiki-serve` is intended to expose read-only projections of local Markdown
folders. Operators are responsible for choosing the wiki root, reviewing content
before serving it, and running the service behind appropriate authentication,
authorization, TLS, and network controls for their environment.

The default HTTP CORS policy is not wildcard-based. It allows local development
origins on `localhost`, `127.0.0.1`, and IPv6 localhost `[::1]` only. Operators
can configure explicit origins with `--cors-origin` or `create_app(...,
cors_origins=[...])`; when explicit origins are configured, they replace the
default local allowlist. Requests that include a browser `Origin` header are
rejected when the origin is outside that allowlist, including MCP Streamable HTTP
requests. CORS and Origin checks are still only browser-boundary protections and
do not replace authentication or network controls.

Draft and unpublished pages are withheld from default read, search, context, and
graph responses. Network HTTP endpoints and MCP tools ignore
`include_drafts=true` unless the operator explicitly enables draft serving with
`create_app(..., allow_drafts=True)` or `llmwiki-serve serve --allow-drafts`.
A2A-style compatibility endpoints are disabled by default and must be enabled
with `create_app(..., enable_a2a_compat=True)` or
`llmwiki-serve serve --enable-a2a-compat`; when enabled, `message:send` always
builds approved-only context. Treat draft serving as an operator/debug
affordance because it can expose content marked with `draft: true`,
`published: false`, `publish: false`, or draft review states.

HTTP `/manifest` and agent-facing manifest surfaces do not expose the local wiki
root path. The CLI `llmwiki-serve manifest` command is local operator output and
does include the root path, so review it before sharing logs or screenshots.

The project does not treat source Markdown as inherently public or safe. Logs,
tracebacks, issue reports, screenshots, graph output, and response samples should
be reviewed and redacted before sharing.
