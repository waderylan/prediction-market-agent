"""Minimal structured logging with defensive field sanitization."""

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

_SENSITIVE_FRAGMENTS = ("api_key", "authorization", "cookie", "password", "secret", "token")
_PROMPT_FIELDS = ("prompt", "query")


def _sanitize(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(fragment in lowered for fragment in _SENSITIVE_FRAGMENTS):
        return "[REDACTED]"
    if any(fragment in lowered for fragment in _PROMPT_FIELDS):
        return f"[OMITTED length={len(str(value))}]"
    if isinstance(value, Mapping):
        return {
            str(child_key): _sanitize(str(child_key), child)
            for child_key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(key, item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class JsonFormatter(logging.Formatter):
    """Render one bounded JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "safe_fields", {})
        if isinstance(fields, Mapping):
            payload["fields"] = {
                str(key): _sanitize(str(key), value) for key, value in fields.items()
            }
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def configure_logging(level: str = "INFO") -> None:
    """Configure the process root logger for JSON output."""

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    """Log an event while keeping secrets and full user prompts out of output."""

    logger.info(event, extra={"safe_fields": fields})
