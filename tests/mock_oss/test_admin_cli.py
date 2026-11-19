"""The simulator controls as commands (`mock-oss outage-start` and the rest)."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from mock_oss import admin_cli
from mock_oss.clock import ManualClock, iso
from mock_oss.main import run
from tests.mock_oss.conftest import KEYS

ENV = {"OSS_KEY_ADMIN": KEYS["admin"]}


def command(client: httpx.Client, capsys: pytest.CaptureFixture[str], *argv: str) -> Any:
    """Run one command against the in-process gateway and return the JSON it printed."""
    assert admin_cli.main(list(argv), client=client, environ=ENV) == 0
    return json.loads(capsys.readouterr().out)


def stand_in(answer: Any) -> httpx.Client:
    """A client whose every request is answered by ``answer`` (a MockTransport handler)."""
    return httpx.Client(transport=httpx.MockTransport(answer), base_url="http://gateway")


def test_an_outage_is_started_backdated_listed_and_resolved(
    client: TestClient, clock: ManualClock, capsys: pytest.CaptureFixture[str]
) -> None:
    started = command(
        client, capsys, "outage-start", "--node-id", "BLR-OLT-0001",
        "--eta-minutes", "120", "--started-minutes-ago", "300",
    )  # fmt: skip
    outage_id = started["outage_id"]
    assert "BLR-OLT-0001" in started["affected_node_ids"]

    active = command(client, capsys, "outages", "--active")["outages"]
    assert [outage["outage_id"] for outage in active] == [outage_id]
    # Backdated: the longer outage that a credit above INR 500 needs, without waiting for it.
    assert active[0]["started_at"] == iso(clock.now() - timedelta(minutes=300))

    command(client, capsys, "outage-resolve", outage_id)
    assert command(client, capsys, "outages", "--active")["outages"] == []
    resolved = command(client, capsys, "outages", "--resolved")["outages"]
    assert [outage["outage_id"] for outage in resolved] == [outage_id]


def test_a_line_is_made_to_fail_and_then_to_pass_again(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    failing = command(client, capsys, "line-fault", "S0000001")
    assert failing == {"service_id": "S0000001", "fault": True, "result": "FAIL"}
    unclear = command(client, capsys, "line-fault", "S0000001", "--inconclusive")
    assert unclear["result"] == "INCONCLUSIVE"
    passing = command(client, capsys, "line-fault", "S0000001", "--clear")
    assert passing == {"service_id": "S0000001", "fault": False, "result": None}


def test_the_network_api_is_taken_down_and_brought_back(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    command(client, capsys, "chaos", "network", "down")

    rules = command(client, capsys, "chaos-list")["rules"]
    assert [(rule["target"], rule["mode"]) for rule in rules] == [("network", "down")]
    assert command(client, capsys, "chaos-clear") == {"cleared": 1}
    assert command(client, capsys, "chaos-list")["rules"] == []


def test_customers_are_found_by_what_a_scene_needs(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    found = command(client, capsys, "customers", "--node-id", "BLR-OLT-0002")["customers"]

    assert [customer["service_id"] for customer in found] == ["S0000003", "S0000004"]


def test_a_request_the_gateway_refuses_exits_1_with_its_answer(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    # Messages are read for an outage or a customer, never all at once.
    assert admin_cli.main(["messages"], client=client, environ=ENV) == 1

    refused = capsys.readouterr()
    assert refused.out == ""
    assert "400" in refused.err
    assert "FILTER_REQUIRED" in refused.err


def test_a_choice_the_gateway_does_not_know_is_refused_before_anything_is_sent() -> None:
    def must_not_be_called(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"sent {request.url}")

    with pytest.raises(SystemExit) as stopped:
        admin_cli.main(
            ["outage-start", "--node-id", "BLR-OLT-0001", "--cause", "METEOR"],
            client=stand_in(must_not_be_called),
            environ=ENV,
        )
    assert stopped.value.code == 2


def test_without_the_admin_key_nothing_is_sent(capsys: pytest.CaptureFixture[str]) -> None:
    def must_not_be_called(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"sent {request.url}")

    assert admin_cli.main(["outages"], client=stand_in(must_not_be_called), environ={}) == 2
    assert "OSS_KEY_ADMIN is not set" in capsys.readouterr().err


def test_the_key_goes_in_the_header_and_is_never_printed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"outages": []})

    assert admin_cli.main(["outages", "--resolved"], client=stand_in(answer), environ=ENV) == 0

    assert sent[0].headers["X-API-Key"] == KEYS["admin"]
    assert sent[0].url.params["active"] == "false"
    printed = capsys.readouterr()
    assert KEYS["admin"] not in printed.out + printed.err


def test_a_gateway_that_cannot_be_reached_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    assert admin_cli.main(["chaos-list"], client=stand_in(unreachable), environ=ENV) == 2

    printed = capsys.readouterr()
    assert "Could not reach the gateway at http://gateway" in printed.err
    assert KEYS["admin"] not in printed.out + printed.err


@pytest.mark.parametrize(
    ("environ", "url"),
    [
        ({}, "http://127.0.0.1:8081"),
        ({"MOCK_OSS_PORT": "18081"}, "http://127.0.0.1:18081"),
        ({"MOCK_OSS_PORT": " "}, "http://127.0.0.1:8081"),
        ({"MOCK_OSS_URL": "http://mock-oss:8081/", "MOCK_OSS_PORT": "9"}, "http://mock-oss:8081"),
    ],
    ids=["default", "port", "blank-port", "explicit-url"],
)
def test_the_gateway_is_looked_for_where_it_runs(environ: dict[str, str], url: str) -> None:
    assert admin_cli.gateway_url(environ) == url


def test_mock_oss_has_the_controls_beside_serve_and_seed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("OSS_KEY_ADMIN", raising=False)

    # Reaches the controls (and stops there, for want of a key) rather than starting a server.
    assert run(["chaos-list"]) == 2
    assert "OSS_KEY_ADMIN is not set" in capsys.readouterr().err
