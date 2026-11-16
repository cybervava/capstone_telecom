"""Structured logs: contact details and secrets are redacted before anything is emitted."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest
import structlog

from skylink_care.observability.logging import configure_logging


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """``configure_logging`` is process-wide and binds to the stream this test captured.

    Left in place, every later test that logs would write to a stream pytest has closed.
    """
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    library_levels = {name: logging.getLogger(name).level for name in ("httpx", "httpcore")}
    yield
    structlog.reset_defaults()
    root.handlers, root.level = handlers, level
    for name, was in library_levels.items():
        logging.getLogger(name).setLevel(was)


def test_logged_values_are_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()

    structlog.get_logger("test").info(
        "customer_wrote",
        text="call me on 9845012345 or asha@example.com",
        customer_id="C0001234",
        api_key="sk-live-not-real",
        authorization="Bearer abc.def.ghi",
    )

    line = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert line["text"] == "call me on <PHONE> or <EMAIL>"
    assert line["customer_id"] == "C0001234"
    assert line["api_key"] == "<redacted>"
    assert line["authorization"] == "<redacted>"
    assert line["event"] == "customer_wrote"
    assert line["level"] == "info"


def test_exception_text_is_redacted_too(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()

    try:
        raise ValueError("no customer with phone +91 98450 12345")
    except ValueError:
        structlog.get_logger("test").exception("lookup_failed")

    line = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert "98450" not in line["exception"]
    assert "<PHONE>" in line["exception"]


def test_a_field_that_only_has_key_in_its_name_is_not_a_secret(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The keys that tie an action to its effect are what an operator searches the logs for.
    Hiding them hid nothing secret and made a failed write untraceable."""
    configure_logging()

    structlog.get_logger("test").info(
        "action_failed",
        action_key="credit:8f1c:OUT-20261116-0007",
        dedupe_key="restored:OUT-20261116-0007",
        idempotency_key="b2c1",
        key="raw-key-material",
        staff_key="not-a-real-staff-key",
        signing_key="not-a-real-signing-key",
        jwt_secret="not-a-real-secret",
        access_token="abc.def.ghi",
        db_password="not-a-real-password",
    )

    line = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert line["action_key"] == "credit:8f1c:OUT-20261116-0007"
    assert line["dedupe_key"] == "restored:OUT-20261116-0007"
    assert line["idempotency_key"] == "b2c1"
    for secret in ("key", "staff_key", "signing_key", "jwt_secret", "access_token", "db_password"):
        assert line[secret] == "<redacted>", secret


def test_what_a_library_logs_is_redacted_like_everything_else(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Libraries log through the standard library, past the structured logger. A database
    driver quoting a failed statement must not be where a phone number gets out."""
    configure_logging()

    logging.getLogger("some.library").warning(
        "could not send to %s (%s), customer %s", "+91 98450 12345", "asha@example.com", "C0001234"
    )

    emitted = capsys.readouterr().err
    assert "could not send to <PHONE> (<EMAIL>), customer C0001234" in emitted
    assert "98450" not in emitted


def test_the_http_client_does_not_log_the_address_of_every_request(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The client logs each request with its full address, and an address can carry what the
    request asked by: a caller's line, a customer id. Every call is in the audit trail with
    its outcome already; the client's line adds nothing but the address."""
    configure_logging()

    logging.getLogger("httpx").info(
        'HTTP Request: GET http://gateway/crm/v1/customers/lookup?phone=%2B919845012345 "200 OK"'
    )
    logging.getLogger("httpx").warning("connection pool is full")

    emitted = capsys.readouterr().err
    assert "lookup" not in emitted
    assert "connection pool is full" in emitted


def test_configuring_twice_does_not_log_everything_twice(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging()
    configure_logging()

    logging.getLogger("some.library").warning("once only")

    assert capsys.readouterr().err.count("once only") == 1


def test_a_secret_is_redacted_wherever_it_sits_in_what_is_logged(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging()

    structlog.get_logger("test").warning(
        "gateway_refused",
        headers={"X-API-Key": "not-a-real-key-123", "X-Request-ID": "abc"},
        detail="retry with Authorization: Bearer abc123def456ghi789",
        error=RuntimeError("401 for http://gw/x?token=not-a-real-token-456"),
    )

    emitted = capsys.readouterr().err
    line = json.loads(emitted.strip().splitlines()[-1])
    assert line["headers"] == {"X-API-Key": "<redacted>", "X-Request-ID": "abc"}
    for secret in ("not-a-real-key-123", "abc123def456ghi789", "not-a-real-token-456"):
        assert secret not in emitted


def test_a_library_line_that_cannot_be_rendered_does_not_break_the_caller(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A wrong format string in a library used to be swallowed where the line is written. The
    filter runs before that, and must not turn a bad log line into a failed request."""
    configure_logging()
    (ours,) = [
        handler for handler in logging.getLogger().handlers if "Our" in type(handler).__name__
    ]
    bad = logging.LogRecord(
        "some.library", logging.WARNING, __file__, 1, "took %d ms for %s", ("not-a-number",), None
    )
    good = logging.LogRecord(
        "some.library", logging.WARNING, __file__, 2, "still logging", (), None
    )

    # Handed to our handler alone: the test runner's own handler renders the record itself.
    ours.handle(bad)
    ours.handle(good)

    emitted = capsys.readouterr().err
    assert "<unrenderable log record>" in emitted
    assert "still logging" in emitted


def test_a_stack_attached_to_a_library_line_is_redacted_too(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging()
    record = logging.LogRecord("some.library", logging.WARNING, __file__, 1, "slow call", (), None)
    record.stack_info = "Stack: lookup(phone='+91 98450 12345')"

    logging.getLogger("some.library").handle(record)

    emitted = capsys.readouterr().err
    assert "lookup(phone='<PHONE>')" in emitted
    assert "98450" not in emitted
