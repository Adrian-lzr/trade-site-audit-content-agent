"""Small, dependency-free request context and redacted event logging helpers."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import time
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from starlette.requests import Request
from starlette.responses import Response


REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID = contextvars.ContextVar("trade_visibility_request_id", default=None)
_MAX_REQUEST_ID_LENGTH = 120
_SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "raw_response",
    "answer_text",
    "content",
    "html",
    "body",
    "value",
}
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[^\s,;]+")
_KEY_VALUE_PATTERN = re.compile(r"(?i)((?:api[_-]?key|password|secret|token)\s*[:=]\s*)[^\s,;]+")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")


def request_id(value: str | None = None) -> str:
    """Return a safe request ID, preserving a valid caller correlation ID."""

    candidate = (value or "").strip()
    if not _REQUEST_ID_PATTERN.fullmatch(candidate[:_MAX_REQUEST_ID_LENGTH]):
        return uuid4().hex
    return candidate


def current_request_id() -> str | None:
    return _REQUEST_ID.get()


def redact(value: Any, *, key: str | None = None) -> Any:
    """Redact secrets and large evidence fields before they reach logs."""

    if key and any(marker in key.casefold() for marker in _SENSITIVE_KEYS):
        return "[redacted]"
    if isinstance(value, Mapping):
        return {str(item_key): redact(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = _BEARER_PATTERN.sub(r"\1[redacted]", value)
        value = _KEY_VALUE_PATTERN.sub(r"\1[redacted]", value)
        return value[:2000] + ("..." if len(value) > 2000 else "")
    return value


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Emit one JSON event with request correlation and redacted fields."""

    payload = {"event": event, "request_id": current_request_id(), **redact(fields)}
    logger.info(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


async def request_context_middleware(request: Request, call_next) -> Response:
    """Attach a request ID and log bounded request metadata and duration."""

    import logging as _logging

    value = request_id(request.headers.get(REQUEST_ID_HEADER))
    token = _REQUEST_ID.set(value)
    started = time.perf_counter()
    response: Response | None = None
    try:
        response = await call_next(request)
        return response
    except Exception as exc:
        log_event(
            _logging.getLogger("trade_visibility.http"),
            "http_request_error",
            method=request.method,
            path=request.url.path,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
            error_type=type(exc).__name__,
        )
        raise
    finally:
        if response is not None:
            response.headers[REQUEST_ID_HEADER] = value
            log_event(
                _logging.getLogger("trade_visibility.http"),
                "http_request_complete",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )
        _REQUEST_ID.reset(token)
