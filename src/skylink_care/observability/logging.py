"""Structured JSON logging with personal data and secrets redacted before anything is emitted."""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from skylink_care.guardrails.pii import redact_for_log

_SECRET_KEYS = ("password", "secret", "token", "api_key", "apikey", "authorization", "key")


def _redact(
    _logger: Any, _method: str, event: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for name in list(event):
        if any(marker in name.lower() for marker in _SECRET_KEYS):
            event[name] = "<redacted>"
        else:
            event[name] = redact_for_log(event[name])
    return event


def configure_logging(level: str = "INFO") -> None:
    """Send JSON logs to stderr. Call once at process start."""
    logging.basicConfig(stream=sys.stderr, level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            # Render the traceback first, so that redaction also covers exception text.
            structlog.processors.format_exc_info,
            _redact,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        # Explicitly stderr: stdout stays free for command output.
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
