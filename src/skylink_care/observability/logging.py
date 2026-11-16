"""Structured JSON logging with personal data and secrets redacted before anything is emitted.

Two streams end here. What this code logs goes through structlog, field by field. What the
libraries log (the HTTP client, the database driver, the web server) goes through the standard
library, and is redacted as text by a filter on the one handler every such record reaches.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from skylink_care.guardrails.pii import REDACTED, is_secret_name, redact_for_log

# Libraries whose INFO lines are one per request, with the full address of the request. An
# address can carry what was asked by (a caller's line). Every call is in the audit trail.
_PER_REQUEST_LOGGERS = ("httpx", "httpcore")
UNRENDERABLE = "<unrenderable log record>"


def _redact(
    _logger: Any, _method: str, event: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for name in list(event):
        event[name] = REDACTED if is_secret_name(name) else redact_for_log(event[name])
    return event


class _RedactedText(logging.Filter):
    """What a library logs, with contact details and secrets taken out: the message with its
    arguments filled in, and the text of an exception or of a stack if it carries one."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            # A format string and its arguments that do not fit. Where a line is written this
            # is swallowed; a filter runs before that, inside the caller's own call.
            message = UNRENDERABLE
        record.msg, record.args = redact_for_log(message), None
        if record.exc_info:
            formatted = logging.Formatter().formatException(record.exc_info)
            record.exc_text, record.exc_info = str(redact_for_log(formatted)), None
        if record.stack_info:
            record.stack_info = str(redact_for_log(record.stack_info))
        return True


class _OurHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """The handler this module installs, so that configuring again replaces it."""


def configure_logging(level: str = "INFO") -> None:
    """Send JSON logs to stderr. Call once at process start; calling again changes nothing."""
    handler = _OurHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(_RedactedText())
    root = logging.getLogger()
    root.handlers = [kept for kept in root.handlers if not isinstance(kept, _OurHandler)]
    root.addHandler(handler)
    root.setLevel(level)
    for name in _PER_REQUEST_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
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
