from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from core import redact_pii


REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID = contextvars.ContextVar("request_id", default="-")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SENSITIVE_KEY_PATTERN = re.compile(r"(?:authorization|cookie|password|secret|token|api[_-]?key|private[_-]?key)", re.IGNORECASE)
LOGGER = logging.getLogger("leadlens")


def request_id_from_header(value: str | None) -> str:
    if value and _REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return str(uuid.uuid4())


def bind_request_id(request_id: str) -> contextvars.Token[str]:
    return _REQUEST_ID.set(request_id)


def reset_request_id(token: contextvars.Token[str]) -> None:
    _REQUEST_ID.reset(token)


def current_request_id() -> str:
    return _REQUEST_ID.get()


def _safe_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): "[REDACTED]" if _SENSITIVE_KEY_PATTERN.search(str(key)) else _safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item) for item in value]
    if isinstance(value, str):
        return redact_pii(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "request_id": getattr(record, "request_id", current_request_id()),
        }
        payload.update(_safe_value(getattr(record, "fields", {})))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def configure_logging() -> None:
    if not any(getattr(handler, "_leadlens_json", False) for handler in LOGGER.handlers):
        handler = logging.StreamHandler()
        handler._leadlens_json = True
        handler.setFormatter(JsonFormatter())
        LOGGER.addHandler(handler)
    LOGGER.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    LOGGER.propagate = True


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    LOGGER.log(level, event, extra={"request_id": current_request_id(), "fields": fields})


def capture_exception(error: BaseException) -> None:
    try:
        import sentry_sdk
    except ImportError:
        return
    sentry_sdk.capture_exception(error)


def _sentry_before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    return _safe_value(event)


def initialize_error_tracking() -> None:
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        LOGGER.warning("error_tracking.unavailable", extra={"request_id": current_request_id(), "fields": {"provider": "sentry"}})
        return
    sentry_sdk.init(
        dsn=dsn,
        environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        send_default_pii=False,
        before_send=_sentry_before_send,
    )


def elapsed_ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)