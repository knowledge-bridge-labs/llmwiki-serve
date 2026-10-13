"""Query-time git synchronization for served Markdown roots.

When enabled, the server fetches the configured remote before answering a
request (at most once per cooldown window) and fast-forwards the served
checkout. Local edits, local commits, and diverged histories stop the sync
instead of being overwritten. Concurrent requests share one in-flight sync and
wait for it only up to a bounded time; past that bound the request is answered
from the current files and marked stale while the sync finishes in the
background.

Git runs as an argument list (no shell) with a per-command timeout. Credentials
are left to the system git credential helper; nothing is stored here.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

DEFAULT_GIT_SYNC_COOLDOWN_SECONDS = 60.0
DEFAULT_GIT_SYNC_WAIT_SECONDS = 5.0
DEFAULT_GIT_SYNC_REMOTE = "origin"
DEFAULT_GIT_COMMAND_TIMEOUT_SECONDS = 60.0
GIT_ERROR_MAX_CHARS = 300
GIT_SYNC_META_KEY = "io.llmwiki/gitSync"

GitSyncState = Literal["idle", "synced", "updated", "blocked", "error"]
GitRunner = Callable[[Sequence[str], Path, float], "subprocess.CompletedProcess[str]"]

_CREDENTIAL_IN_URL = re.compile(r"(://)[^/@\s]+@")
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off", ""}


@dataclass(frozen=True)
class GitSyncConfig:
    enabled: bool = False
    cooldown_seconds: float = DEFAULT_GIT_SYNC_COOLDOWN_SECONDS
    wait_seconds: float = DEFAULT_GIT_SYNC_WAIT_SECONDS
    remote: str = DEFAULT_GIT_SYNC_REMOTE
    branch: str | None = None
    command_timeout_seconds: float = DEFAULT_GIT_COMMAND_TIMEOUT_SECONDS


class GitSyncError(RuntimeError):
    """A git sync step failed or was refused; the served files are unchanged."""

    def __init__(self, message: str, *, state: GitSyncState = "error") -> None:
        super().__init__(message)
        self.state: GitSyncState = state


def git_sync_config_from_env() -> GitSyncConfig:
    """Build a git sync config from LLMWIKI_GIT_SYNC* environment variables."""
    config = GitSyncConfig(enabled=_env_bool("LLMWIKI_GIT_SYNC"))
    cooldown = _env_float("LLMWIKI_GIT_SYNC_COOLDOWN_SECONDS")
    wait = _env_float("LLMWIKI_GIT_SYNC_WAIT_SECONDS")
    timeout = _env_float("LLMWIKI_GIT_SYNC_TIMEOUT_SECONDS")
    if cooldown is not None:
        config = replace(config, cooldown_seconds=cooldown)
    if wait is not None:
        config = replace(config, wait_seconds=wait)
    if timeout is not None:
        config = replace(config, command_timeout_seconds=timeout)
    remote = os.getenv("LLMWIKI_GIT_SYNC_REMOTE")
    if remote:
        config = replace(config, remote=remote)
    branch = os.getenv("LLMWIKI_GIT_SYNC_BRANCH")
    if branch:
        config = replace(config, branch=branch)
    return config


def validate_git_sync_config(config: GitSyncConfig) -> GitSyncConfig:
    if config.cooldown_seconds < 0:
        raise ValueError("git sync cooldown seconds must be non-negative")
    if config.wait_seconds < 0:
        raise ValueError("git sync wait seconds must be non-negative")
    if config.command_timeout_seconds <= 0:
        raise ValueError("git sync command timeout seconds must be positive")
    if not _is_safe_git_name(config.remote):
        raise ValueError("git sync remote must be a non-empty name that does not start with '-'")
    if config.branch is not None and not _is_safe_git_name(config.branch):
        raise ValueError("git sync branch must be a non-empty name that does not start with '-'")
    return config


def resolve_git_repo_root(
    root: Path,
    *,
    runner: GitRunner | None = None,
    timeout: float = DEFAULT_GIT_COMMAND_TIMEOUT_SECONDS,
) -> Path:
    """Return the top-level directory of the git work tree containing root."""
    run = runner or run_git
    try:
        result = run(["rev-parse", "--show-toplevel"], Path(root), timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"git sync requires git: {exc}") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError("git sync requires the served root to be inside a git work tree")
    return Path(result.stdout.strip())


def run_git(args: Sequence[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run git without a shell, without interactive prompts, and with a timeout."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}
    return subprocess.run(  # noqa: S603 - argument list, no shell
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


class GitSyncer:
    """Single-flight, cooldown-bounded fetch + fast-forward for one checkout."""

    def __init__(
        self,
        root: Path | str,
        config: GitSyncConfig,
        *,
        runner: GitRunner | None = None,
        clock: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] | None = None,
    ) -> None:
        self.root = Path(root)
        self.config = validate_git_sync_config(config)
        self._run = runner or run_git
        self._clock = clock or time.monotonic
        self._wall_clock = wall_clock or time.time
        self._lock = threading.Lock()
        self._in_flight: threading.Event | None = None
        self._repo_root: Path | None = None
        self._last_attempt: float | None = None
        self._last_attempt_at: float | None = None
        self._last_success_at: float | None = None
        self._last_error: str | None = None
        self._state: GitSyncState = "idle"
        self._branch: str | None = config.branch
        self._head_sha: str | None = None
        self._remote_sha: str | None = None
        self.fetch_count = 0
        self.generation = 0

    def ensure_fresh(self) -> bool:
        """Start or join a due sync, wait up to wait_seconds, and return staleness."""
        with self._lock:
            event = self._in_flight
            if event is None and self._is_due():
                event = threading.Event()
                self._in_flight = event
                self._last_attempt = self._clock()
                threading.Thread(
                    target=self._run_sync,
                    args=(event,),
                    name="llmwiki-git-sync",
                    daemon=True,
                ).start()
        if event is None:
            return self._last_error is not None
        if not event.wait(self.config.wait_seconds):
            logger.warning(
                "[git_sync] sync still running after %.1fs; answering from current files",
                self.config.wait_seconds,
            )
            return True
        return self._last_error is not None

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Wait for an in-flight sync to finish (used by tests and shutdown paths)."""
        event = self._in_flight
        return True if event is None else event.wait(timeout)

    def status(self) -> dict[str, Any]:
        in_progress = self._in_flight is not None
        return {
            "enabled": True,
            "state": self._state,
            "in_progress": in_progress,
            "stale": in_progress or self._last_error is not None,
            "remote": self.config.remote,
            "branch": self._branch,
            "head_sha": self._head_sha,
            "remote_sha": self._remote_sha,
            "last_attempt_at": _iso(self._last_attempt_at),
            "last_success_at": _iso(self._last_success_at),
            "last_error": self._last_error,
            "fetch_count": self.fetch_count,
            "cooldown_seconds": self.config.cooldown_seconds,
            "wait_seconds": self.config.wait_seconds,
        }

    def _is_due(self) -> bool:
        return (
            self._last_attempt is None
            or self._clock() - self._last_attempt >= self.config.cooldown_seconds
        )

    def _run_sync(self, event: threading.Event) -> None:
        self._last_attempt_at = self._wall_clock()
        started = self._clock()
        try:
            changed = self._sync_once()
            self._last_error = None
            self._last_success_at = self._wall_clock()
            self._state = "updated" if changed else "synced"
            logger.info(
                "[git_sync] %s head=%s (%.2fs)",
                self._state,
                (self._head_sha or "")[:12],
                self._clock() - started,
            )
        except GitSyncError as exc:
            self._record_failure(str(exc), exc.state)
        except subprocess.TimeoutExpired as exc:
            self._record_failure(f"git command timed out after {exc.timeout:.0f}s", "error")
        except Exception as exc:
            logger.error("[git_sync] unexpected failure: %s", exc, exc_info=True)
            self._record_failure(f"unexpected git sync failure: {type(exc).__name__}", "error")
        finally:
            with self._lock:
                self._in_flight = None
            event.set()

    def _record_failure(self, message: str, state: GitSyncState) -> None:
        self._last_error = sanitize_git_message(message)
        self._state = state
        logger.warning("[git_sync] %s; keeping current files: %s", state, self._last_error)

    def _sync_once(self) -> bool:
        repo = self._repo_root or resolve_git_repo_root(
            self.root,
            runner=self._run,
            timeout=self.config.command_timeout_seconds,
        )
        self._repo_root = repo
        current_branch = self._git_text(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"])
        if self.config.branch is not None and current_branch != self.config.branch:
            raise GitSyncError(
                f"checked-out branch {current_branch!r} differs from configured "
                f"{self.config.branch!r}; not switching branches",
                state="blocked",
            )
        self._branch = current_branch
        remote_branch = self._upstream_branch(repo, current_branch)
        remote_ref = f"refs/remotes/{self.config.remote}/{remote_branch}"
        self.fetch_count += 1
        self._git_text(
            repo,
            [
                "fetch",
                "--quiet",
                "--no-write-fetch-head",
                self.config.remote,
                f"+refs/heads/{remote_branch}:{remote_ref}",
            ],
        )
        remote_sha = self._git_text(repo, ["rev-parse", "--verify", f"{remote_ref}^{{commit}}"])
        head_sha = self._git_text(repo, ["rev-parse", "--verify", "HEAD^{commit}"])
        self._remote_sha = remote_sha
        self._head_sha = head_sha
        if head_sha == remote_sha:
            return False
        if self._git_text(repo, ["status", "--porcelain", "--untracked-files=no"]):
            raise GitSyncError(
                "local tracked changes present; fast-forward skipped", state="blocked"
            )
        if not self._git_ok(repo, ["merge-base", "--is-ancestor", head_sha, remote_sha]):
            if self._git_ok(repo, ["merge-base", "--is-ancestor", remote_sha, head_sha]):
                reason = "local commits ahead of remote"
            else:
                reason = "local branch diverged from remote"
            raise GitSyncError(f"{reason}; fast-forward skipped", state="blocked")
        self._git_text(repo, ["merge", "--ff-only", "--quiet", remote_sha])
        self._head_sha = self._git_text(repo, ["rev-parse", "--verify", "HEAD^{commit}"])
        self.generation += 1
        return True

    def _upstream_branch(self, repo: Path, current_branch: str) -> str:
        merge_ref = self._run(
            ["config", "--get", f"branch.{current_branch}.merge"],
            repo,
            self.config.command_timeout_seconds,
        )
        value = merge_ref.stdout.strip() if merge_ref.returncode == 0 else ""
        prefix = "refs/heads/"
        return value[len(prefix) :] if value.startswith(prefix) else current_branch

    def _git_text(self, repo: Path, args: list[str]) -> str:
        result = self._run(args, repo, self.config.command_timeout_seconds)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise GitSyncError(f"git {args[0]} failed: {detail}")
        return result.stdout.strip()

    def _git_ok(self, repo: Path, args: list[str]) -> bool:
        return self._run(args, repo, self.config.command_timeout_seconds).returncode == 0


def sanitize_git_message(message: str) -> str:
    """Redact URL credentials and clip git output before exposing it."""
    cleaned = _CREDENTIAL_IN_URL.sub(r"\1***@", " ".join(message.split()))
    if len(cleaned) > GIT_ERROR_MAX_CHARS:
        return cleaned[: GIT_ERROR_MAX_CHARS - 3] + "..."
    return cleaned


def disabled_git_sync_status() -> dict[str, Any]:
    return {"enabled": False}


def _iso(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=UTC).isoformat(timespec="seconds")


def _is_safe_git_name(value: str) -> bool:
    return bool(value) and not value.startswith("-") and not any(ch.isspace() for ch in value)


def _env_bool(name: str) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be a boolean (1/0/true/false)")


def _env_float(name: str) -> float | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
