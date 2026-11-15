"""The shared HTTP layer: every failure becomes a typed error, never an exception or a guess."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from skylink_care.adapters.clients import BillingWriter, CrmReader, NetworkReader
from skylink_care.adapters.convert import to_live_status
from skylink_care.adapters.http import OssHttp
from skylink_care.adapters.result import Err, ErrorKind, Ok

BASE = "http://oss.test"
NODE_STATUS_OK = {
    "node_id": "BLR-OLT-0012",
    "status": "OPERATIONAL",
    "outage": None,
    "active_alarms": [],
    "observed_at": "2026-11-15T04:31:07Z",
    "feed_seq": 4812,
}
OUTAGE = {
    "outage_id": "OUT-20261115-0003",
    "version": 2,
    "cause": "FIBRE_CUT",
    "planned": False,
    "service_types": ["broadband"],
    "started_at": "2026-11-15T04:10:00Z",
    "eta": "2026-11-15T07:30:00Z",
    "eta_source": "field_update",
    "resolved_at": None,
}
STATUS_PATH = "/network/v1/nodes/BLR-OLT-0012/status"


@pytest.fixture
async def http() -> AsyncIterator[OssHttp]:
    async with httpx.AsyncClient(base_url=BASE, timeout=1.0) as client:
        yield OssHttp(client, api_key="key-diagnostics", identity="diagnostics")


@respx.mock
async def test_success_is_parsed_into_the_typed_model(http: OssHttp) -> None:
    route = respx.get(BASE + STATUS_PATH).respond(200, json=NODE_STATUS_OK)

    result = await NetworkReader(http).node_status("BLR-OLT-0012", correlation_id="corr-9")

    assert isinstance(result, Ok)
    assert result.value.status == "OPERATIONAL"
    assert result.value.observed_at.tzinfo is not None
    sent = route.calls.last.request
    assert sent.headers["X-API-Key"] == "key-diagnostics"
    assert sent.headers["X-Correlation-ID"] == "corr-9"
    assert sent.headers["X-Request-ID"] == result.request_id


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (400, ErrorKind.INVALID),
        (401, ErrorKind.UNAUTHORIZED),
        (403, ErrorKind.FORBIDDEN),
        (404, ErrorKind.NOT_FOUND),
        (409, ErrorKind.CONFLICT),
        (410, ErrorKind.GONE),
        (422, ErrorKind.INVALID),
        (429, ErrorKind.RATE_LIMITED),
        (500, ErrorKind.UNAVAILABLE),
        (503, ErrorKind.UNAVAILABLE),
        (504, ErrorKind.TIMEOUT),
        (418, ErrorKind.PROTOCOL),
    ],
)
@respx.mock
async def test_each_failure_status_maps_to_one_error_kind(
    http: OssHttp, status: int, kind: ErrorKind
) -> None:
    respx.get(BASE + STATUS_PATH).respond(
        status, json={"error": {"code": "SOME_CODE", "message": "why"}}
    )

    result = await NetworkReader(http).node_status("BLR-OLT-0012", correlation_id="c")

    assert isinstance(result, Err)
    assert result.kind is kind
    assert result.status_code == status
    assert (result.code, result.message) == ("SOME_CODE", "why")


@respx.mock
async def test_timeout_is_an_error_with_unknown_outcome(http: OssHttp) -> None:
    respx.get(BASE + STATUS_PATH).mock(side_effect=httpx.ReadTimeout("too slow"))

    result = await NetworkReader(http).node_status("BLR-OLT-0012", correlation_id="c")

    assert isinstance(result, Err)
    assert result.kind is ErrorKind.TIMEOUT
    assert result.outcome_unknown
    assert result.retryable


@respx.mock
async def test_connection_failure_is_unavailable(http: OssHttp) -> None:
    respx.get(BASE + STATUS_PATH).mock(side_effect=httpx.ConnectError("refused"))

    result = await NetworkReader(http).node_status("BLR-OLT-0012", correlation_id="c")

    assert isinstance(result, Err)
    assert result.kind is ErrorKind.UNAVAILABLE


@pytest.mark.parametrize(
    ("raised", "kind"),
    [
        (httpx.TooManyRedirects("loop"), ErrorKind.UNAVAILABLE),
        (httpx.DecodingError("bad gzip"), ErrorKind.PROTOCOL),
        (httpx.RemoteProtocolError("peer closed"), ErrorKind.UNAVAILABLE),
    ],
)
@respx.mock
async def test_no_client_exception_escapes_the_adapter(
    http: OssHttp, raised: Exception, kind: ErrorKind
) -> None:
    respx.get(BASE + STATUS_PATH).mock(side_effect=raised)

    result = await NetworkReader(http).node_status("BLR-OLT-0012", correlation_id="c")

    assert isinstance(result, Err)
    assert result.kind is kind


@pytest.mark.parametrize("identifier", ["..", ".", "../..", "C0001234/../C0009999"])
@respx.mock
async def test_dot_segments_cannot_address_a_different_resource(
    http: OssHttp, identifier: str
) -> None:
    route = respx.get(url__regex=r".*/crm/v1/.*").respond(404, json={})

    await CrmReader(http).consent(identifier, correlation_id="c")

    path = route.calls.last.request.url.raw_path
    assert path.startswith(b"/crm/v1/customers/")
    assert path.endswith(b"/consent")
    assert b"/../" not in path
    assert path.count(b"/") == 5


@pytest.mark.parametrize("kind", [ErrorKind.NOT_FOUND, ErrorKind.INVALID, ErrorKind.CONFLICT])
def test_definitive_refusals_are_neither_retryable_nor_ambiguous(kind: ErrorKind) -> None:
    error = Err(kind=kind, message="no")

    assert not error.retryable
    assert not error.outcome_unknown


@respx.mock
async def test_a_body_that_is_not_json_is_a_protocol_error(http: OssHttp) -> None:
    respx.get(BASE + STATUS_PATH).respond(200, text="<html>gateway</html>")

    result = await NetworkReader(http).node_status("BLR-OLT-0012", correlation_id="c")

    assert isinstance(result, Err)
    assert result.kind is ErrorKind.PROTOCOL


@pytest.mark.parametrize(
    "broken",
    [
        {**NODE_STATUS_OK, "status": "OUTAGE"},  # says outage, carries none
        {**NODE_STATUS_OK, "outage": OUTAGE},  # says operational, carries one
        {**NODE_STATUS_OK, "observed_at": "2026-11-15T04:31:07"},  # no timezone
        {**NODE_STATUS_OK, "status": "FINE"},
        {key: value for key, value in NODE_STATUS_OK.items() if key != "observed_at"},
    ],
    ids=["outage-without-detail", "operational-with-outage", "naive-time", "enum", "missing"],
)
@respx.mock
async def test_an_answer_that_breaks_the_contract_is_a_protocol_error(
    http: OssHttp, broken: dict[str, object]
) -> None:
    respx.get(BASE + STATUS_PATH).respond(200, json=broken)

    result = await NetworkReader(http).node_status("BLR-OLT-0012", correlation_id="c")

    assert isinstance(result, Err)
    assert result.kind is ErrorKind.PROTOCOL


@respx.mock
async def test_identifiers_are_encoded_as_a_single_path_segment(http: OssHttp) -> None:
    route = respx.get(url__regex=r".*/network/v1/nodes/.*").respond(404, json={})

    await NetworkReader(http).node_status("../../admin/v1/reset", correlation_id="c")

    assert route.calls.last.request.url.raw_path == (
        b"/network/v1/nodes/%2E%2E%2F%2E%2E%2Fadmin%2Fv1%2Freset/status"
    )


@respx.mock
async def test_write_sends_the_body_unchanged_with_its_idempotency_key(http: OssHttp) -> None:
    body = {
        "customer_id": "C0000001",
        "service_id": "S0000001",
        "amount_inr": "541.61",
        "reason_code": "OUTAGE_CREDIT",
        "outage_id": "OUT-1",
        "policy_version": "v0-placeholder",
        "complaint_ref": "SKL-261115-000001-7",
        "approved_by": "sup-1",
    }
    credit = {
        **body,
        "credit_id": "CR-1",
        "status": "APPLIED",
        "created_at": "2026-11-15T05:00:00Z",
    }
    route = respx.post(BASE + "/billing/v1/credits").respond(
        200, json=credit, headers={"Idempotent-Replay": "true"}
    )

    result = await BillingWriter(http).apply_credit(
        body, idempotency_key="credit:S0000001:OUT-1", correlation_id="c"
    )

    assert isinstance(result, Ok)
    assert result.replayed
    assert str(result.value.amount_inr) == "541.61"
    sent = route.calls.last.request
    assert sent.headers["Idempotency-Key"] == "credit:S0000001:OUT-1"
    assert httpx.Response(200, content=sent.content).json() == body


async def test_lookup_needs_exactly_one_identifier(http: OssHttp) -> None:
    crm = CrmReader(http)

    with pytest.raises(ValueError, match="exactly one"):
        await crm.lookup(correlation_id="c")
    with pytest.raises(ValueError, match="exactly one"):
        await crm.lookup(correlation_id="c", phone="+919800000001", email="a@example.com")


# --- conversion to evidence ----------------------------------------------------------------------


@respx.mock
async def test_operational_answer_becomes_live_clear_evidence(http: OssHttp) -> None:
    respx.get(BASE + STATUS_PATH).respond(200, json=NODE_STATUS_OK)

    live = to_live_status(await NetworkReader(http).node_status("BLR-OLT-0012", correlation_id="c"))

    assert live.available
    assert live.outage is None
    assert not live.critical_alarm
    assert live.observed_at is not None


@respx.mock
async def test_outage_answer_becomes_live_outage_evidence(http: OssHttp) -> None:
    respx.get(BASE + STATUS_PATH).respond(
        200, json={**NODE_STATUS_OK, "status": "OUTAGE", "outage": OUTAGE}
    )

    live = to_live_status(await NetworkReader(http).node_status("BLR-OLT-0012", correlation_id="c"))

    assert live.outage is not None
    assert live.outage.outage_id == "OUT-20261115-0003"
    assert live.outage.eta_source == "field_update"


@respx.mock
async def test_a_critical_alarm_on_the_parent_is_carried_into_the_evidence(http: OssHttp) -> None:
    alarm = {
        "alarm_id": "ALM-1",
        "node_id": "BLR-AGG-0003",
        "alarm_type": "LINK_DOWN",
        "severity": "critical",
        "raised_at": "2026-11-15T04:10:04Z",
    }
    respx.get(BASE + STATUS_PATH).respond(200, json={**NODE_STATUS_OK, "active_alarms": [alarm]})

    live = to_live_status(await NetworkReader(http).node_status("BLR-OLT-0012", correlation_id="c"))

    assert live.available
    assert live.critical_alarm


@respx.mock
async def test_a_minor_alarm_is_not_suspicious(http: OssHttp) -> None:
    alarm = {
        "alarm_id": "ALM-2",
        "node_id": "BLR-OLT-0012",
        "alarm_type": "HIGH_TEMP",
        "severity": "minor",
        "raised_at": "2026-11-15T04:10:04Z",
    }
    respx.get(BASE + STATUS_PATH).respond(200, json={**NODE_STATUS_OK, "active_alarms": [alarm]})

    live = to_live_status(await NetworkReader(http).node_status("BLR-OLT-0012", correlation_id="c"))

    assert not live.critical_alarm


@pytest.mark.parametrize("status", [503, 504, 410, 404])
@respx.mock
async def test_a_failed_status_call_is_no_evidence_at_all(http: OssHttp, status: int) -> None:
    respx.get(BASE + STATUS_PATH).respond(status, json={"error": {"code": "X", "message": "y"}})

    live = to_live_status(await NetworkReader(http).node_status("BLR-OLT-0012", correlation_id="c"))

    assert not live.available
    assert live.outage is None
    assert live.observed_at is None
