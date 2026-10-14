from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from llmwiki_serve import git_sync as git_sync_module
from llmwiki_serve.api import create_app, create_mcp_stream_server
from llmwiki_serve.git_sync import (
    GIT_SYNC_META_KEY,
    GitSyncConfig,
    git_sync_config_from_env,
    sanitize_git_message,
    validate_git_sync_config,
)
from llmwiki_serve.service import LlmWikiService

# git sync is deprecated (it writes to the served root); legacy behavior stays covered.
pytestmark = pytest.mark.filterwarnings("ignore:git_sync is deprecated:DeprecationWarning")

ADDED_TOKEN = "zephyrgitsynctoken"
GIT_IDENTITY = ["-c", "user.name=llmwiki-test", "-c", "user.email=llmwiki-test@example.invalid"]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class GitFixture:
    """Bare remote + writer clone (simulates other committers) + serving clone."""

    def __init__(self, tmp_path: Path) -> None:
        self.remote = tmp_path / "remote.git"
        self.writer = tmp_path / "writer"
        self.serving = tmp_path / "serving"
        git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(self.remote))
        git(tmp_path, "clone", "--quiet", str(self.remote), str(self.writer))
        git(self.writer, "checkout", "--quiet", "-B", "main")
        write_page(self.writer / "wiki" / "index.md", "Index", "Start with [[topic]].")
        write_page(self.writer / "wiki" / "topic.md", "Topic", "Original topic text.")
        self.commit_and_push("seed wiki")
        git(tmp_path, "clone", "--quiet", str(self.remote), str(self.serving))

    @property
    def served_root(self) -> Path:
        return self.serving / "wiki"

    def commit_and_push(self, message: str) -> str:
        git(self.writer, "add", "-A")
        git(self.writer, *GIT_IDENTITY, "commit", "--quiet", "-m", message)
        git(self.writer, "push", "--quiet", "origin", "main")
        return git(self.writer, "rev-parse", "HEAD")

    def push_added_page(self) -> str:
        write_page(self.writer / "wiki" / "added.md", "Added", f"{ADDED_TOKEN} arrives by git.")
        return self.commit_and_push("add page")

    def serving_head(self) -> str:
        return git(self.serving, "rev-parse", "HEAD")


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=30
    )
    return result.stdout.strip()


def write_page(path: Path, title: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {title}\nreview_state: approved\n---\n# {title}\n\n{body}\n",
        encoding="utf-8",
    )


def search_text(service: LlmWikiService, query: str) -> str:
    return json.dumps(service.search(query, limit=5), default=str)


def sync_config(**overrides: Any) -> GitSyncConfig:
    return GitSyncConfig(enabled=True, **overrides)


class CountingGit:
    """Wraps the real git runner, counts fetches, and can hold fetch open."""

    def __init__(self, *, hold_fetch: bool = False) -> None:
        self.real = git_sync_module.run_git
        self.fetches = 0
        self.release = threading.Event()
        self.fetch_started = threading.Event()
        if not hold_fetch:
            self.release.set()
        self._lock = threading.Lock()

    def __call__(
        self, args: Sequence[str], cwd: Path, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        if args and args[0] == "fetch":
            with self._lock:
                self.fetches += 1
            self.fetch_started.set()
            assert self.release.wait(10)
        return self.real(args, cwd, timeout)


@pytest.fixture
def repo(tmp_path: Path) -> GitFixture:
    return GitFixture(tmp_path)


def test_git_sync_disabled_by_default_runs_no_git(
    repo: GitFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_git(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("git must not run when git sync is disabled")

    monkeypatch.setattr(git_sync_module, "run_git", fail_git)
    repo.push_added_page()
    for config in (None, GitSyncConfig()):
        service = LlmWikiService(repo.served_root, git_sync=config)
        assert ADDED_TOKEN not in search_text(service, ADDED_TOKEN)
        assert service.git_sync_status() is None


def test_git_sync_respects_cooldown_then_fast_forwards(repo: GitFixture) -> None:
    clock = Clock()
    service = LlmWikiService(
        repo.served_root, git_sync=sync_config(cooldown_seconds=60), clock=clock
    )
    assert ADDED_TOKEN not in search_text(service, ADDED_TOKEN)
    first = service.git_sync_status()
    assert first is not None
    assert first["fetch_count"] == 1
    assert first["state"] == "synced"
    assert first["head_sha"] == first["remote_sha"] == repo.serving_head()

    pushed_sha = repo.push_added_page()
    clock.now = 59
    assert ADDED_TOKEN not in search_text(service, ADDED_TOKEN)
    assert service.git_sync_status()["fetch_count"] == 1  # type: ignore[index]

    clock.now = 60
    assert ADDED_TOKEN in search_text(service, ADDED_TOKEN)
    status = service.git_sync_status()
    assert status is not None
    assert status["fetch_count"] == 2
    assert status["state"] == "updated"
    assert status["head_sha"] == status["remote_sha"] == pushed_sha == repo.serving_head()
    assert status["stale"] is False
    assert status["last_success_at"]


def test_git_sync_refreshes_even_with_refresh_interval(repo: GitFixture) -> None:
    clock = Clock()
    service = LlmWikiService(
        repo.served_root,
        refresh_interval_seconds=3600,
        git_sync=sync_config(cooldown_seconds=0),
        clock=clock,
    )
    assert ADDED_TOKEN not in search_text(service, ADDED_TOKEN)
    repo.push_added_page()
    clock.now = 1
    assert ADDED_TOKEN in search_text(service, ADDED_TOKEN)


def test_concurrent_requests_share_one_fetch(
    repo: GitFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CountingGit(hold_fetch=True)
    monkeypatch.setattr(git_sync_module, "run_git", runner)
    repo.push_added_page()
    service = LlmWikiService(repo.served_root, git_sync=sync_config(wait_seconds=10))
    outputs: list[str] = []

    def worker() -> None:
        outputs.append(search_text(service, ADDED_TOKEN))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    assert runner.fetch_started.wait(10)
    runner.release.set()
    for thread in threads:
        thread.join(15)
    assert runner.fetches == 1
    assert len(outputs) == 6
    assert all(ADDED_TOKEN in output for output in outputs)


def test_wait_bound_exceeded_answers_stale_then_catches_up(
    repo: GitFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CountingGit(hold_fetch=True)
    monkeypatch.setattr(git_sync_module, "run_git", runner)
    repo.push_added_page()
    service = LlmWikiService(
        repo.served_root, git_sync=sync_config(wait_seconds=0.05, cooldown_seconds=60)
    )
    assert ADDED_TOKEN not in search_text(service, ADDED_TOKEN)
    status = service.git_sync_status()
    assert status is not None
    assert status["in_progress"] is True
    assert status["stale"] is True

    runner.release.set()
    syncer = service._git_syncer
    assert syncer is not None and syncer.wait_idle(10)
    assert ADDED_TOKEN in search_text(service, ADDED_TOKEN)
    status = service.git_sync_status()
    assert status is not None
    assert status["stale"] is False
    assert runner.fetches == 1


def test_local_tracked_change_blocks_sync_and_keeps_files(repo: GitFixture) -> None:
    topic = repo.served_root / "topic.md"
    write_page(topic, "Topic", "Locally edited topic text.")
    before_head = repo.serving_head()
    repo.push_added_page()
    service = LlmWikiService(repo.served_root, git_sync=sync_config())

    assert ADDED_TOKEN not in search_text(service, ADDED_TOKEN)
    status = service.git_sync_status()
    assert status is not None
    assert status["state"] == "blocked"
    assert status["stale"] is True
    assert "local tracked changes" in status["last_error"]
    assert repo.serving_head() == before_head
    assert "Locally edited topic text." in topic.read_text(encoding="utf-8")
    assert not (repo.served_root / "added.md").exists()


def test_diverged_history_blocks_sync(repo: GitFixture) -> None:
    write_page(repo.served_root / "local.md", "Local", "Local only commit.")
    git(repo.serving, "add", "-A")
    git(repo.serving, *GIT_IDENTITY, "commit", "--quiet", "-m", "local commit")
    local_head = repo.serving_head()
    repo.push_added_page()
    service = LlmWikiService(repo.served_root, git_sync=sync_config())

    assert ADDED_TOKEN not in search_text(service, ADDED_TOKEN)
    status = service.git_sync_status()
    assert status is not None
    assert status["state"] == "blocked"
    assert "diverged" in status["last_error"]
    assert repo.serving_head() == local_head


def test_branch_mismatch_blocks_sync(repo: GitFixture) -> None:
    service = LlmWikiService(repo.served_root, git_sync=sync_config(branch="release"))
    service.search("topic")
    status = service.git_sync_status()
    assert status is not None
    assert status["state"] == "blocked"
    assert "not switching branches" in status["last_error"]


def test_http_health_diagnostics_and_mcp_meta_expose_git_sync(repo: GitFixture) -> None:
    client = TestClient(create_app(repo.served_root, git_sync=sync_config()))
    rpc = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "llmwiki_search", "arguments": {"query": "topic"}},
        },
    ).json()
    meta = rpc["result"]["_meta"][GIT_SYNC_META_KEY]
    assert meta["head_sha"] == repo.serving_head()

    health = client.get("/health").json()
    assert health["git_sync"]["enabled"] is True
    assert health["git_sync"]["head_sha"] == repo.serving_head()
    diagnostics = client.get("/diagnostics/git-sync").json()
    assert diagnostics["remote_sha"] == repo.serving_head()

    plain = TestClient(create_app(repo.served_root))
    assert "git_sync" not in plain.get("/health").json()
    assert plain.get("/diagnostics/git-sync").json()["enabled"] is False
    plain_rpc = plain.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "llmwiki_search", "arguments": {"query": "topic"}},
        },
    ).json()
    assert "_meta" not in plain_rpc["result"]


def test_streamable_mcp_tool_result_carries_git_sync_meta(repo: GitFixture) -> None:
    service = LlmWikiService(repo.served_root, git_sync=sync_config())
    server = create_mcp_stream_server(service)
    result = asyncio.run(server.call_tool("llmwiki_search", {"query": "topic"}))
    assert result.meta is not None
    assert result.meta[GIT_SYNC_META_KEY]["head_sha"] == repo.serving_head()


def test_git_sync_config_env_and_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLMWIKI_GIT_SYNC", "1")
    monkeypatch.setenv("LLMWIKI_GIT_SYNC_COOLDOWN_SECONDS", "30")
    monkeypatch.setenv("LLMWIKI_GIT_SYNC_WAIT_SECONDS", "2.5")
    monkeypatch.setenv("LLMWIKI_GIT_SYNC_REMOTE", "upstream")
    monkeypatch.setenv("LLMWIKI_GIT_SYNC_BRANCH", "main")
    config = git_sync_config_from_env()
    assert config == GitSyncConfig(
        enabled=True, cooldown_seconds=30, wait_seconds=2.5, remote="upstream", branch="main"
    )
    with pytest.raises(ValueError):
        validate_git_sync_config(GitSyncConfig(remote="--upload-pack=evil"))
    with pytest.raises(ValueError):
        validate_git_sync_config(GitSyncConfig(cooldown_seconds=-1))
    monkeypatch.setenv("LLMWIKI_GIT_SYNC", "maybe")
    with pytest.raises(ValueError):
        git_sync_config_from_env()


def test_git_error_messages_redact_url_credentials() -> None:
    message = sanitize_git_message("fatal: https://user:s3cret@git.example/repo.git denied")
    assert "s3cret" not in message
    assert "https://***@git.example" in message


def test_cli_rejects_git_sync_outside_git_work_tree(tmp_path: Path) -> None:
    from llmwiki_serve.cli import resolve_git_sync_cli_config

    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(ValueError, match="git work tree"):
        resolve_git_sync_cli_config(
            plain, enabled=True, cooldown_seconds=None, wait_seconds=None, remote=None, branch=None
        )


def test_focused_read_goes_through_git_sync(repo: GitFixture) -> None:
    clock = Clock()
    service = LlmWikiService(
        repo.served_root, git_sync=sync_config(cooldown_seconds=60), clock=clock
    )
    assert service.read("added")["found"] is False
    repo.push_added_page()
    clock.now = 60

    focused = service.read("added", query=ADDED_TOKEN, max_chars=400)

    assert focused.get("found") is not False
    assert ADDED_TOKEN in json.dumps(focused, default=str)
    status = service.git_sync_status()
    assert status is not None
    assert status["state"] == "updated"
    assert status["head_sha"] == repo.serving_head()


def test_mcp_tool_filter_still_blocks_with_git_sync_meta(repo: GitFixture) -> None:
    service = LlmWikiService(repo.served_root, git_sync=sync_config())
    server = create_mcp_stream_server(service, enabled_tools=["llmwiki_search"])

    allowed = asyncio.run(server.call_tool("llmwiki_search", {"query": "topic"}))
    assert allowed.meta is not None
    assert allowed.meta[GIT_SYNC_META_KEY]["head_sha"] == repo.serving_head()

    with pytest.raises(Exception, match="disabled"):
        asyncio.run(server.call_tool("llmwiki_graph", {}))


def test_enabled_git_sync_emits_deprecation_warning(tmp_path: Path) -> None:
    fixture = GitFixture(tmp_path)
    with pytest.warns(DeprecationWarning, match="read-only source guarantee"):
        LlmWikiService(fixture.served_root, git_sync=GitSyncConfig(enabled=True))


def test_disabled_git_sync_emits_no_deprecation_warning(tmp_path: Path) -> None:
    import warnings

    fixture = GitFixture(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        LlmWikiService(fixture.served_root, git_sync=GitSyncConfig(enabled=False))
        LlmWikiService(fixture.served_root)


def test_cli_git_sync_prints_deprecation_warning(tmp_path: Path, monkeypatch: Any) -> None:
    import uvicorn
    from typer.testing import CliRunner

    from llmwiki_serve.cli import app as cli_app

    fixture = GitFixture(tmp_path)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)
    enabled = CliRunner().invoke(
        cli_app, ["serve", str(fixture.served_root), "--io-log", "off", "--git-sync"]
    )
    disabled = CliRunner().invoke(cli_app, ["serve", str(fixture.served_root), "--io-log", "off"])

    assert enabled.exit_code == 0, enabled.output
    assert "--git-sync is deprecated" in enabled.output
    assert disabled.exit_code == 0, disabled.output
    assert "deprecated" not in disabled.output
