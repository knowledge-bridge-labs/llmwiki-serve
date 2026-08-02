# Changelog

All notable changes to LLMWiki Serve will be documented here.

This project follows a lightweight public-preview changelog format. Dates use
`YYYY-MM-DD`.

## Unreleased

- Documented agent-guided lexical retrieval as the recommended direct-agent
  workflow and reconciled the agent-guided spec, tasks, tests, ADR, and release
  guidance with the implemented V1 guidance/query-variant behavior. Benchmark
  notes remain engineering evidence only and do not add public quality or
  performance claims.

## 0.2.9 - 2026-08-01

- Added optional source-owned semantic retrieval preview as `mode=vector` and
  `mode=hybrid` across Python service calls, HTTP `/query` and `/search`, MCP
  JSON-RPC, MCP Streamable HTTP, and CLI `query/search --mode`, without adding
  new endpoints, tools, commands, or result fields. Lexical remains the default
  retrieval mode.
- Added `llmwiki-serve[vector]` with local FastEmbed `0.8.x` and direct NumPy
  `2.4.x` dependency bounds. Vector dependencies, provider construction, model
  access, index builds, and vector sidecar writes remain absent unless an
  operator explicitly enables vector retrieval.
- Added local FastEmbed provider configuration with explicit model name,
  local-files-only default model access, operator-only download opt-in, and
  redacted provider/model metadata. The explicit candidate model is
  `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` via FastEmbed's
  `qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q` source revision
  `faf4aa4225822f3bc6376869cb1164e8e3feedd0`, dimension `384`, license
  `apache-2.0`.
- Added deterministic heading/paragraph vector chunking, exact cosine page
  ranking, fixed-`k=60` lexical+dense hybrid reciprocal rank fusion, bounded
  optional read-only orientation hints from source-owned `hot.md`, `index.md`,
  and `overview.md`, English exact-identifier guards before hybrid fusion,
  vector/hybrid `min_score` rejection, and managed-context hit recording for
  semantic routes. Retrieval does not rewrite orientation files and is not a
  GraphRAG or universal quality-improvement claim.
- Added an external vector sidecar cache with redacted salted identity,
  approved/draft visibility isolation, checksum-named float32 `.npy` vector
  sidecars, compact JSON chunk metadata, manifest-last atomic publish,
  sidecar-local locking, corruption-as-miss rebuild behavior, and no raw source
  text, snippets, raw queries, local roots, model local paths, or secrets in
  cache records.
- Added exact retrieval capability strings in health, manifest, source-bundle,
  and MCP metadata. Vector and hybrid capabilities are advertised only when a
  configured provider is usable; disabled semantic requests now fail
  actionably instead of falling back to lexical.
- Added full SciFact vector/hybrid benchmark runner support and regression tests
  for lexical, vector, plain-RRF, and production hybrid evaluation paths. These
  scripts remain offline benchmark tooling under `scripts/benchmark_adapters/`,
  not installed `llmwiki-serve` commands.
- Added NoMIRACL-ko judged-pool acquisition, materialization, vector/hybrid
  runner support, and tests for Korean retrieval diagnostics. The judged-pool
  protocol is local benchmark evidence only and is not a full MIRACL-ko corpus
  claim.
- Added a curated LLMWiki orientation mechanism benchmark fixture, runner, and
  fake-provider tests for orientation-related hybrid behavior, boilerplate
  resistance, exact identifier preservation, exact no-orientation fallback, and
  approved-only draft isolation. The report is labeled non-authoritative and is
  not an external retrieval-quality, language-quality, poisoning-safety, or
  abstention benchmark.
- Documented that current vector/hybrid Windows and DGX Spark Ubuntu
  small/team dirty-snapshot runs are engineering evidence only. No public
  vector/hybrid quality or performance claim is release evidence until the
  report is committed under `benchmarks/verified_sources/reports/`, tied to a
  clean release revision, validated by the public report gates, and free of
  private paths or secrets. Larger corpus claims remain experimental until
  10k/50k/100k/500k gates are accepted.

## 0.2.8 - 2026-08-01

- Recorded the final analyzer release decision: legacy lexical ranking remains
  the product default because OpenWiki generic-shadow class gates regress under
  English. The English-aware Snowball analyzer is public explicit opt-in through
  `--analyzer-profile legacy|english` on `serve`, `query`, and `search`, and
  through Python `create_app` and `LlmWikiService`. Release runtime and public
  Python surfaces support exactly `legacy|english`; HTTP/MCP request schemas
  stay unchanged.
- Recorded evaluated experimental candidates `english_additive` and
  `english_flatlike` as decision evidence only, not shipped or supported
  runtime profiles, and deferred hybrid/fusion ranking to a separate future
  spec.
- For the English opt-in profile, excluded `source_refs` from broad stemmed
  BM25 content. Exact authored compound lookup and exact path/source-reference
  token matching remain available when the query contains the same original
  token.
- Hardened public SciFact report generation so CLI and programmatic paths
  require explicit `analyzer_profile` and `implementation_revision` metadata.
  Public validation rejects non-public profiles and all-zero placeholder
  revisions. Final sanitized Windows and DGX Spark Ubuntu SciFact reports now
  validate from immutable revision
  `git:8d04e8a46487827ee488a7ddab005aaab8dd885d` with identical primary
  quality metrics. On PR #35, Linux and Windows Python 3.11/3.12 CI jobs and
  both CodeQL checks pass at that head. Merge, PyPI publish, and hosted docs
  deployment remain pending.
- Added an in-memory postings index for lexical search to reduce warm p95
  latency while preserving legacy exact-result behavior for the legacy analyzer.
- Added the official BEIR SciFact benchmark adapter, safe acquisition,
  materialization, and runner path for the full 5,183-document corpus,
  300-query test split, and 339 qrels, with source checksums, license checks,
  public-safety report gates, implementation-revision capture, and contextual
  same-data reference rows only for BEIR BM25 and Anserini flat BM25.
- Added reproduction CLI support for downloading, validating, materializing, and
  running the SciFact benchmark workflow without committing raw benchmark data.

## 0.2.7 - 2026-07-30

- Added opt-in managed context for generic Markdown folders, using an external
  local sidecar for opaque page-hit priors and projection-derived orientation
  while keeping authored source files and `graph/graph.json` untouched.
- Added verified-source benchmark harnesses, fixtures, public-safe benchmark
  cases, report validation, and collection support for retrieval-quality
  evidence with Qwen tokenizer provenance and source-mutation guards.
- Expanded upstream candidate smoke support with 12 actual-pinned public cases,
  public report generation, license evidence fields, setup validation, and
  private-path/credential report scanning.
- Added proposed strict-answerability spec and ADR material without enabling new
  runtime answer-synthesis behavior.
- Documented the managed-context boundary and central upstream compatibility
  evidence links in README, architecture, and release guidance.

## 0.2.6 - 2026-07-29

- Hardened `llmwiki-serve ls/status` process discovery with `psutil`-backed
  argv/cwd and TCP listener ownership. Registry-empty discovery still avoids
  fixed-port scans, dedupes launcher/wrapper chains by endpoint, reports the
  actual listener PID when available, resolves relative process roots against
  cwd, keeps exact but unreachable process candidates visible as unhealthy
  `service_verified=false`, records optional process create time for PID reuse
  checks, and adds `--probe-timeout-seconds` for local health probe tuning.
- Restricted process-derived health probes to listener-confirmed local
  endpoints, including IPv6 wildcard loopback handling and bounded probing for
  many unreachable candidates.

## 0.2.5 - 2026-07-28

- Improved `llmwiki-serve ls/status` so default local discovery uses the current
  registry first, then verifies unregistered legacy/orphan `llmwiki-serve serve`
  processes discovered from OS command lines on their parsed host and port.
  JSON now reports `registered`, `orphan`, `version`, `discovery_source`, and
  `root_source`, while default human output avoids printing full root paths.

## 0.2.4 - 2026-07-28

- Added literal search mode for exact substring retrieval across service, HTTP,
  MCP, and CLI search/query flows.
- Added search/query payload controls for `fields`, `snippet_chars`,
  `min_score`, and `exclude_page_ids`, plus read `fields` projection for
  body-free metadata reads.

## 0.2.3 - 2026-07-28

- Added `llmwiki-serve ls` and `llmwiki-serve status` for local operator
  discovery of running serve instances, including health, stale-record,
  duplicate, and parent/subfolder annotations.
- Improved Korean and numeric search relevance with better token matching,
  BM25-style length normalization, smaller role boosts, numeric pseudo-tag
  filtering, and shorter default snippets.

## 0.2.2 - 2026-07-23

- Scoped MCP server names, instructions, and tool descriptions to the served
  wiki manifest so multi-wiki MCP clients can distinguish sources.
- Added MCP metadata override knobs for Python callers, CLI operators, and
  environment-based deployments without changing tool names or response
  contracts.
- Lowered omitted full-graph defaults from 500 to 100 nodes while preserving
  explicit graph requests up to the existing 2,000-node maximum.
- Added configurable graph/context omitted-limit defaults and advertised those
  defaults in MCP tool metadata.

## 0.2.1 - 2026-07-22

- Added an optional Redis/Valkey projection store extra and CLI/env
  configuration so production deployments can reuse derived projections across
  server processes while keeping in-memory projection as the default.
- Added projection-store diagnostics fields for backend status, including
  `backend_kind` and a sanitized Redis `endpoint`, so operators can verify
  whether the active backend is memory or Redis without exposing credentials or
  endpoint query details.
- Documented that Redis stores sensitive derived projection data, including
  drafts, and that automatic Redis TTL/cleanup is not part of this release.

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
