from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, NoReturn, TypeAlias

import typer

from .api import QUERY_LIMIT_MAX, QUERY_LIMIT_MIN, create_app
from .projection_store import (
    ProjectionStoreBackend,
    RedisFailurePolicy,
    create_projection_store,
)
from .service import LlmWikiService

app = typer.Typer(help="Serve or inspect an LLMWiki Markdown folder.")

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


@app.command()
def manifest(root: WikiRootArgument) -> None:
    """Print wiki manifest JSON."""
    try:
        typer.echo(LlmWikiService(root).manifest().model_dump_json(indent=2))
    except FileNotFoundError as exc:
        exit_with_error(str(exc))


@app.command()
def query(root: WikiRootArgument, text: str, limit: QueryLimitOption = 8) -> None:
    """Build a context pack for a query."""
    try:
        typer.echo(LlmWikiService(root).context(text, limit=limit).model_dump_json(indent=2))
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
) -> None:
    """Run the HTTP, MCP-style JSON-RPC, and MCP Streamable HTTP server."""
    import uvicorn

    try:
        projection_backend = resolve_projection_store_backend(projection_store_backend)
        resolved_redis_url = redis_url or os.getenv("LLMWIKI_REDIS_URL")
        resolved_namespace = cache_namespace or os.getenv("LLMWIKI_CACHE_NAMESPACE") or "default"
        resolved_source_id = source_id or os.getenv("LLMWIKI_SOURCE_ID")
        projection_store = create_projection_store(
            projection_backend,
            redis_url=resolved_redis_url,
            redis_failure_policy=redis_failure_policy,
        )
        LlmWikiService(
            root,
            refresh_interval_seconds=refresh_interval_seconds,
            producer_manifest_path=producer_manifest,
            projection_store=projection_store,
            cache_namespace=resolved_namespace,
            source_id=resolved_source_id,
        ).index()
    except FileNotFoundError as exc:
        exit_with_error(str(exc))
    except (RuntimeError, ValueError) as exc:
        exit_with_error(str(exc))

    uvicorn.run(
        create_app(
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
        ),
        host=host,
        port=port,
    )


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


def exit_with_error(message: str) -> NoReturn:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)
