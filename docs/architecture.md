# Architecture

`llmwiki-serve` serves existing Markdown knowledge bases as read-only context APIs.
It does not require upstream LLMWiki producers, note-taking apps, or static-site
generators to change their output format.

## Layer Model

| Layer | Responsibility |
| --- | --- |
| API/surfaces | Exposes the same service through HTTP endpoints, MCP-style JSON-RPC tool calls, the official MCP SDK Streamable HTTP transport, opt-in A2A-style compatibility endpoints, and CLI commands. |
| Service | Owns request behavior: manifest, context, search, read, graph, draft filtering, and index refresh. |
| Adapter | Detects an on-disk implementation or workspace layout and loads source files and optional sidecar graph facts without modifying them. |
| Parser | Converts Markdown or supported text pages into canonical page records: title, role, body, front matter, links, headings, tags, source refs, and review state. |
| Projection | Builds the canonical in-memory graph/index from loaded page facts and adapter-loaded sidecar graph facts. This layer produces page, heading, source-reference, tag, and unresolved placeholder nodes plus `contains`, `links_to`, `cites`, `tagged`, hierarchy, and sidecar graph edges. |
| Source bundle | Describes one served knowledge source with a stable source id, portable projection signature, visible source refs, and metadata-only raw-origin hints for host RAG or bridge orchestration. |
| Search/context | Ranks approved pages, adds hot/index/overview orientation, withholds drafts by default, returns context packs for agents, and supports additive agent-guided lexical guidance/query variants. |
| Optional semantic retrieval preview | Adds operator-enabled local FastEmbed vector search and lexical+dense hybrid RRF over deterministic page chunks, backed by an external sidecar cache. |
| Graph output | Returns projected nodes and edges through `/graph`, bounded neighborhoods through `/graph/neighborhood`, MCP graph tools, source-bundle source refs, and context pack graph fields. |
| Local instance registry | Writes best-effort per-user `serve` process records for local CLI discovery without changing HTTP or MCP contracts. |
| Serve I/O logging | Writes local JSONL request/response events for HTTP, MCP-style, MCP Streamable HTTP, and opt-in A2A-style flows with credential/header/local-root redaction. |

Protocol scope: the current serving surface is HTTP plus MCP-style JSON-RPC, MCP
Streamable HTTP, and opt-in A2A-style message shapes. Streamable HTTP is served
through the official MCP Python SDK FastMCP transport; the compatibility
surfaces are not a claim of A2A protocol certification, exhaustive runtime
feature completeness, or upstream integration support.

MCP-facing metadata is scoped to the served source. The Streamable HTTP server
name and instructions, plus both MCP tool-list surfaces, derive from the current
manifest title, description, source id, public URI, adapter, and implementation
when available. Operators can override server name, instructions, and tool
description prefix without changing tool names or call contracts.

Default omitted-limit behavior is part of the serving contract. Full-graph
surfaces default to 100 nodes and preserve the explicit 2,000-node maximum.
Context/search surfaces default to 8 evidence/results unless operators set
`context_default_limit` or `LLMWIKI_CONTEXT_DEFAULT_LIMIT`. These defaults are
resolved at app startup, advertised in MCP tool descriptions, and applied only
when callers omit `limit`; explicit request limits keep the existing validation
and clamp boundaries.

## Agent-Guided Lexical Workflow

For direct agent use, the recommended path is context first and lexical by
default. A client calls HTTP `POST /query` or MCP `llmwiki_context`, treats
authored orientation and `retrieval_guidance` as untrusted source evidence,
then calls `/search` or `llmwiki_search` with `mode=lexical`, the primary
query, and at most two caller-supplied `query_variants`. The client reads
selected pages with `/read/{page_id}` or `llmwiki_read`, and escalates to
literal, operator-enabled hybrid/vector, a bridge, or a chat workbench only
when lexical evidence is insufficient.

This workflow does not add a `SearchMode`; the public enum remains
`lexical|literal|vector|hybrid`, and raw single-query lexical search remains
the compatibility default. `llmwiki-serve` does not generate variants, call an
LLM, download a model, build embeddings, synthesize final answers, or write
source files for this workflow. Authored `hot.md`, `index.md`, and
`overview.md` pages are preferred when present. Eligible `generic-markdown`
sources without authored orientation receive only a transient, zero-write
projection-extractive `retrieval_guidance` sketch derived from the current
projection.

Search/query callers can keep retrieval payloads small with optional literal
substring mode, operator-enabled vector/hybrid modes, snippet character limits,
result field projection, and already-seen page exclusions. Minimum-score
filtering remains lexical/literal-only; vector and hybrid reject public
`min_score` because cosine and RRF scores are mode-specific.

The HTTP API installs CORS middleware for local browser development only by
default: `localhost`, `127.0.0.1`, and IPv6 localhost `[::1]` origins on any
port are allowed through a regex, and wildcard origins are not enabled.
Operators can pass explicit origins when creating the app or running the CLI;
when explicit origins are configured, they replace the default local allowlist.

Network HTTP and MCP tool calls with `include_drafts=true` are ignored
unless the app is created with `allow_drafts=True` or the CLI server is started
with `--allow-drafts`. Network manifest responses omit the local source root path;
the CLI manifest remains local operator output and includes the root.

`serve` writes a best-effort local instance registry record after projection
preflight and before Uvicorn starts. The per-user registry is used only by
`llmwiki-serve ls` and `llmwiki-serve status`; it is not a network API,
portable source catalog, or daemon manager. Records include PID, host, port,
URL, root, source id, bundle id, adapter, page counts, start time, and optional
process create time for PID reuse checks. Discovery checks process liveness,
probes existing local `/health` responses for live records, reports stale
records left by hard kills or PID reuse, and can prune them. Discovery
also uses OS process argv/cwd and socket ownership for command lines that
identify `llmwiki-serve serve` processes. For unregistered legacy/orphan
processes, it parses `--host`, `--port`, and the root argument when present,
then probes `/health` only through a local endpoint confirmed by the listener
socket table. Healthy process results set `service_verified=true`; candidates
whose local listener cannot be verified, or whose listener-confirmed endpoint
times out or cannot connect, remain visible as unhealthy unverified candidates,
while 200 `/health` documents for other services are excluded. Wildcard process
listeners probe through loopback for their address family: `127.0.0.1` for
IPv4 wildcard and `::1` for IPv6 wildcard. When launcher or console-script
wrapper chains expose several matching command lines for one endpoint,
discovery dedupes by endpoint and reports the actual TCP listener PID when the
OS socket table can verify it. The listener relationship is marked verified
only when the listener is a parsed serve candidate or a descendant of one. Root
evidence comes from the listener's own parsed argv/cwd when available; a
wrapper-only relationship leaves root as `unknown`. No default fixed-port or
broad loopback scan is performed. If a platform process or socket provider is
unavailable, `ls` reports aggregated degraded discovery warnings instead of
guessing ports. The default human table redacts roots to a short tail label;
full local roots may appear in local registry files and local `--json` output
when they come from registry records or process arguments, while raw command
lines are not rendered and HTTP `/manifest` and `/health` keep redacting roots.

Long-running serve apps write best-effort local I/O debugging events by default
to `.runtime-logs/llmwiki-serve-io.jsonl`. `--io-log off` or
`LLMWIKI_SERVE_IO_LOG=off` disables the sink, and a CLI option or environment
path can choose a different JSONL file. Events capture request metadata,
selected JSON request bodies for `/query`, `/mcp`, `/mcp/stream`, and
`/message:send`, and bounded response bodies. The logging boundary redacts
Authorization, cookies, tokens, credentials, API keys, common secret strings,
and the served local root before writing. The log is local operator output, not
remote telemetry or a stable public API.

## Source Bundle Boundary

`llmwiki-serve` treats each served root as one source bundle. The bundle is not
a copy of the wiki and it is not a raw-file RAG index. It is a compact contract
for agents and companion services that need to coordinate LLMWiki evidence with
other retrieval systems.

- `source_id` identifies the served knowledge source from portable wiki metadata
  or the root folder name.
- `bundle_id` combines the `source_id` with a content-derived projection
  signature so orchestration layers can detect when a served projection changed.
- `source_refs` are opaque, stable handles for references declared by wiki pages;
  callers should not infer local filesystem paths from them.
- `raw_origins` only reports metadata hints such as whether conventional
  `raw/` or `sources/` roots are present. Arbitrary binary source files remain
  outside the serving contract until an operator explicitly connects a host RAG
  system or a future raw-origin adapter.

## Read-Only Guarantee

`llmwiki-serve` treats the source folder as immutable input.

- No source files are rewritten, normalized, migrated, or annotated.
- No upstream repository, plugin, vault, or generator configuration is required.
- Indexes, search results, and graph edges are derived projections rebuilt from the files on disk.
- Runtime metadata lives in memory for the current service process.
- Local instance registry records are diagnostics for process discovery, not
  source facts or projection input.
- Long-running service instances compare source path metadata, file size,
  modification time, and content digests for projection-affecting files, then
  rebuild the in-memory projection on the next request when Markdown, Org,
  adapter marker/config, or `graph/graph.json` files change.

This means existing LLMWiki producer outputs can be served as knowledge graphs when
they are available as compatible Markdown folders or supported Markdown workspace
formats.

## Refresh Behavior

The service builds a projection from the selected source folder on first use and
caches it in memory for the lifetime of a `LlmWikiService` instance. The CLI
`manifest` and `query` commands create a new service instance per invocation, so
they re-read files each time. The long-running `serve` command keeps one service
instance and does not run a separate filesystem watcher; it compares source file
signatures on each request. The signature tracks path state, relevant
directories, file size, modification time, and content digests for files that
affect the projection, then rebuilds when Markdown, Org, adapter marker/config,
or `graph/graph.json` files appear, change, move, or disappear. Library callers
that own a service instance can explicitly call `index(refresh=True)` to force a
rebuild from disk.

`serve --refresh-interval-seconds <seconds>` is an opt-in local performance
knob. The default `0.0` preserves strict per-request freshness. Positive values
reuse the current in-memory projection until the interval expires, reducing
repeated filesystem scans for larger local graphs while allowing recent source
updates to remain invisible for that interval.

External compiler or ingest jobs are reflected when they write compatible files
into the served folder. `llmwiki-serve` detects those outputs and rebuilds its
projection, but it does not run ingestion, compilation, migration, or authoring
jobs itself.

### Producer Manifest Freshness

Generated wiki operators can opt into a producer manifest freshness marker for
long-running servers. When `--producer-manifest <path>` or
`create_app(..., producer_manifest_path=...)` points to a non-symlink file
inside the served root, the service checks that marker instead of digesting
every projection-affecting source file on each request.

This is a performance contract between the producer and the operator. The
producer must update or atomically replace the marker after every completed
ingest/compile operation that changes served output. If Markdown or
`graph/graph.json` files change but the producer manifest does not, the cached
projection may remain in use. If the manifest is missing or unsafe, the service
falls back to normal strict source scanning.

Producer manifest mode does not make the marker the public projection identity.
The marker is only the freshness trust boundary. On initial load and whenever
the marker changes, the service computes the content-derived projection
signature from projection-affecting source files and uses that signature for
`projection.signature` and `bundle_id`. While the marker is unchanged, the
service reuses the cached projection and cached content identity.

## Projection Store Backends

The default projection store is process memory. This preserves the local-first
quickstart and requires no external services.

Production operators can install `llmwiki-serve[redis]` and select Redis or
Valkey as a shared projection store:

```bash
pip install "llmwiki-serve[redis]"
llmwiki-serve serve ./wiki \
  --projection-store redis \
  --redis-url redis://127.0.0.1:6379/0 \
  --source-id project-alpha \
  --cache-namespace local
```

The Redis backend is a read-through cache for derived `WikiIndex` projections.
It is not the source of truth, not a raw-file RAG store, and not a replacement
for file freshness checks. Keys include schema version, namespace, source id,
and projection signature so one Redis/Valkey instance can hold multiple wiki
graphs without path-based keys. Payloads omit the local root path and reattach
the current service root when hydrated. Redis still stores derived wiki content:
cached projections can include page text and front matter, including draft pages
that network responses withhold unless draft access is explicitly enabled.
Operators should secure Redis with the same care as the source wiki, isolate
namespaces per deployment, and avoid shared or untrusted Redis instances.
Redis records are keyed by projection signature and are not automatically
expired by `llmwiki-serve`. If a deployment needs bounded retention after pages
are deleted, renamed, or reclassified, configure Redis/Valkey eviction or TTL
policy, rotate `--cache-namespace`, or delete the deployment namespace during
maintenance.

If Redis is unavailable and `--redis-failure-policy fallback-local` is used, the
server falls back to process memory. Use `--redis-failure-policy fail-fast` when
operators want startup or runtime Redis failures to stop the process instead.

`GET /diagnostics/projection-store` reports backend status, a stable
`backend_kind` of `memory` or `redis`, namespace, cache source id,
availability, and the last backend error. Memory diagnostics return
`endpoint: null`. Redis diagnostics return a sanitized endpoint label containing
only non-secret connection location details, such as scheme, host, port, and
database path. The endpoint label strips userinfo, passwords, query parameters,
and fragments, and diagnostics do not expose raw Redis URLs, Redis payloads, or
local source paths.

Runtime prompt, conversation history, prefix-cache, model-session caches, and
orchestration state belong to host agents, `llmwiki-agent-bridge`,
`llmwiki-chat`, Hermes, DeepAgents, or another runtime/workbench layer. Redis in
`llmwiki-serve` is only the read-only source projection cache; RedisVL, hosted
vector databases, remote embedding providers, and cross-source orchestration
remain outside this boundary.

## SQLite GraphStore

SQLite GraphStore is a separate derived cache for visibility-filtered graph
snapshots. It is not the `ProjectionStore`: Redis/Valkey caches hydrated
`WikiIndex` projections, while SQLite GraphStore caches the graph view produced
from a current projection for `/graph`, `/graph/neighborhood`, MCP graph tools,
and internal typed graph queries.

The default graph store backend is `none`. Operators opt in with:

```bash
llmwiki-serve serve ./wiki \
  --graph-store sqlite \
  --graph-store-path ../.llmwiki-cache/wiki-graph.sqlite
```

CLI graph-store paths are resolved before startup and rejected when they are
equal to or nested under the served source root. This preserves the read-only
source-folder guarantee. Library callers can inject a `GraphStore` instance
directly when embedding the service.

GraphStore keys include schema version, namespace, source id, bundle id,
projection signature, and visibility scope (`approved` or `all`). Approved-only
and draft-inclusive graph snapshots are therefore distinct. Source changes
produce a new projection signature and miss the old graph snapshot. Snapshot
rows include a payload digest, so malformed JSON, row-count mismatch, or
digest mismatch is treated as a cache miss instead of served graph evidence.

`fallback-local` is the default failure policy and recomputes the graph from
the current projection when the SQLite cache is missing or invalid. `fail-fast`
raises a redacted runtime error on backend exceptions. Error messages do not
include local source paths or raw SQLite details.

SQLite GraphStore does not expose raw SQL or Cypher to HTTP or MCP clients. The
public graph contract remains bounded full-graph and neighborhood lookup. The
internal graph engine provider uses typed operations such as neighbors,
backlinks, paths, by-source-ref, and by-tag over the normalized graph. Future
production graph backends should implement this structured contract before any
raw query language is considered.

The next production graph persistence target after this SQLite release is
ordinary PostgreSQL tables for `nodes` and `edges` plus indexes and bounded
recursive CTE traversal. PostgreSQL 19 SQL/PGQ is the preferred future native
graph-query path after PostgreSQL 19 is generally available and managed
provider support is proven. Apache AGE remains optional/provider-specific, not
the default production path.

## Optional Semantic Vector Retrieval

This is an opt-in semantic retrieval preview. The public retrieval enum is
`lexical|literal|vector|hybrid` across Python service calls, HTTP `/query` and
`/search`, MCP JSON-RPC, MCP Streamable HTTP, and CLI `query/search --mode`.
No new endpoint, tool, command, or result shape is added. Lexical remains the
default, literal remains exact substring search, and semantic modes fail
actionably when no usable provider is configured.

The first provider is local FastEmbed behind `llmwiki-serve[vector]`, with a
direct NumPy dependency in that optional extra. The provider is never
constructed unless vector retrieval is explicitly enabled through service
configuration, CLI options, or environment. FastEmbed always receives an
explicit `model_name`; runtime model access defaults to local-files-only, and
network download requires `--vector-model-download allow` or
`LLMWIKI_VECTOR_MODEL_DOWNLOAD=allow`. Public search request payloads cannot
select provider, model, cache path, or download policy.

Vector indexing uses text schema `llmwiki-vector-text-v1`: deterministic
heading- and paragraph-aware chunks derived from `WikiPage.text`, with page
title and heading breadcrumb in the embedding input. Paths, local roots,
source-reference labels, graph metadata, raw front matter, and generated
summaries are excluded. Results stay page-level; the best chunk can choose the
snippet, while citations remain the owning page `source_refs`.

The vector sidecar cache is sensitive derived local state outside the served
root. Cache identity includes source scope, a local salted root fingerprint,
projection/content hash, visibility scope, provider/model/revision/dimension,
provider artifact or version evidence for the shipped FastEmbed provider,
distance metric, text schema, index schema, and cache schema. Approved-only and
draft-inclusive indexes are separate identities. Cache schema
`llmwiki-vector-cache-v2` stores float32 embeddings in checksum-named `.npy`
sidecars and stable page/chunk ids, locators, and hashes in compact JSON
metadata. It does not store raw source text, snippets, raw queries, local
roots, model local paths, credentials, or provider responses. Explicit vector
sidecar and FastEmbed model cache directories are resolved before provider
startup and rejected when they are equal to or nested under the served source
root.

Writers publish vector and metadata sidecars first, then the manifest with
`os.replace`. Readers trust only a manifest whose sidecar checksums, shape,
dtype, schema, provider/content identity, and visibility identity validate;
corrupt, partial, mismatched, or stale records are cache misses and are rebuilt
when the provider is available. A sidecar-local exclusive lock coordinates
concurrent builders and is never created under the served source root. During a
cross-process cold build, a competing request can fail with a retryable
build-in-progress error when the lock timeout is reached; it must not read a
partial cache or silently fall back to a different retrieval mode.

Hybrid mode is lexical plus dense retrieval with weighted RRF and bounded
optional read-only orientation hints. It is not universal orientation-first
retrieval, not GraphRAG, and not a universal quality improvement claim.
Canonical root `hot.md`, `index.md`, and `overview.md` pages can form an
orientation guide layer; they are source-owned pages and are never generated,
replaced, or modified by retrieval. Hybrid selects at most three query-relevant
orientation seeds from bounded lexical and vector ranks over the orientation
subset, then expands a capped related set only from trustworthy links,
`source_refs`, and tags visible in or near the matched orientation evidence. If
no safe related set exists, public hybrid falls back exactly to the plain
lexical+dense RRF ordering and payload shape.

Hybrid embeds the query once and reuses that vector to score the orientation
subset, the safe related subset, and the global vector recall channel. It fuses
bounded channel ranks with fixed weighted RRF and constant `60`: lexical/exact
`1.0`, related-vector `1.0`, global-vector `0.75`, orientation evidence
`0.35`, and graph prior `0.25`. For English exact identifier, path, version, or
source-reference queries, the existing exact-required document guard is applied
before fusion so vector-only approximate matches cannot satisfy the query.

The exact local backend is the preview implementation. Release-facing supported
size claims are limited to the current recorded small/team corpus and chunk
counts, vector dimensions, memory envelope, and platforms. Larger scale remains
experimental until 10k, 50k, 100k, and 500k corpus gates are accepted. ANN
indexes, Redis vector search, hosted vector databases, remote embedding
providers, newer embedding models, and rerankers are future experiments, not
bundled defaults.

Negative and unanswerable query diagnostics may report false positives,
top-k behavior, score separation, and citation precision. They do not define a
calibrated abstention threshold, reliable no-evidence confidence score, broad
multilingual quality claim, poisoning-safety claim, SOTA claim, or
vector-database-scale claim. Public vector/hybrid quality or performance
reports require clean commit SHA reruns on Windows and Ubuntu/DGX; dirty
worktree runs remain engineering evidence only.

Detailed candidate caps, scoring semantics, and benchmark posture live in the
[semantic vector retrieval spec](../specs/semantic-vector-retrieval/spec.md)
and
[vector retrieval ADR](decisions/2026-08-01-optional-source-owned-semantic-vector-retrieval-boundary.md).

## Compatible Output Targets

The named producer repositories below are compatible output targets for local
Markdown folders. They are not certified producer integrations, endorsed
upstream plugins, or per-release support claims. `llmwiki-serve` only reads the
generated or stored files on disk when they match the native folder contract or
a supported format adapter.

The optional upstream smoke uses pinned public sample snapshots from selected
targets, not floating branch heads, so those checks are reproducible
compatibility-smoke probes rather than live upstream certification, upstream
producer certification, or quality certification. Current public smoke evidence
is summarized in the central
[Evidence](https://knowledge-bridge-labs.github.io/llmwiki-docs/evidence) page
instead of duplicated here.

| Target | Adapter | Coverage | What is accepted today |
| --- | --- | --- | --- |
| `atomicstrata/llm-wiki-compiler` | `llmwiki-markdown` | Compatible Markdown output target | Markdown folders matching the native LLMWiki contract, including `hot.md`, `index.md`, `overview.md`, and topic pages. |
| `nashsu/llm_wiki` | `llmwiki-markdown` | Compatible Markdown output target | Generated interlinked Markdown knowledge bases when stored or exported as local Markdown files. |
| `SamurAIGPT/llm-wiki-agent` | `llmwiki-markdown` | Compatible Markdown output target | Persistent agent-maintained Markdown wiki outputs when they follow the native Markdown folder shape. |
| `lucasastorian/llmwiki` | `llmwiki-markdown` | Compatible Markdown output target | Generated LLMWiki-style Markdown folders without changing the producer project. |
| `Pratiyush/llm-wiki` | `llmwiki-markdown` | Compatible Markdown output target | Agent-session-derived Markdown knowledge bases when exported as local Markdown files. |
| `langchain-ai/deepagents` `examples/llm-wiki` | `llmwiki-markdown` | Compatible workspace-layout variant | DeepAgents LLM Wiki workspaces where the repository root contains `raw/`, runner-managed `log.md`, and a nested served `wiki/` folder with `wiki/index.md`, canonical pages, and optional `wiki/query/*.md` routing hints. |
| Obsidian vault | `obsidian` | Format adapter | Markdown files, YAML front matter, wikilinks, tags, and `.obsidian` workspace detection. |
| `logseq/logseq` | `logseq` | Format adapter | `pages/` and `journals/` Markdown or Org files plus page references. |
| `foambubble/foam` | `foam` | Format adapter | VS Code Markdown workspaces with wikilinks and optional `.foam` markers. |
| `dendronhq/dendron` | `dendron` | Format adapter | Dendron Markdown vaults and dotted hierarchy file names. |
| `jackyzha0/quartz` | `quartz` | Format adapter | Quartz `content/` Markdown folders and generated-site source vaults. |

## Maturity Levels

**Native Markdown adapter:** The LLMWiki Markdown path is the primary supported
model. It is designed for compiled or generated Markdown folders where `hot.md`,
`index.md`, `overview.md`, topic pages, YAML front matter, Markdown links, and
wikilinks are the contract. Named producer repositories above are compatibility
targets for that Markdown output shape, not per-release integration certifications
unless fixture tests cover a specific producer output.

The DeepAgents LLM Wiki example is treated as a variant of this native Markdown
path, not as a projection layer or managed runtime dependency. `llmwiki-serve`
does not run LangSmith Sandbox, Context Hub sync, `ingest`, `query`, or `lint`;
it reads the resulting local `wiki/` files when they are present.

**Format adapters:** Obsidian, Logseq, Foam, Dendron, and Quartz adapters support
common on-disk Markdown layouts and project markers. They project those workspaces
into the same canonical page and graph model as native LLMWiki Markdown.

**Known gaps:** The service does not emulate every application-specific runtime
feature. Advanced plugin metadata, non-Markdown assets, application databases,
custom build transforms, Dendron schema validation, full Logseq block semantics,
and Quartz theme/plugin behavior are outside the current projection model.

Bundled fixtures cover representative local folder layouts and the projection
contract for those examples. They do not guarantee compatibility with every
upstream producer release, plugin setting, theme transform, synchronization
state, or private workspace convention.

The optional `scripts/upstream_candidate_smoke.py` gate extends this with pinned
public sample/template snapshots. It fetches immutable commits into a temporary
directory outside the repository and validates only static Markdown inputs
through the current projection/service behavior. It is not upstream release
certification. Candidate projects that require credentials, desktop runtimes,
LLM provider calls, or heavy application builds are intentionally excluded unless
they also provide a small static Markdown sample folder that can be checked
without those dependencies.

### Candidate Smoke Coverage

The generated compatibility suite in `tests/test_candidate_samples.py` creates
one local synthetic folder per catalog target, including the DeepAgents
`raw/`/`wiki/`/`log.md` workspace-layout variant. It proves that `llmwiki-serve`
can project each accepted on-disk shape into manifest, context, search, read,
graph, HTTP, MCP-style, MCP Streamable HTTP, and opt-in A2A-style surfaces
without mutating the input tree.
The A2A-style checks use the explicit compatibility opt-in; default app
instances keep those routes disabled.

The upstream snapshot smoke checks a different evidence path: it clones only
pinned public commits, never floating branches, and runs the same service checks
against real static upstream folders. The current central Evidence page records
a 12-case actual-pinned Windows report, including product URLs, pinned commits,
adapters, file/page counts, graph counts, license evidence, and mutation
status. Treat that report as projection compatibility evidence only; it does
not measure retrieval quality, answer quality, model behavior,
vendor-runtime conformance, or upstream producer certification.

The generated compatibility suite still covers synthetic catalog output shapes
that may not have exact upstream smoke evidence in a given report. The optional
upstream smoke can also include static LLMWiki-style folders beyond the original
catalog when they provide useful public compatibility evidence without requiring
provider calls or source mutation.

## Graph Projection

The projection layer is the boundary between source formats and served graph
output. Adapters and parsers preserve source facts, including optional
`graph/graph.json` sidecar edge facts. Projection turns those loaded facts into a
stable graph and does not locate or read sidecar files directly:

- `page:*` nodes for every loaded page.
- `heading:*` nodes connected from pages with `contains` edges.
- `source:*` nodes connected from pages with `cites` edges.
- `tag:*` nodes connected from pages with `tagged` edges.
- Placeholder/external nodes for unresolved wikilinks and explicit graph edges.
- `links_to` edges when Markdown links or wikilinks resolve to another loaded page.
- Dendron hierarchy edges derived from dotted note names.
- Optional edges from adapter-loaded `graph/graph.json` facts when native LLMWiki producers emit an explicit graph beside the wiki.

Sidecar graph facts are accepted as either a JSON object with an `edges` array
or a top-level array of edge objects. Each edge fact uses this schema:

```json
{
  "from": "overview",
  "to": "concepts/release",
  "type": "supports",
  "confidence": 0.88
}
```

Endpoint keys may be `from` and `to`, or `source` and `target`. `type` is
optional and defaults to `related_to`. `confidence` is optional, but when
present it must be numeric; boolean or string confidence values are ignored.

The graph is intentionally derived, not authoritative. The source folder remains
the system of record, with Markdown pages and optional sidecar facts loaded by
adapters.

`graph/graph.json` is a source-owned graph sidecar, not managed context.
Experimental managed context is generic-Markdown only and stores an external
opaque sidecar outside the source root. Native `hot.md`, `index.md`,
`overview.md`, and root `quickstart.md` remain source-owned and untouched; the
managed-context sidecar is not a source page, not `graph/graph.json`, and not a
replacement for authored structure.

## Graph Neighborhood Lookup

`GET /graph/neighborhood` and MCP `llmwiki_graph_neighbors` expose a bounded
subgraph around one or more seed values. Seeds resolve to graph node ids first,
then page ids, paths, labels, and slugs. Callers can choose outgoing, incoming,
or bidirectional traversal, cap depth and result size, and filter relation
types.

This operation is intended for CKG-like graph-guided retrieval by host agents:
use `/query` or `llmwiki_context` for orientation, then use graph neighborhood
lookup when the question depends on relationships such as prerequisites,
dependencies, source lineage, ownership, or policy. It does not claim
compatibility with any external CKG standard and does not replace search or
exact page reads.

Neighborhood lookup uses the same graph visibility boundary as `/graph`. Draft
and unapproved page nodes are hidden unless the server is explicitly configured
to allow draft access and the request opts into `include_drafts=true`.

When a wiki is served from a nested source root such as `wiki/`, sidecar
endpoints may use either source-root-relative paths such as `concepts/release`
or root-relative paths such as `wiki/concepts/release.md`; both forms resolve to
the same loaded page. Duplicate sidecar and wikilink edges with the same
`source`, `target`, and `relation` are deduplicated while preserving sidecar
metadata such as `source`, `path`, and `confidence`.
