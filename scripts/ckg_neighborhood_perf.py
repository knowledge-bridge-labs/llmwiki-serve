from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from llmwiki_serve.service import LlmWikiService


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare full graph payloads with bounded graph-neighborhood lookup."
    )
    parser.add_argument("--pages", type=int, default=500)
    parser.add_argument("--edges-per-page", type=int, default=4)
    parser.add_argument("--iterations", type=int, default=25)
    parser.add_argument(
        "--refresh-interval-seconds",
        type=float,
        default=60.0,
        help=(
            "Projection freshness interval for the synthetic service. Use 0 to "
            "measure strict per-request freshness checks."
        ),
    )
    parser.add_argument(
        "--producer-manifest",
        action="store_true",
        help="Use a small producer manifest marker for freshness checks.",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="llmwiki-ckg-perf-") as temp_dir:
        root = Path(temp_dir) / "wiki"
        build_wiki(root, pages=max(2, args.pages), edges_per_page=max(1, args.edges_per_page))
        producer_manifest_path = (
            ".llmwiki-producer-manifest.json" if args.producer_manifest else None
        )
        service = LlmWikiService(
            root,
            refresh_interval_seconds=args.refresh_interval_seconds,
            producer_manifest_path=producer_manifest_path,
        )
        service.index()

        graph_limit = max(args.pages * 5, 500)
        measurements = {
            "config": {
                "pages": args.pages,
                "edges_per_page": args.edges_per_page,
                "refresh_interval_seconds": args.refresh_interval_seconds,
                "producer_manifest": args.producer_manifest,
            },
            "context": measure(
                lambda: service.context("dependency marker p0000", limit=8).model_dump(),
                iterations=args.iterations,
            ),
            "full_graph": measure(
                lambda: service.graph(limit=graph_limit),
                iterations=args.iterations,
            ),
            "neighborhood": measure(
                lambda: service.graph_neighbors(
                    seeds=["p0000"],
                    depth=2,
                    direction="out",
                    relations=["requires", "implements"],
                    limit=80,
                ).model_dump(),
                iterations=args.iterations,
            ),
        }

    print(json.dumps(measurements, indent=2, sort_keys=True))
    return 0


def build_wiki(root: Path, *, pages: int, edges_per_page: int) -> None:
    root.mkdir(parents=True)
    write_page(
        root / "hot.md",
        "Current Focus",
        "dependency marker p0000 starts here and points agents toward the index.",
    )
    write_page(
        root / "index.md",
        "Synthetic CKG Perf Wiki",
        "dependency marker p0000 documents a synthetic graph for performance checks.",
        frontmatter="wiki_title: Synthetic CKG Perf Wiki\n",
    )
    for index in range(pages):
        page_id = f"p{index:04d}"
        links = " ".join(
            f"[[p{(index + offset + 1) % pages:04d}]]"
            for offset in range(min(edges_per_page, pages - 1))
        )
        write_page(
            root / f"{page_id}.md",
            f"Page {page_id}",
            f"dependency marker {page_id} {links}",
        )

    graph_dir = root / "graph"
    graph_dir.mkdir()
    sidecar_edges = []
    for index in range(pages):
        for offset in range(edges_per_page):
            target = (index + offset + 1) % pages
            sidecar_edges.append(
                {
                    "from": f"p{index:04d}",
                    "to": f"p{target:04d}",
                    "type": "requires" if offset % 2 == 0 else "implements",
                    "confidence": 0.9,
                }
            )
    (graph_dir / "graph.json").write_text(
        json.dumps({"edges": sidecar_edges}),
        encoding="utf-8",
    )
    (root / ".llmwiki-producer-manifest.json").write_text(
        json.dumps(
            {
                "schema": "llmwiki-producer-manifest/marker-v0",
                "page_count": pages + 2,
                "edge_count": len(sidecar_edges),
                "build": 1,
            }
        ),
        encoding="utf-8",
    )


def write_page(path: Path, title: str, body: str, *, frontmatter: str = "") -> None:
    path.write_text(
        f"---\nreview_state: approved\n{frontmatter}---\n# {title}\n\n{body}\n",
        encoding="utf-8",
    )


def measure(call: Callable[[], dict[str, Any]], *, iterations: int) -> dict[str, Any]:
    timings: list[float] = []
    last_payload: dict[str, Any] = {}
    for _index in range(iterations):
        started = time.perf_counter()
        last_payload = call()
        timings.append((time.perf_counter() - started) * 1000)
    return {
        "iterations": iterations,
        "median_ms": round(statistics.median(timings), 3),
        "p95_ms": round(percentile(timings, 95), 3),
        "json_bytes": len(json.dumps(last_payload, ensure_ascii=False)),
        "node_count": len(
            last_payload.get("nodes") or last_payload.get("graph", {}).get("nodes", [])
        ),
        "edge_count": len(
            last_payload.get("edges") or last_payload.get("graph", {}).get("edges", [])
        ),
    }


def percentile(values: list[float], percentile_value: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((percentile_value / 100) * (len(ordered) - 1)))
    return ordered[index]


if __name__ == "__main__":
    raise SystemExit(main())
