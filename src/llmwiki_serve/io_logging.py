from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from starlette.types import ASGIApp, Message, Receive, Scope, Send

IO_LOG_ENV_VAR = "LLMWIKI_SERVE_IO_LOG"
DEFAULT_IO_LOG_PATH = Path(".runtime-logs") / "llmwiki-serve-io.jsonl"
IO_LOG_SCHEMA = "llmwiki-serve-io-log-v1"
IO_LOG_OFF_VALUES = {"0", "false", "no", "off", "none", "disabled", "disable"}
IO_LOG_ON_VALUES = {"1", "true", "yes", "on"}
BODY_CAPTURE_PATHS = {"/query", "/mcp", "/mcp/stream", "/message:send"}
MAX_BODY_CAPTURE_BYTES = 64 * 1024
REDACTION = "[REDACTED]"
LOCAL_ROOT_REDACTION = "[REDACTED_LOCAL_ROOT]"

_SENSITIVE_KEY_RE = re.compile(
    r"authorization|cookie|token|secret|password|passwd|credential|api[-_]?key|session|jwt|bearer",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_BASIC_RE = re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=-]+")
_COOKIE_LINE_RE = re.compile(r"(?i)\b(Set-Cookie|Cookie)\s*:\s*[^\r\n]+")
_OPENAI_KEY_RE = re.compile(r"\bsk-proj-[A-Za-z0-9_-]{8,}\b|\bsk-[A-Za-z0-9_-]{8,}\b")
_GITHUB_TOKEN_RE = re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{8,}\b")
_QUERY_SECRET_RE = re.compile(
    r"(?i)((?:^|[?&])(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"token|key|secret|client[_-]?secret|password|credential|code|sig|signature)=)"
    r"[^&\s\"'<>]+"
)
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_WINDOWS_PATH_RE = re.compile(r"\b[A-Za-z]:\\[^\s\"'<>]+")
_UNC_PATH_RE = re.compile(r"\\\\[^\\\s\"'<>]+\\[^\s\"'<>]+")
_POSIX_PRIVATE_PATH_RE = re.compile(r"/(?:Users|home|var/folders|var/tmp|tmp)/[^\s\"'<>]+")
_QUOTED_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)([\"']?[A-Za-z0-9_-]*(?:token|secret|password|credential|api[-_]?key)"
    r"[A-Za-z0-9_-]*[\"']?\s*[:=]\s*[\"'])([^\"']+)([\"'])"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_-]*(?:token|secret|password|credential|api[-_]?key)"
    r"[A-Za-z0-9_-]*)(\s*[:=]\s*)([^\s,;&\"']+)"
)


def resolve_io_log_path(value: Path | str | bool | None = None) -> Path | None:
    raw_value: Path | str | bool | None = value
    if raw_value is None:
        raw_value = os.environ.get(IO_LOG_ENV_VAR)
    if raw_value is None or raw_value is True:
        return DEFAULT_IO_LOG_PATH
    if raw_value is False:
        return None
    if isinstance(raw_value, Path):
        return raw_value

    normalized = str(raw_value).strip()
    if not normalized or normalized.lower() in IO_LOG_ON_VALUES:
        return DEFAULT_IO_LOG_PATH
    if normalized.lower() in IO_LOG_OFF_VALUES:
        return None
    return Path(normalized)


class JsonlIoLogSink:
    def __init__(
        self,
        path: Path,
        *,
        local_roots: Sequence[Path | str] = (),
    ) -> None:
        self.path = path
        self.local_root_strings = local_root_strings(local_roots)
        self._lock = threading.Lock()

    def write(self, event: dict[str, Any]) -> None:
        redacted = redact_value(event, self.local_root_strings)
        line = json.dumps(redacted, ensure_ascii=False, sort_keys=True)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self.path.open("a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")
        except OSError:
            return


class IoLoggingMiddleware:
    def __init__(self, app: ASGIApp, sink: JsonlIoLogSink) -> None:
        self.app = app
        self.sink = sink

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "")
        path = str(scope.get("path") or "")
        query_string = decode_query_string(scope.get("query_string"))
        request_headers = decode_headers(
            cast(list[tuple[bytes, bytes]], scope.get("headers") or [])
        )
        request_capture_enabled = path in BODY_CAPTURE_PATHS
        request_captured = bytearray()
        request_size = 0
        response_captured = bytearray()
        response_size = 0
        response_headers: dict[str, str] = {}
        status_code = 500
        request_id = uuid.uuid4().hex
        started = time.perf_counter()
        logged = False

        async def logging_receive() -> Message:
            nonlocal request_size
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                if isinstance(body, bytes):
                    request_size += len(body)
                    if request_capture_enabled:
                        append_limited(request_captured, body)
            return message

        async def logging_send(message: Message) -> None:
            nonlocal logged, response_headers, response_size, status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status") or 0)
                response_headers = decode_headers(
                    cast(list[tuple[bytes, bytes]], message.get("headers") or [])
                )
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if isinstance(body, bytes):
                    response_size += len(body)
                    append_limited(response_captured, body)

            await send(message)

            if message["type"] == "http.response.body" and not message.get("more_body", False):
                logged = True
                self._write_event(
                    request_id=request_id,
                    method=method,
                    path=path,
                    query_string=query_string,
                    status_code=status_code,
                    duration_ms=duration_ms(started),
                    request_headers=request_headers,
                    request_body=bytes(request_captured),
                    request_size=request_size,
                    request_capture_enabled=request_capture_enabled,
                    response_headers=response_headers,
                    response_body=bytes(response_captured),
                    response_size=response_size,
                )

        try:
            await self.app(scope, logging_receive, logging_send)
        except Exception:
            if not logged:
                self._write_event(
                    request_id=request_id,
                    method=method,
                    path=path,
                    query_string=query_string,
                    status_code=500,
                    duration_ms=duration_ms(started),
                    request_headers=request_headers,
                    request_body=bytes(request_captured),
                    request_size=request_size,
                    request_capture_enabled=request_capture_enabled,
                    response_headers=response_headers,
                    response_body=b"",
                    response_size=0,
                    error="unhandled_exception",
                )
            raise

    def _write_event(
        self,
        *,
        request_id: str,
        method: str,
        path: str,
        query_string: str,
        status_code: int,
        duration_ms: float,
        request_headers: dict[str, str],
        request_body: bytes,
        request_size: int,
        request_capture_enabled: bool,
        response_headers: dict[str, str],
        response_body: bytes,
        response_size: int,
        error: str | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "schema": IO_LOG_SCHEMA,
            "event": "serve_io",
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "request_id": request_id,
            "method": method,
            "path": path,
            "query_string": query_string,
            "status": status_code,
            "duration_ms": duration_ms,
            "request": {
                "headers": redact_headers(request_headers, self.sink.local_root_strings),
                "body": body_summary(
                    request_body,
                    total_size=request_size,
                    headers=request_headers,
                    captured=request_capture_enabled,
                    local_roots=self.sink.local_root_strings,
                ),
            },
            "response": {
                "headers": redact_headers(response_headers, self.sink.local_root_strings),
                "body": body_summary(
                    response_body,
                    total_size=response_size,
                    headers=response_headers,
                    captured=True,
                    local_roots=self.sink.local_root_strings,
                ),
            },
        }
        if error:
            event["error"] = error
        self.sink.write(event)


def append_limited(target: bytearray, chunk: bytes) -> None:
    remaining = MAX_BODY_CAPTURE_BYTES - len(target)
    if remaining > 0:
        target.extend(chunk[:remaining])


def body_summary(
    body: bytes,
    *,
    total_size: int,
    headers: dict[str, str],
    captured: bool,
    local_roots: Sequence[str],
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "captured": captured,
        "size_bytes": total_size,
        "captured_bytes": len(body),
        "truncated": total_size > len(body),
    }
    if not captured or total_size == 0:
        return summary

    content_type = header_value(headers, "content-type")
    text = decode_body_text(body)
    if is_json_content(content_type, text) and not summary["truncated"]:
        try:
            summary["json"] = redact_value(json.loads(text), local_roots)
            return summary
        except json.JSONDecodeError:
            pass

    if text:
        summary["text"] = redact_value(text, local_roots)
    return summary


def redact_headers(headers: dict[str, str], local_roots: Sequence[str]) -> dict[str, str]:
    return {
        key: REDACTION if is_sensitive_key(key) else str(redact_value(value, local_roots))
        for key, value in headers.items()
    }


def redact_value(value: Any, local_roots: Sequence[str]) -> Any:
    if isinstance(value, dict):
        return {
            str(key): REDACTION if is_sensitive_key(str(key)) else redact_value(item, local_roots)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item, local_roots) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item, local_roots) for item in value]
    if isinstance(value, str):
        return redact_string(value, local_roots)
    return value


def redact_string(value: str, local_roots: Sequence[str]) -> str:
    redacted = value
    for root in local_roots:
        if root:
            redacted = redacted.replace(root, LOCAL_ROOT_REDACTION)
    redacted = _BEARER_RE.sub(f"Bearer {REDACTION}", redacted)
    redacted = _BASIC_RE.sub(f"Basic {REDACTION}", redacted)
    redacted = _COOKIE_LINE_RE.sub(rf"\1: {REDACTION}", redacted)
    redacted = _OPENAI_KEY_RE.sub(REDACTION, redacted)
    redacted = _GITHUB_TOKEN_RE.sub(REDACTION, redacted)
    redacted = _QUERY_SECRET_RE.sub(rf"\1{REDACTION}", redacted)
    redacted = _URL_RE.sub("[REDACTED_URL]", redacted)
    redacted = _WINDOWS_PATH_RE.sub(LOCAL_ROOT_REDACTION, redacted)
    redacted = _UNC_PATH_RE.sub(LOCAL_ROOT_REDACTION, redacted)
    redacted = _POSIX_PRIVATE_PATH_RE.sub(LOCAL_ROOT_REDACTION, redacted)
    redacted = _QUOTED_SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{REDACTION}{match.group(3)}",
        redacted,
    )
    return _SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTION}",
        redacted,
    )


def local_root_strings(local_roots: Sequence[Path | str]) -> tuple[str, ...]:
    values: set[str] = set()
    for root in local_roots:
        root_path = Path(root)
        candidates = [str(root_path)]
        with suppress(OSError):
            candidates.append(str(root_path.expanduser().resolve()))
        for candidate in candidates:
            normalized = candidate.strip()
            if normalized:
                values.add(normalized)
                values.add(normalized.replace("\\", "/"))
    return tuple(sorted(values, key=len, reverse=True))


def decode_headers(raw_headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for raw_key, raw_value in raw_headers:
        key = raw_key.decode("latin-1").lower()
        value = raw_value.decode("latin-1")
        if key in headers:
            headers[key] = f"{headers[key]}, {value}"
        else:
            headers[key] = value
    return headers


def decode_query_string(raw_query_string: object) -> str:
    if isinstance(raw_query_string, bytes):
        return raw_query_string.decode("latin-1")
    return ""


def decode_body_text(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("utf-8", errors="replace")


def header_value(headers: dict[str, str], name: str) -> str:
    return headers.get(name.lower(), "")


def is_json_content(content_type: str, text: str) -> bool:
    lowered = content_type.lower()
    return "json" in lowered or text.lstrip().startswith(("{", "["))


def is_sensitive_key(key: str) -> bool:
    return _SENSITIVE_KEY_RE.search(key) is not None


def duration_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)
