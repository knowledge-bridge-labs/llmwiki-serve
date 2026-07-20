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
| Search/context | Ranks approved pages, adds hot/index/overview orientation, withholds drafts by default, and returns context packs for agents. |
| Graph output | Returns projected nodes and edges through `/graph`, bounded neighborhoods through `/graph/neighborhood`, MCP graph tools, source-bundle source refs, and context pack graph fields. |
| Serve I/O logging | Writes local JSONL request/response events for HTTP, MCP-style, MCP Streamable HTTP, and opt-in A2A-style flows with credential/header/local-root redaction. |

Protocol scope: the current serving surface is HTTP plus MCP-style JSON-RPC, MCP
Streamable HTTP, and opt-in A2A-style message shapes. Streamable HTTP is served
through the official MCP Python SDK FastMCP transport; the compatibility
surfaces are not a claim of A2A protocol certification, exhaustive runtime
feature completeness, or upstream integration support.

The HTTP API installs CORS middleware for local browser development only by
default: `localhost`, `127.0.0.1`, and IPv6 localhost `[::1]` origins on any
port are allowed through a regex, and wildcard origins are not enabled.
Operators can pass explicit origins when creating the app or running the CLI;
when explicit origins are configured, they replace the default local allowlist.

Network HTTP and MCP tool calls with `include_drafts=true` are ignored
unless the app is created with `allow_drafts=True` or the CLI server is started
with `--allow-drafts`. Network manifest responses omit the local source root path;
the CLI manifest remains local operator output and includes the root.

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

## Compatible Output Targets

The named producer repositories below are compatible output targets for local
Markdown folders. They are not certified producer integrations, endorsed
upstream plugins, or per-release support claims. `llmwiki-serve` only reads the
generated or stored files on disk when they match the native folder contract or
a supported format adapter.

The optional upstream smoke uses pinned public sample snapshots from selected
targets, not floating branch heads, so those checks are reproducible compatibility
probes rather than live upstream certification.

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

The upstream snapshot smoke is narrower and stronger in a different way: it
clones only pinned public commits, never floating branches, and runs the same
service checks against real static upstream folders. As of the 2026-07-01 audit,
the actual upstream smoke covers these public static snapshots:

| Target | Smoke case | Static folder checked |
| --- | --- | --- |
| `atomicstrata/llm-wiki-compiler` | `atomic-compiler-basic` | `examples/basic/wiki` generated LLMWiki Markdown. |
| `SamurAIGPT/llm-wiki-agent` | `samuraigpt-agent` | Repository root Markdown wiki snapshot. |
| `Pratiyush/llm-wiki` | `pratiyush-llm-wiki` | Repository root Markdown knowledge-base snapshot. |
| `logseq/logseq` | `logseq-exporter-test-graph` | `deps/graph-parser/test/resources/exporter-test-graph` static Logseq graph fixture. |
| `foambubble/foam` | `foam-template` | Repository root static Foam template workspace. |
| `dendronhq/dendron` | `dendron-test-workspace` | `test-workspace` static Dendron workspace. |
| `jason-effi-lab/karpathy-llm-wiki-vault` | `karpathy-llm-wiki-vault` | `wiki` static LLMWiki Markdown vault with concepts, entities, sources, and syntheses. |
| `luotwo/llm-wiki` | `luotwo-llm-wiki` | Repository root with nested static `wiki/` source root. |
| `nishio/llm-wiki-about-delite` | `nishio-llm-wiki-about-delite` | Repository root static Quartz source tree with config and Markdown pages. |
| `iBlinkQ/llm-wiki-obsidian-blink` | `iblinkq-llm-wiki-obsidian-blink` | Repository root static LLMWiki Obsidian vault with `.obsidian` marker. |

The generated compatibility suite still covers the original catalog targets that
do not currently have exact upstream smoke cases:

| Target | Why it is not an upstream smoke case |
| --- | --- |
| `nashsu/llm_wiki` | The public repository has docs and the source app, but no small pinned generated LLMWiki Markdown folder matching the native folder contract without running the app/provider-backed workflow. |
| `lucasastorian/llmwiki` | The public repository describes runtime workspace creation; it does not include a small static generated `wiki/` folder that can be cloned and served directly. |
| `jackyzha0/quartz` | The repository includes Quartz docs Markdown, but not a current adapter-detectable `quartz.config.*` plus populated `content/` site root; creating that root requires Quartz initialization/build steps. |

The smoke also includes static LLMWiki-style folders beyond the original catalog
when they provide useful public compatibility evidence without requiring
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
