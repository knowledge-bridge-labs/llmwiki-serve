# Changelog

All notable changes to LLMWiki Serve will be documented here.

This project follows a lightweight public-preview changelog format. Dates use
`YYYY-MM-DD`.

## 0.2.0 - 2026-07-17

- Added default-on local serve I/O JSONL logging for HTTP, MCP-style,
  MCP Streamable HTTP, and opt-in A2A-style request/response debugging, with
  `--io-log off` / `LLMWIKI_SERVE_IO_LOG=off` opt-out and credential/header/root
  redaction.
- Added bounded graph-neighborhood lookup through `GET /graph/neighborhood` and
  MCP `llmwiki_graph_neighbors` for CKG-like graph-guided agent inspection.
- Added an opt-in `--producer-manifest` freshness marker contract for generated
  wiki operators that can update a manifest after every ingest/compile run;
  source changes remain stale until that marker changes.
- Added CODEOWNERS for the planned Knowledge Bridge Labs maintainer team and
  hardened the automated PR review guide's changed-file rendering.
- Added a usage-question issue form so public support routing works while blank
  issues remain disabled.
- Polished the README first screen with badges, public-preview status,
  cross-repo toolchain positioning, and a clearer what/what-not/how-it-works
  overview.
- Linked the README release status to the cross-repo status and compatibility
  matrix in the docs portal.
- Updated maintainer and vulnerability-reporting wording so public governance
  routes point at Knowledge Bridge Labs without temporary transfer language.
- Hardened live serving refresh so cached projections detect source-file and
  graph-sidecar rewrites, additions, and nested output changes even when writers
  preserve path, inode, size, and mtime metadata.
- Added runtime refresh coverage for compile output creation/replacement,
  Obsidian raw-ingest notes, nested wiki notes inside Obsidian vaults, status
  visibility flips, and stat-preserving Markdown/sidecar rewrites.
- Fixed query evidence ranking so hot/index/overview role boosts cannot make an
  otherwise unmatched or draft-only query answerable.
- Aligned CI dependency setup with contributor guidance by using the locked
  `uv sync --extra dev` workflow, normalized source-distribution smoke text
  reads on Windows, and documented the wheel-install smoke fallback for local
  uv cache misses.
- Added contributor-facing PR review guidance through GitHub annotations, a
  generated changed-path review guide, visible reviewer-focus prompts, and
  documented maintainer review expectations.
- Added the official MCP Python SDK FastMCP Streamable HTTP endpoint at
  `/mcp/stream`, kept `/mcp` JSON-RPC compatibility, and made A2A-style
  compatibility endpoints opt-in.

## 0.1.0 - 2026-07-01

- Initial public preview of the Python server for serving LLMWiki-style Markdown
  folders over HTTP, MCP-style JSON-RPC, A2A-style message endpoints, and CLI
  commands.
- Rejected marker/config-only Foam, Dendron, Quartz, and Logseq roots as
  unsupported when they contain no servable wiki pages, and returned redacted
  JSON root errors from regular HTTP routes.
- Added explicit source distribution content smoke coverage and documented the
  intended minimal OSS-friendly sdist include/exclude policy.
- Added wiki metadata to context packs and clarified MCP-style/A2A-style
  context responses so agents receive hot/index/overview orientation before
  query-ranked evidence.
- Added an executable generated candidate sample suite and generated-artifact smoke
  coverage for manifest, context, search, read, graph, MCP-style, A2A-style,
  graph closure, and refresh behavior.
- Hardened default graph responses so shared non-page nodes do not carry
  draft-derived page paths into approved-only graph or context payloads.
- Treated `.vscode` Markdown and Org files as workspace metadata rather than
  served knowledge while preserving Foam extension marker detection.
- Improved CLI root, limit, port, and unsupported-folder failure messages so
  operator-facing errors stay short and non-traceback-based.
- Added CI timeout/concurrency controls, removed duplicate release smoke runs,
  documented the Python API boundary, and expanded release smoke wheel content
  checks.
- Clarified CORS behavior so explicit `--cors-origin` values replace the
  default local-development allowlist, and added regression coverage for that
  policy.
- Documented fresh-clone Quick Start prerequisites and added the release smoke
  script to CI before package builds.
- Added a public release smoke script and clarified fixture-vs-real-wiki
  validation boundaries, refresh behavior, and package metadata.
- Added public PR operating guidance for substantial changes, prior discussion,
  low-effort or unverified generated contributions, and safer security fallback
  routing.
- Clarified compatible Markdown output target wording and added Logseq `.org`
  fixture coverage.
- Added pull request and issue templates for CLI, HTTP, MCP, A2A, adapter, and
  source-folder compatibility work.
- Added CodeQL, dependency review, and Dependabot configuration for public
  collaboration readiness.
- Added support routing, CODEOWNERS preparation, and a release checklist for
  public collaboration readiness.
- Added a project code of conduct and README links to contribution, security,
  conduct, architecture, and release-note documents.
