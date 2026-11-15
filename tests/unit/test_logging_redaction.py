"""Structured logs: contact details and secrets are redacted before anything is emitted."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
import structlog

from skylink_care.observability.logging import configure_logging


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """``configure_logging`` is process-wide and binds to the stream this test captured.

    Left in place, every later test that logs would write to a stream pytest has closed.
    """
    yield
    structlog.reset_defaults()


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
