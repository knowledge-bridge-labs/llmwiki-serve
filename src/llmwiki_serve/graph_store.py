from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from .models import GraphEdge, GraphNode

GRAPH_STORE_SCHEMA_VERSION = "graph-store-v1"
GraphStoreBackend = Literal["none", "sqlite"]
GraphStoreFailurePolicy = Literal["fallback-local", "fail-fast"]
GraphVisibilityScope = Literal["approved", "all"]


@dataclass(frozen=True)
class GraphStoreKey:
    namespace: str
    source_id: str
    bundle_id: str
    projection_signature: str
    visibility_scope: GraphVisibilityScope = "approved"
    schema_version: str = GRAPH_STORE_SCHEMA_VERSION


@dataclass(frozen=True)
class GraphRecord:
    key: GraphStoreKey
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class GraphStore(Protocol):
    def get(self, key: GraphStoreKey) -> GraphRecord | None: ...

    def put(self, record: GraphRecord) -> None: ...

    def invalidate_source(self, *, namespace: str, source_id: str) -> None: ...


class SqliteGraphStore:
    """SQLite-backed derived graph snapshot cache.

    The source wiki remains the system of record. The store only caches graph
    payloads after projection and visibility filtering.
    """

    backend_kind: Literal["sqlite"] = "sqlite"

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            ensure_schema(connection)

    def get(self, key: GraphStoreKey) -> GraphRecord | None:
        stored_key = sqlite_key_values(key)
        with self._connect() as connection:
            ensure_schema(connection)
            snapshot = connection.execute(
                """
                SELECT node_count, edge_count, payload_digest
                FROM graph_snapshots
                WHERE schema_version = ?
                  AND namespace = ?
                  AND source_id = ?
                  AND bundle_id = ?
                  AND projection_signature = ?
                  AND visibility_scope = ?
                """,
                stored_key,
            ).fetchone()
            if snapshot is None:
                return None
            node_rows = connection.execute(
                """
                SELECT node_id, label, kind, path, metadata_json
                FROM graph_nodes
                WHERE schema_version = ?
                  AND namespace = ?
                  AND source_id = ?
                  AND bundle_id = ?
                  AND projection_signature = ?
                  AND visibility_scope = ?
                ORDER BY position
                """,
                stored_key,
            ).fetchall()
            edge_rows = connection.execute(
                """
                SELECT source, target, relation, metadata_json
                FROM graph_edges
                WHERE schema_version = ?
                  AND namespace = ?
                  AND source_id = ?
                  AND bundle_id = ?
                  AND projection_signature = ?
                  AND visibility_scope = ?
                ORDER BY position
                """,
                stored_key,
            ).fetchall()

        try:
            if int(snapshot["node_count"]) != len(node_rows) or int(
                snapshot["edge_count"]
            ) != len(edge_rows):
                return None
            record = GraphRecord(
                key=key,
                nodes=[
                    GraphNode.model_validate(
                        {
                            "id": row["node_id"],
                            "label": row["label"],
                            "kind": row["kind"],
                            "path": row["path"],
                            "metadata": json.loads(row["metadata_json"]),
                        }
                    )
                    for row in node_rows
                ],
                edges=[
                    GraphEdge.model_validate(
                        {
                            "source": row["source"],
                            "target": row["target"],
                            "relation": row["relation"],
                            "metadata": json.loads(row["metadata_json"]),
                        }
                    )
                    for row in edge_rows
                ],
            )
            if str(snapshot["payload_digest"]) != graph_record_digest(
                record.nodes, record.edges
            ):
                return None
            return record
        except (TypeError, ValueError, KeyError, json.JSONDecodeError, ValidationError):
            return None

    def put(self, record: GraphRecord) -> None:
        stored_key = sqlite_key_values(record.key)
        created_at = time.time()
        node_rows = [
            (
                *stored_key,
                position,
                node.id,
                node.label,
                node.kind,
                node.path,
                json.dumps(node.metadata, ensure_ascii=False, sort_keys=True),
            )
            for position, node in enumerate(record.nodes)
        ]
        edge_rows = [
            (
                *stored_key,
                position,
                edge.source,
                edge.target,
                edge.relation,
                json.dumps(edge.metadata, ensure_ascii=False, sort_keys=True),
            )
            for position, edge in enumerate(record.edges)
        ]
        with self._connect() as connection:
            ensure_schema(connection)
            delete_record(connection, record.key)
            connection.execute(
                """
                INSERT INTO graph_snapshots (
                  schema_version,
                  namespace,
                  source_id,
                  bundle_id,
                  projection_signature,
                  visibility_scope,
                  created_at,
                  node_count,
                  edge_count,
                  payload_digest
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    *stored_key,
                    created_at,
                    len(record.nodes),
                    len(record.edges),
                    graph_record_digest(record.nodes, record.edges),
                ),
            )
            connection.executemany(
                """
                INSERT INTO graph_nodes (
                  schema_version,
                  namespace,
                  source_id,
                  bundle_id,
                  projection_signature,
                  visibility_scope,
                  position,
                  node_id,
                  label,
                  kind,
                  path,
                  metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                node_rows,
            )
            connection.executemany(
                """
                INSERT INTO graph_edges (
                  schema_version,
                  namespace,
                  source_id,
                  bundle_id,
                  projection_signature,
                  visibility_scope,
                  position,
                  source,
                  target,
                  relation,
                  metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                edge_rows,
            )

    def invalidate_source(self, *, namespace: str, source_id: str) -> None:
        stored_namespace = safe_key_part(namespace)
        stored_source_id = safe_key_part(source_id)
        with self._connect() as connection:
            ensure_schema(connection)
            for table in ("graph_edges", "graph_nodes", "graph_snapshots"):
                connection.execute(
                    f"DELETE FROM {table} WHERE namespace = ? AND source_id = ?",
                    (stored_namespace, stored_source_id),
                )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()


def create_graph_store(
    backend: GraphStoreBackend, *, path: Path | str | None = None
) -> GraphStore | None:
    if backend == "none":
        if path is not None:
            raise ValueError("--graph-store-path requires --graph-store=sqlite")
        return None
    if backend == "sqlite":
        if path is None:
            raise ValueError("--graph-store-path is required when --graph-store=sqlite")
        try:
            return SqliteGraphStore(path)
        except (OSError, sqlite3.Error) as exc:
            raise ValueError("unable to open graph store database") from exc
    raise ValueError("graph store backend must be 'none' or 'sqlite'")


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS graph_snapshots (
          schema_version TEXT NOT NULL,
          namespace TEXT NOT NULL,
          source_id TEXT NOT NULL,
          bundle_id TEXT NOT NULL,
          projection_signature TEXT NOT NULL,
          visibility_scope TEXT NOT NULL,
          created_at REAL NOT NULL,
          node_count INTEGER NOT NULL,
          edge_count INTEGER NOT NULL,
          payload_digest TEXT NOT NULL DEFAULT '',
          PRIMARY KEY (
            schema_version,
            namespace,
            source_id,
            bundle_id,
            projection_signature,
            visibility_scope
          )
        );

        CREATE TABLE IF NOT EXISTS graph_nodes (
          schema_version TEXT NOT NULL,
          namespace TEXT NOT NULL,
          source_id TEXT NOT NULL,
          bundle_id TEXT NOT NULL,
          projection_signature TEXT NOT NULL,
          visibility_scope TEXT NOT NULL,
          position INTEGER NOT NULL,
          node_id TEXT NOT NULL,
          label TEXT NOT NULL,
          kind TEXT NOT NULL,
          path TEXT NOT NULL,
          metadata_json TEXT NOT NULL,
          PRIMARY KEY (
            schema_version,
            namespace,
            source_id,
            bundle_id,
            projection_signature,
            visibility_scope,
            position
          )
        );

        CREATE INDEX IF NOT EXISTS graph_nodes_lookup
          ON graph_nodes (
            schema_version,
            namespace,
            source_id,
            bundle_id,
            projection_signature,
            visibility_scope,
            node_id
          );

        CREATE TABLE IF NOT EXISTS graph_edges (
          schema_version TEXT NOT NULL,
          namespace TEXT NOT NULL,
          source_id TEXT NOT NULL,
          bundle_id TEXT NOT NULL,
          projection_signature TEXT NOT NULL,
          visibility_scope TEXT NOT NULL,
          position INTEGER NOT NULL,
          source TEXT NOT NULL,
          target TEXT NOT NULL,
          relation TEXT NOT NULL,
          metadata_json TEXT NOT NULL,
          PRIMARY KEY (
            schema_version,
            namespace,
            source_id,
            bundle_id,
            projection_signature,
            visibility_scope,
            position
          )
        );

        CREATE INDEX IF NOT EXISTS graph_edges_lookup
          ON graph_edges (
            schema_version,
            namespace,
            source_id,
            bundle_id,
            projection_signature,
            visibility_scope,
            source,
            target
          );
        """
    )
    snapshot_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(graph_snapshots)").fetchall()
    }
    if "payload_digest" not in snapshot_columns:
        connection.execute(
            "ALTER TABLE graph_snapshots ADD COLUMN payload_digest TEXT NOT NULL DEFAULT ''"
        )


def delete_record(connection: sqlite3.Connection, key: GraphStoreKey) -> None:
    stored_key = sqlite_key_values(key)
    for table in ("graph_edges", "graph_nodes", "graph_snapshots"):
        connection.execute(
            f"""
            DELETE FROM {table}
            WHERE schema_version = ?
              AND namespace = ?
              AND source_id = ?
              AND bundle_id = ?
              AND projection_signature = ?
              AND visibility_scope = ?
            """,
            stored_key,
        )


def graph_record_for_view(
    key: GraphStoreKey, nodes: list[GraphNode], edges: list[GraphEdge]
) -> GraphRecord:
    return GraphRecord(
        key=key,
        nodes=[node.model_copy(deep=True) for node in nodes],
        edges=[edge.model_copy(deep=True) for edge in edges],
    )


def graph_record_digest(nodes: list[GraphNode], edges: list[GraphEdge]) -> str:
    payload = {
        "nodes": graph_nodes_payload(nodes),
        "edges": graph_edges_payload(edges),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def graph_nodes_payload(nodes: list[GraphNode]) -> list[dict[str, Any]]:
    return [node.model_dump(mode="json") for node in nodes]


def graph_edges_payload(edges: list[GraphEdge]) -> list[dict[str, Any]]:
    return [edge.model_dump(mode="json") for edge in edges]


def sqlite_key_values(key: GraphStoreKey) -> tuple[str, str, str, str, str, str]:
    return (
        safe_key_part(key.schema_version),
        safe_key_part(key.namespace),
        safe_key_part(key.source_id),
        safe_key_part(key.bundle_id),
        safe_key_part(key.projection_signature),
        safe_key_part(key.visibility_scope),
    )


def safe_key_part(value: str) -> str:
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    if not candidate:
        candidate = "empty"
    if candidate == value:
        return candidate
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{candidate[:80]}-{digest}"


def safe_graph_store_error(_exc: Exception) -> str:
    return "backend operation failed"
