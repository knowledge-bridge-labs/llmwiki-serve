from __future__ import annotations

import json
import os
from enum import StrEnum
from pathlib import Path
from typing import Annotated, NoReturn, TypeAlias

import typer

from .api import (
    GRAPH_LIMIT_MAX,
    GRAPH_LIMIT_MIN,
    QUERY_LIMIT_MAX,
    QUERY_LIMIT_MIN,
    create_app,
)
from .instances import (
    HEALTH_PROBE_TIMEOUT_SECONDS,
    InstanceInfo,
    InstanceRecord,
    LocalInstanceDiscoveryResult,
    discover_local_instances,
    register_instance,
    unregister_instance,
)
from .models import SearchMode
from .projection_store import (
    ProjectionStoreBackend,
    RedisFailurePolicy,
    create_projection_store,
)
from .service import LlmWikiService

app = typer.Typer(help="Serve or inspect an LLMWiki Markdown folder.")


class SearchModeChoice(StrEnum):
    lexical = "lexical"
    literal = "literal"


WikiRootArgument: TypeAlias = Annotated[
    Path,
    typer.Argument(
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        resolve_path=True,
        help="Existing LLMWiki-compatible Markdown folder to read.",
    ),
]
QueryLimitOption: TypeAlias = Annotated[
    int,
    typer.Option(
        "--limit",
        "-l",
        min=QUERY_LIMIT_MIN,
        max=QUERY_LIMIT_MAX,
        help=f"Maximum context/search evidence items ({QUERY_LIMIT_MIN}-{QUERY_LIMIT_MAX}).",
    ),
]
SearchModeOption: TypeAlias = Annotated[
    SearchModeChoice,
    typer.Option(
        "--mode",
        help="Search mode: lexical ranking or literal exact-substring matching.",
    ),
]
SearchFieldsOption: TypeAlias = Annotated[
    list[str] | None,
    typer.Option(
        "--fields",
        help=(
            "Comma-separated or repeated SearchResult fields to return. "
            "page_id is always included when set."
        ),
    ),
]
SnippetCharsOption: TypeAlias = Annotated[
    int | None,
    typer.Option(
        "--snippet-chars",
        min=0,
        max=2_000,
        help="Maximum characters per result snippet. Use 0 for empty snippets.",
    ),
]
MinScoreOption: TypeAlias = Annotated[
    float | None,
    typer.Option(
        "--min-score",
        min=0.0,
        help="Drop search results below this score.",
    ),
]
ExcludePageIdOption: TypeAlias = Annotated[
    list[str] | None,
    typer.Option(
        "--exclude-page-id",
        help="Page id or path to exclude from search evidence. Repeat for multiple pages.",
    ),
]
ServePortOption: TypeAlias = Annotated[
    int,
    typer.Option("--port", min=1, max=65_535, help="TCP port for the HTTP server."),
]
RefreshIntervalOption: TypeAlias = Annotated[
    float,
    typer.Option(
        "--refresh-interval-seconds",
        min=0.0,
        help=(
            "Seconds to reuse the in-memory projection before checking files again. "
            "Default 0 keeps strict per-request freshness."
        ),
    ),
]
ProducerManifestOption: TypeAlias = Annotated[
    Path | None,
    typer.Option(
        "--producer-manifest",
        help=(
            "Root-relative or absolute producer freshness marker. When present "
            "inside the served root, strict refresh checks use this marker "
            "instead of rescanning all source files."
        ),
    ),
]
IoLogOption: TypeAlias = Annotated[
    str | None,
    typer.Option(
        "--io-log",
        help=(
            "Serve I/O JSONL log path, or 'off' to disable. Defaults to "
            ".runtime-logs/llmwiki-serve-io.jsonl; env LLMWIKI_SERVE_IO_LOG "
            "can also be 'off' or a path."
        ),
    ),
]
ProjectionStoreOption: TypeAlias = Annotated[
    ProjectionStoreBackend | None,
    typer.Option(
        "--projection-store",
        help=("Projection cache backend. Use redis only after installing llmwiki-serve\\[redis]."),
    ),
]
RedisFailurePolicyOption: TypeAlias = Annotated[
    RedisFailurePolicy,
    typer.Option(
        "--redis-failure-policy",
        help="Redis outage behavior. fallback-local keeps serving from process memory.",
    ),
]
GraphDefaultLimitOption: TypeAlias = Annotated[
    int | None,
    typer.Option(
        "--graph-default-limit",
        min=GRAPH_LIMIT_MIN,
        max=GRAPH_LIMIT_MAX,
        help=(
            "Default /graph and llmwiki_graph node limit when clients omit limit. "
            "Env: LLMWIKI_GRAPH_DEFAULT_LIMIT."
        ),
    ),
]
ContextDefaultLimitOption: TypeAlias = Annotated[
    int | None,
    typer.Option(
        "--context-default-limit",
        min=QUERY_LIMIT_MIN,
        max=QUERY_LIMIT_MAX,
        help=(
            "Default query/search and llmwiki_context evidence limit when clients omit "
            "limit. Env: LLMWIKI_CONTEXT_DEFAULT_LIMIT."
        ),
    ),
]
McpServerNameOption: TypeAlias = Annotated[
    str | None,
    typer.Option(
        "--mcp-server-name",
        "--mcp-title",
        help=(
            "Override the MCP Streamable HTTP server name and default JSON-RPC tool "
            "scope label. Env: LLMWIKI_MCP_SERVER_NAME or LLMWIKI_MCP_TITLE."
        ),
    ),
]
McpInstructionsOption: TypeAlias = Annotated[
    str | None,
    typer.Option(
        "--mcp-instructions",
        help=("Override MCP Streamable HTTP server instructions. Env: LLMWIKI_MCP_INSTRUCTIONS."),
    ),
]
McpToolDescriptionPrefixOption: TypeAlias = Annotated[
    str | None,
    typer.Option(
        "--mcp-tool-description-prefix",
        help=(
            "Override the prefix added to MCP JSON-RPC and Streamable HTTP tool "
            "descriptions. Env: LLMWIKI_MCP_TOOL_DESCRIPTION_PREFIX."
        ),
    ),
]
InstanceStateDirOption: TypeAlias = Annotated[
    Path | None,
    typer.Option(
        "--state-dir",
        help=(
            "Local instance registry state directory. Defaults to "
            "$LLMWIKI_SERVE_STATE_DIR or the per-user state directory."
        ),
    ),
]
ProbePortOption: TypeAlias = Annotated[
    list[int] | None,
    typer.Option(
        "--probe-port",
        help=(
            "Manually probe an explicit loopback port for diagnostics. "
            "Repeat for multiple ports. No ports are guessed by default."
        ),
    ),
]
ProbeTimeoutOption: TypeAlias = Annotated[
    float,
    typer.Option(
        "--probe-timeout-seconds",
        min=0.1,
        max=30.0,
        help="Seconds to wait for each local /health probe used by ls/status.",
    ),
]


@app.command()
def manifest(root: WikiRootArgument) -> None:
    """Print wiki manifest JSON."""
    try:
        typer.echo(LlmWikiService(root).manifest().model_dump_json(indent=2))
    except FileNotFoundError as exc:
        exit_with_error(str(exc))


@app.command()
def query(
    root: WikiRootArgument,
    text: str,
    limit: QueryLimitOption = 8,
    mode: SearchModeOption = SearchModeChoice.lexical,
    fields: SearchFieldsOption = None,
    snippet_chars: SnippetCharsOption = None,
    min_score: MinScoreOption = None,
    exclude_page_id: ExcludePageIdOption = None,
) -> None:
    """Build a context pack for a query."""
    try:
        result_fields = split_cli_values(fields)
        typer.echo(
            LlmWikiService(root)
            .context(
                text,
                limit=limit,
                mode=search_mode_value(mode),
                fields=result_fields,
                snippet_chars=snippet_chars,
                min_score=min_score,
                exclude_page_ids=split_cli_values(exclude_page_id) or [],
            )
            .model_dump_json(indent=2, exclude_unset=result_fields is not None)
        )
    except FileNotFoundError as exc:
        exit_with_error(str(exc))


@app.command("search")
def search_pages(
    root: WikiRootArgument,
    text: str,
    limit: QueryLimitOption = 8,
    mode: SearchModeOption = SearchModeChoice.lexical,
    fields: SearchFieldsOption = None,
    snippet_chars: SnippetCharsOption = None,
    min_score: MinScoreOption = None,
    exclude_page_id: ExcludePageIdOption = None,
) -> None:
    """Search pages and print result JSON."""
    try:
        typer.echo(
            json.dumps(
                {
                    "results": LlmWikiService(root).search(
                        text,
                        limit=limit,
                        mode=search_mode_value(mode),
                        fields=split_cli_values(fields),
                        snippet_chars=snippet_chars,
                        min_score=min_score,
                        exclude_page_ids=split_cli_values(exclude_page_id) or [],
                    )
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except FileNotFoundError as exc:
        exit_with_error(str(exc))


@app.command("source-refs")
def source_refs(root: WikiRootArgument) -> None:
    """Print typed source-reference handles JSON."""
    try:
        typer.echo(LlmWikiService(root).source_refs().model_dump_json(indent=2))
    except FileNotFoundError as exc:
        exit_with_error(str(exc))


@app.command("source-bundle")
def source_bundle(root: WikiRootArgument) -> None:
    """Print source bundle manifest JSON."""
    try:
        typer.echo(LlmWikiService(root).source_bundle().model_dump_json(indent=2))
    except FileNotFoundError as exc:
        exit_with_error(str(exc))


@app.command("ls")
def ls_instances(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable instance JSON."),
    ] = False,
    probe: Annotated[
        bool,
        typer.Option(
            "--probe/--no-probe",
            help="Probe each live registered instance through its local /health endpoint.",
        ),
    ] = True,
    prune_stale: Annotated[
        bool,
        typer.Option(
            "--prune-stale",
            help="Remove stale registry records after reporting them.",
        ),
    ] = False,
    processes: Annotated[
        bool,
        typer.Option(
            "--processes/--no-processes",
            help="Discover unregistered local serve processes from the OS process table.",
        ),
    ] = True,
    probe_port: ProbePortOption = None,
    probe_timeout_seconds: ProbeTimeoutOption = HEALTH_PROBE_TIMEOUT_SECONDS,
    state_dir: InstanceStateDirOption = None,
) -> None:
    """List local llmwiki-serve instances."""
    try:
        probe_ports = validate_probe_ports(probe_port or [], source="--probe-port")
    except ValueError as exc:
        exit_with_error(str(exc))
    discovery = discover_local_instances(
        state_dir=state_dir,
        probe=probe,
        prune_stale=prune_stale,
        probe_timeout_seconds=probe_timeout_seconds,
        processes=processes,
        manual_probe_ports=probe_ports,
    )
    print_instance_discovery(discovery, json_output=json_output)


@app.command("status")
def status_instances(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable instance JSON."),
    ] = False,
    probe: Annotated[
        bool,
        typer.Option(
            "--probe/--no-probe",
            help="Probe each live registered instance through its local /health endpoint.",
        ),
    ] = True,
    prune_stale: Annotated[
        bool,
        typer.Option(
            "--prune-stale",
            help="Remove stale registry records after reporting them.",
        ),
    ] = False,
    processes: Annotated[
        bool,
        typer.Option(
            "--processes/--no-processes",
            help="Discover unregistered local serve processes from the OS process table.",
        ),
    ] = True,
    probe_port: ProbePortOption = None,
    probe_timeout_seconds: ProbeTimeoutOption = HEALTH_PROBE_TIMEOUT_SECONDS,
    state_dir: InstanceStateDirOption = None,
) -> None:
    """Alias for ls."""
    try:
        probe_ports = validate_probe_ports(probe_port or [], source="--probe-port")
    except ValueError as exc:
        exit_with_error(str(exc))
    discovery = discover_local_instances(
        state_dir=state_dir,
        probe=probe,
        prune_stale=prune_stale,
        probe_timeout_seconds=probe_timeout_seconds,
        processes=processes,
        manual_probe_ports=probe_ports,
    )
    print_instance_discovery(discovery, json_output=json_output)


@app.command()
def serve(
    root: WikiRootArgument,
    host: str = "127.0.0.1",
    port: ServePortOption = 8765,
    allow_drafts: Annotated[
        bool,
        typer.Option(
            "--allow-drafts",
            help="Allow HTTP and MCP-style include_drafts requests to return draft pages.",
        ),
    ] = False,
    cors_origin: Annotated[
        list[str] | None,
        typer.Option(
            "--cors-origin",
            help="Allowed browser CORS origin. Repeat for multiple explicit origins.",
        ),
    ] = None,
    enable_a2a_compat: Annotated[
        bool,
        typer.Option(
            "--enable-a2a-compat",
            help="Enable legacy A2A-style compatibility endpoints.",
        ),
    ] = False,
    refresh_interval_seconds: RefreshIntervalOption = 0.0,
    producer_manifest: ProducerManifestOption = None,
    io_log: IoLogOption = None,
    projection_store_backend: ProjectionStoreOption = None,
    redis_url: Annotated[
        str | None,
        typer.Option(
            "--redis-url",
            help="Redis/Valkey URL for --projection-store=redis.",
        ),
    ] = None,
    redis_failure_policy: RedisFailurePolicyOption = "fallback-local",
    cache_namespace: Annotated[
        str | None,
        typer.Option(
            "--cache-namespace",
            help="Projection cache namespace for shared Redis/Valkey deployments.",
        ),
    ] = None,
    source_id: Annotated[
        str | None,
        typer.Option(
            "--source-id",
            help=(
                "Explicit source id for cache keys and manifests. Recommended with "
                "--projection-store=redis."
            ),
        ),
    ] = None,
    graph_default_limit: GraphDefaultLimitOption = None,
    context_default_limit: ContextDefaultLimitOption = None,
    mcp_server_name: McpServerNameOption = None,
    mcp_instructions: McpInstructionsOption = None,
    mcp_tool_description_prefix: McpToolDescriptionPrefixOption = None,
) -> None:
    """Run the HTTP, MCP-style JSON-RPC, and MCP Streamable HTTP server."""
    import uvicorn

    try:
        projection_backend = resolve_projection_store_backend(projection_store_backend)
        resolved_redis_url = redis_url or os.getenv("LLMWIKI_REDIS_URL")
        resolved_namespace = cache_namespace or os.getenv("LLMWIKI_CACHE_NAMESPACE") or "default"
        resolved_source_id = source_id or os.getenv("LLMWIKI_SOURCE_ID")
        resolved_graph_default_limit = resolve_int_option_env(
            graph_default_limit,
            "LLMWIKI_GRAPH_DEFAULT_LIMIT",
        )
        resolved_context_default_limit = resolve_int_option_env(
            context_default_limit,
            "LLMWIKI_CONTEXT_DEFAULT_LIMIT",
        )
        resolved_mcp_server_name = (
            mcp_server_name
            or os.getenv("LLMWIKI_MCP_SERVER_NAME")
            or os.getenv("LLMWIKI_MCP_TITLE")
        )
        resolved_mcp_instructions = mcp_instructions or os.getenv("LLMWIKI_MCP_INSTRUCTIONS")
        resolved_mcp_tool_description_prefix = mcp_tool_description_prefix or os.getenv(
            "LLMWIKI_MCP_TOOL_DESCRIPTION_PREFIX"
        )
        projection_store = create_projection_store(
            projection_backend,
            redis_url=resolved_redis_url,
            redis_failure_policy=redis_failure_policy,
        )
        preflight_service = LlmWikiService(
            root,
            refresh_interval_seconds=refresh_interval_seconds,
            producer_manifest_path=producer_manifest,
            projection_store=projection_store,
            cache_namespace=resolved_namespace,
            source_id=resolved_source_id,
        )
        preflight_service.index()
        fastapi_app = create_app(
            root,
            allow_drafts=allow_drafts,
            cors_origins=cors_origin,
            enable_a2a_compat=enable_a2a_compat,
            refresh_interval_seconds=refresh_interval_seconds,
            producer_manifest_path=producer_manifest,
            io_log=io_log,
            projection_store=projection_store,
            cache_namespace=resolved_namespace,
            source_id=resolved_source_id,
            graph_default_limit=resolved_graph_default_limit,
            context_default_limit=resolved_context_default_limit,
            mcp_server_name=resolved_mcp_server_name,
            mcp_instructions=resolved_mcp_instructions,
            mcp_tool_description_prefix=resolved_mcp_tool_description_prefix,
        )
    except FileNotFoundError as exc:
        exit_with_error(str(exc))
    except (RuntimeError, ValueError) as exc:
        exit_with_error(str(exc))

    registry_path = None
    try:
        registry_path = register_instance(
            InstanceRecord.from_manifest(
                pid=os.getpid(),
                host=host,
                port=port,
                manifest=preflight_service.manifest(),
            )
        )
    except OSError as exc:
        typer.secho(f"Warning: failed to write local instance registry: {exc}", err=True)

    try:
        uvicorn.run(
            fastapi_app,
            host=host,
            port=port,
        )
    finally:
        unregister_instance(registry_path)


def resolve_projection_store_backend(
    value: ProjectionStoreBackend | None,
) -> ProjectionStoreBackend:
    if value is not None:
        return value
    env_value = os.getenv("LLMWIKI_PROJECTION_STORE")
    if env_value == "memory":
        return "memory"
    if env_value == "redis":
        return "redis"
    if env_value:
        raise ValueError("LLMWIKI_PROJECTION_STORE must be 'memory' or 'redis'")
    return "memory"


def resolve_int_option_env(value: int | None, env_name: str) -> int | None:
    if value is not None:
        return value
    env_value = os.getenv(env_name)
    if not env_value:
        return None
    try:
        return int(env_value)
    except ValueError as exc:
        raise ValueError(f"{env_name} must be an integer") from exc


def split_cli_values(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    result: list[str] = []
    for value in values:
        for item in value.split(","):
            normalized = item.strip()
            if normalized and normalized not in result:
                result.append(normalized)
    return result


def validate_probe_ports(values: list[int], *, source: str) -> list[int]:
    ports: list[int] = []
    for port in values:
        if port <= 0 or port > 65_535:
            raise ValueError(f"{source} ports must be between 1 and 65535")
        if port not in ports:
            ports.append(port)
    return ports


def search_mode_value(mode: SearchModeChoice) -> SearchMode:
    return "literal" if mode is SearchModeChoice.literal else "lexical"


def print_instance_discovery(
    discovery: LocalInstanceDiscoveryResult,
    *,
    json_output: bool,
) -> None:
    if json_output:
        typer.echo(
            json.dumps(
                discovery.model_dump(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return
    print_instances(discovery.instances, json_output=False)
    for warning in sorted(set(discovery.warnings)):
        typer.secho(f"Warning: {warning}", fg=typer.colors.YELLOW, err=True)


def print_instances(instances: list[InstanceInfo], *, json_output: bool) -> None:
    if json_output:
        typer.echo(
            json.dumps(
                {"instances": [item.model_dump() for item in instances]},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return
    if not instances:
        typer.echo("No llmwiki-serve instances found.")
        return
    typer.echo(render_instance_table(instances))


def render_instance_table(instances: list[InstanceInfo]) -> str:
    headers = [
        "PID",
        "URL",
        "STATUS",
        "REGISTRY",
        "VERSION",
        "ROOT",
        "ADAPTER",
        "PAGES",
        "SOURCE",
        "NOTES",
    ]
    rows = [
        [
            str(item.record.pid) if item.record.pid > 0 else "-",
            item.record.url.removeprefix("http://"),
            item.status,
            "registered" if item.registered else "orphan",
            item.version or "unknown",
            redacted_root_label(item.record.root),
            item.record.adapter or "-",
            f"{item.record.approved_page_count}/{item.record.page_count}",
            item.record.source_id or "-",
            ",".join(sorted(item.notes)) or "-",
        ]
        for item in instances
    ]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]
    lines = [
        "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)),
        "  ".join("-" * width for width in widths),
    ]
    lines.extend(
        "  ".join(value.ljust(widths[index]) for index, value in enumerate(row)) for row in rows
    )
    return "\n".join(lines)


def redacted_root_label(value: str) -> str:
    if not value:
        return "-"
    try:
        path = Path(value).expanduser()
        anchor = path.anchor
        parts = [part for part in path.parts if part and part != anchor]
    except (OSError, ValueError):
        return "-"
    if not parts:
        return "-"
    return ".../" + "/".join(parts[-2:])


def exit_with_error(message: str) -> NoReturn:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)
