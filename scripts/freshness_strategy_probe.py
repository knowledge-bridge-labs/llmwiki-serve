from __future__ import annotations

import argparse
import importlib.metadata
import json
import queue
import shutil
import statistics
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from llmwiki_serve.service import LlmWikiService


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare freshness invalidation candidates for llmwiki-serve."
    )
    parser.add_argument("--pages", type=int, default=1200)
    parser.add_argument("--events", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    args = parser.parse_args()

    results: dict[str, Any] = {
        "config": {
            "pages": args.pages,
            "events": args.events,
            "timeout_seconds": args.timeout_seconds,
        },
        "watchfiles": run_watchfiles_probe(args.pages, args.events, args.timeout_seconds),
        "watchdog": run_watchdog_probe(args.pages, args.events, args.timeout_seconds),
        "producer_manifest_marker": run_producer_manifest_probe(args.pages),
        "watchman": run_watchman_probe(),
    }
    print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))


def run_watchfiles_probe(pages: int, events: int, timeout_seconds: float) -> dict[str, Any]:
    try:
        from watchfiles import watch
    except ImportError as exc:
        return {"available": False, "error": repr(exc)}

    with tempfile.TemporaryDirectory(prefix="llmwiki-watchfiles-") as tmp:
        root = Path(tmp)
        write_synthetic_wiki(root, pages)
        changes: queue.Queue[Any] = queue.Queue()
        stop_event = threading.Event()

        def worker() -> None:
            try:
                for batch in watch(
                    root,
                    watch_filter=None,
                    debounce=50,
                    step=10,
                    stop_event=stop_event,
                    rust_timeout=500,
                    recursive=True,
                ):
                    changes.put((time.perf_counter(), sorted(str(path) for _, path in batch)))
                    if stop_event.is_set():
                        break
            except Exception as exc:  # pragma: no cover - diagnostic probe
                changes.put(("error", repr(exc)))

        started = time.perf_counter()
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        time.sleep(0.25)
        startup_ms = elapsed_ms(started)

        latencies = []
        missed = 0
        for index in range(events):
            path = root / f"page-{index % pages:04d}.md"
            written_at = time.perf_counter()
            path.write_text(page_text(index, f"watchfiles-token-{index}"), encoding="utf-8")
            latency = wait_for_path(changes, path, written_at, timeout_seconds)
            if latency is None:
                missed += 1
            else:
                latencies.append(latency)

        stop_event.set()
        thread.join(timeout=2.0)
        return summarize_event_probe("watchfiles", latencies, missed, startup_ms)


def run_watchdog_probe(pages: int, events: int, timeout_seconds: float) -> dict[str, Any]:
    try:
        from watchdog.events import FileSystemEvent, FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError as exc:
        return {"available": False, "error": repr(exc)}

    with tempfile.TemporaryDirectory(prefix="llmwiki-watchdog-") as tmp:
        root = Path(tmp)
        write_synthetic_wiki(root, pages)
        changes: queue.Queue[Any] = queue.Queue()

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event: FileSystemEvent) -> None:
                changes.put((time.perf_counter(), [event.src_path]))

        observer = Observer()
        started = time.perf_counter()
        observer.schedule(Handler(), str(root), recursive=True)
        observer.start()
        time.sleep(0.25)
        startup_ms = elapsed_ms(started)

        latencies = []
        missed = 0
        try:
            for index in range(events):
                path = root / f"page-{index % pages:04d}.md"
                written_at = time.perf_counter()
                path.write_text(page_text(index, f"watchdog-token-{index}"), encoding="utf-8")
                latency = wait_for_path(changes, path, written_at, timeout_seconds)
                if latency is None:
                    missed += 1
                else:
                    latencies.append(latency)
        finally:
            observer.stop()
            observer.join(timeout=2.0)

        return summarize_event_probe("watchdog", latencies, missed, startup_ms)


def run_producer_manifest_probe(pages: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="llmwiki-producer-manifest-") as tmp:
        root = Path(tmp)
        write_synthetic_wiki(root, pages)
        manifest = root / ".llmwiki-producer-manifest.json"
        manifest.write_text(json.dumps({"build": 1}) + "\n", encoding="utf-8")

        strict_service = LlmWikiService(root)
        strict_service.read("index")
        strict_latencies = [time_call(lambda: strict_service.read("index")) for _ in range(5)]

        service = LlmWikiService(root, producer_manifest_path=manifest.name)
        service.read("index")
        no_change_latencies = [time_call(lambda: service.read("index")) for _ in range(25)]

        stale_path = root / "index.md"
        stale_path.write_text(page_text(0, "producer-stale-token"), encoding="utf-8")
        stale_result = service.read("index")
        stale_until_marker_changes = "producer-stale-token" not in stale_result["text"]

        refresh_latencies = []
        for index in range(5):
            token = f"producer-refresh-token-{index}"
            stale_path.write_text(page_text(index, token), encoding="utf-8")
            manifest.write_text(json.dumps({"build": index + 2}) + "\n", encoding="utf-8")
            refresh_latencies.append(
                time_call(lambda token=token: assert_read_token(service, token))
            )

        return {
            "available": True,
            "kind": "producer contract, stdlib file marker",
            "strict_no_change_read_ms": summarize_latencies(strict_latencies),
            "manifest_no_change_read_ms": summarize_latencies(no_change_latencies),
            "manifest_refresh_after_marker_ms": summarize_latencies(refresh_latencies),
            "stale_until_marker_changes": stale_until_marker_changes,
        }


def run_watchman_probe() -> dict[str, Any]:
    executable = shutil.which("watchman")
    if executable is None:
        return {
            "available": False,
            "reason": "watchman executable was not found on PATH",
        }
    try:
        completed = subprocess.run(
            [executable, "version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"available": False, "executable": executable, "error": repr(exc)}
    return {
        "available": completed.returncode == 0,
        "executable": executable,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def write_synthetic_wiki(root: Path, pages: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.md").write_text(
        """---
review_state: approved
---
# Synthetic Wiki

Synthetic index.
""",
        encoding="utf-8",
    )
    for index in range(pages):
        path = root / f"page-{index:04d}.md"
        path.write_text(page_text(index, f"seed-token-{index}"), encoding="utf-8")


def page_text(index: int, token: str) -> str:
    return f"""---
review_state: approved
---
# Page {index}

{token}
"""


def wait_for_path(
    changes: queue.Queue[Any], target: Path, written_at: float, timeout_seconds: float
) -> float | None:
    deadline = time.perf_counter() + timeout_seconds
    target_text = str(target)
    while time.perf_counter() < deadline:
        try:
            observed_at, paths = changes.get(timeout=max(0.01, deadline - time.perf_counter()))
        except queue.Empty:
            return None
        if observed_at == "error":
            raise RuntimeError(paths)
        if any(Path(path) == target or str(path) == target_text for path in paths):
            return (observed_at - written_at) * 1000
    return None


def summarize_event_probe(
    name: str, latencies: list[float], missed: int, startup_ms: float
) -> dict[str, Any]:
    result = {
        "available": True,
        "name": name,
        "package_version": package_version(name),
        "startup_ms": round(startup_ms, 3),
        "events_seen": len(latencies),
        "missed_events": missed,
    }
    result.update(summarize_latencies(latencies))
    return result


def summarize_latencies(latencies: list[float]) -> dict[str, Any]:
    if not latencies:
        return {"median_ms": None, "p95_ms": None, "min_ms": None, "max_ms": None}
    sorted_latencies = sorted(latencies)
    p95 = sorted_latencies[int((len(sorted_latencies) - 1) * 0.95)]
    return {
        "median_ms": round(statistics.median(sorted_latencies), 3),
        "p95_ms": round(p95, 3),
        "min_ms": round(sorted_latencies[0], 3),
        "max_ms": round(sorted_latencies[-1], 3),
    }


def time_call(callback: Any) -> float:
    started = time.perf_counter()
    callback()
    return elapsed_ms(started)


def elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def assert_read_token(service: LlmWikiService, token: str) -> None:
    result = service.read("index")
    if token not in result["text"]:
        raise AssertionError(f"missing token {token}")


def package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


if __name__ == "__main__":
    main()
