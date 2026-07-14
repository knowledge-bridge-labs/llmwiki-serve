from __future__ import annotations

import os
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from llmwiki_serve.projection_store import RedisProjectionStore
from llmwiki_serve.service import LlmWikiService

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WIKI_ROOT = REPO_ROOT / "examples" / "sample-wiki"
REDIS_URL_ENV = "LLMWIKI_REDIS_URL"
ROOTS_ENV = "LLMWIKI_REDIS_E2E_ROOTS"

pytestmark = pytest.mark.skipif(
    not os.environ.get(REDIS_URL_ENV),
    reason=f"{REDIS_URL_ENV} is not set",
)


def configured_wiki_roots() -> tuple[Path, ...]:
    value = os.environ.get(ROOTS_ENV, "")
    if not value.strip():
        return (DEFAULT_WIKI_ROOT,)
    roots: list[Path] = []
    for item in value.split(";"):
        raw_root = item.strip()
        if not raw_root:
            continue
        root = Path(raw_root)
        roots.append(root if root.is_absolute() else REPO_ROOT / root)
    return tuple(roots) or (DEFAULT_WIKI_ROOT,)


def root_id(root: Path) -> str:
    return root.name or root.as_posix()


@pytest.mark.parametrize("wiki_root", configured_wiki_roots(), ids=root_id)
def test_redis_projection_store_hydrates_fresh_service_surfaces(
    wiki_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert wiki_root.exists(), f"Configured wiki root does not exist: {wiki_root}"

    redis_url = os.environ[REDIS_URL_ENV]
    redis_client = live_redis_client(redis_url)
    namespace = f"pytest-redis-e2e-{uuid.uuid4().hex}"

    try:
        first_service = LlmWikiService(
            wiki_root,
            projection_store=RedisProjectionStore(
                url=redis_url,
                failure_policy="fail-fast",
            ),
            cache_namespace=namespace,
        )

        first_payloads = service_payloads(first_service)
        namespace_keys = redis_namespace_keys(redis_client, namespace)

        assert namespace_keys
        assert any(":projections:" in key for key in namespace_keys)
        assert any(":sources:" in key for key in namespace_keys)
        assert all(key.startswith(f"llmwiki:{namespace}:") for key in namespace_keys)
        assert_no_root_path_in_namespace_values(redis_client, namespace_keys, wiki_root)

        import llmwiki_serve.service as service_module

        def fail_wiki_builder(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("fresh service should hydrate projection from Redis")

        monkeypatch.setattr(service_module, "load_wiki", fail_wiki_builder)
        monkeypatch.setattr(service_module, "project_wiki", fail_wiki_builder)

        fresh_service = LlmWikiService(
            wiki_root,
            projection_store=RedisProjectionStore(
                url=redis_url,
                failure_policy="fail-fast",
            ),
            cache_namespace=namespace,
        )

        assert service_payloads(fresh_service) == first_payloads
    finally:
        clean_redis_namespace(redis_client, namespace)


def service_payloads(service: LlmWikiService) -> dict[str, Any]:
    manifest = service.manifest().model_dump(mode="json")
    query = service.context("", limit=5).model_dump(mode="json")
    search = service.search("", limit=5)
    assert manifest["page_count"] > 0
    assert query["evidence"]
    assert search

    read_page_id = search[0]["page_id"]
    read = service.read(read_page_id)
    assert read.get("found") is not False

    graph = service.graph(limit=500)
    assert graph["nodes"]

    return {
        "manifest": manifest,
        "query": query,
        "search": search,
        "read": read,
        "graph": graph,
    }


def live_redis_client(redis_url: str) -> Any:
    redis_module = pytest.importorskip(
        "redis",
        reason='Redis integration tests require the "redis" optional dependency',
    )
    client = redis_module.Redis.from_url(redis_url, decode_responses=True)
    try:
        client.ping()
    except Exception as exc:
        pytest.fail(f"{REDIS_URL_ENV} is set, but Redis is not reachable: {exc!r}")
    return client


def redis_namespace_keys(redis_client: Any, namespace: str) -> list[str]:
    return sorted(redis_client.scan_iter(match=f"llmwiki:{namespace}:*"))


def assert_no_root_path_in_namespace_values(
    redis_client: Any,
    namespace_keys: Iterable[str],
    wiki_root: Path,
) -> None:
    root_text = str(wiki_root.resolve())
    for key in namespace_keys:
        value = redis_client.get(key)
        if isinstance(value, str):
            assert root_text not in value


def clean_redis_namespace(redis_client: Any, namespace: str) -> None:
    keys = redis_namespace_keys(redis_client, namespace)
    if keys:
        redis_client.delete(*keys)
