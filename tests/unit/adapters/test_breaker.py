"""A dependency that keeps failing is rested instead of being called for every contact."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from pydantic import SecretStr

from skylink_care.adapters.breaker import BreakerPolicy, Circuit, Circuits
from skylink_care.adapters.clients import (
    BillingWriter,
    CrmReader,
    NetworkFeedReader,
    NetworkReader,
)
from skylink_care.adapters.gateway import GatewayPolicy, OssGateway, open_gateway
from skylink_care.adapters.http import OssHttp
from skylink_care.adapters.result import Err, ErrorKind, Ok, Result
from skylink_care.config import OssKeys, Settings

BASE = "http://oss.test"
STATUS = BASE + "/network/v1/nodes/N1/status"
NODE_STATUS_OK = {
    "node_id": "N1",
    "status": "OPERATIONAL",
    "outage": None,
    "active_alarms": [],
    "observed_at": "2026-11-15T04:31:07Z",
    "feed_seq": 4812,
}
CREDIT = {
    "customer_id": "C1",
    "service_id": "S1",
    "amount_inr": "120.50",
    "reason_code": "OUTAGE_CREDIT",
    "outage_id": "OUT-1",
    "policy_version": "v0-placeholder",
}
FAILURES, REST = 3, 10


class ManualClock:
    """Seconds that only go forwards, and only when a test says so."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=BASE, timeout=1.0) as shared:
        yield shared


@pytest.fixture
def http(client: httpx.AsyncClient, clock: ManualClock) -> OssHttp:
    circuits = Circuits(BreakerPolicy(failures=FAILURES, rest_seconds=REST), clock)
    return OssHttp(client, api_key="key", identity="diagnostics", circuits=circuits)


@pytest.fixture
def retrying(client: httpx.AsyncClient, clock: ManualClock) -> OssHttp:
    circuits = Circuits(BreakerPolicy(failures=FAILURES, rest_seconds=REST), clock)
    return OssHttp(
        client,
        api_key="key",
        identity="diagnostics",
        circuits=circuits,
        read_retries=1,
        retry_pause_seconds=0,
    )


async def status(http: OssHttp) -> Result[object]:
    return await NetworkReader(http).node_status("N1", correlation_id="corr")


async def open_the_circuit(http: OssHttp, route: respx.Route) -> None:
    route.respond(503)
    for _ in range(FAILURES):
        await status(http)


def kind(result: Result[object]) -> ErrorKind | None:
    return result.kind if isinstance(result, Err) else None


@respx.mock
async def test_a_dependency_that_keeps_failing_is_no_longer_called(http: OssHttp) -> None:
    route = respx.get(STATUS).respond(503)

    failed = [await status(http) for _ in range(FAILURES)]
    rested = await status(http)

    assert [kind(result) for result in failed] == [ErrorKind.UNAVAILABLE] * FAILURES
    assert isinstance(rested, Err)
    assert rested.kind is ErrorKind.CIRCUIT_OPEN
    # Not attempted: asking again later may work, and nothing was sent that could have landed.
    assert rested.retryable
    assert not rested.outcome_unknown
    assert route.call_count == FAILURES


@respx.mock
async def test_timeouts_count_like_any_other_failure(http: OssHttp) -> None:
    route = respx.get(STATUS).mock(side_effect=httpx.ReadTimeout("slow"))

    failed = [await status(http) for _ in range(FAILURES)]

    assert [kind(result) for result in failed] == [ErrorKind.TIMEOUT] * FAILURES
    assert kind(await status(http)) is ErrorKind.CIRCUIT_OPEN
    assert route.call_count == FAILURES


@respx.mock
async def test_failures_that_are_not_consecutive_do_not_open_the_circuit(http: OssHttp) -> None:
    down, up = httpx.Response(503), httpx.Response(200, json=NODE_STATUS_OK)
    route = respx.get(STATUS).mock(side_effect=[down, down, up, down, down, up])

    results = [await status(http) for _ in range(6)]

    assert route.call_count == 6
    assert [isinstance(result, Ok) for result in results] == [False, False, True] * 2


@respx.mock
async def test_a_refusal_is_an_answer_and_never_opens_the_circuit(http: OssHttp) -> None:
    route = respx.get(STATUS).respond(404, json={"error": {"code": "NO_NODE", "message": "?"}})

    results = [await status(http) for _ in range(FAILURES + 2)]

    assert {kind(result) for result in results} == {ErrorKind.NOT_FOUND}
    assert route.call_count == FAILURES + 2


@respx.mock
async def test_after_the_rest_one_call_tries_again_and_a_good_answer_closes_the_circuit(
    http: OssHttp, clock: ManualClock
) -> None:
    route = respx.get(STATUS)
    await open_the_circuit(http, route)
    route.respond(200, json=NODE_STATUS_OK)

    clock.advance(REST - 1)
    still_resting = await status(http)
    clock.advance(1)
    probe = await status(http)
    after = [await status(http) for _ in range(3)]

    assert kind(still_resting) is ErrorKind.CIRCUIT_OPEN
    assert isinstance(probe, Ok)
    assert all(isinstance(result, Ok) for result in after)
    assert route.call_count == FAILURES + 4


@respx.mock
async def test_a_probe_that_fails_rests_the_dependency_for_another_period(
    http: OssHttp, clock: ManualClock
) -> None:
    route = respx.get(STATUS)
    await open_the_circuit(http, route)

    clock.advance(REST)
    probe = await status(http)
    straight_after = await status(http)
    clock.advance(REST)
    second_probe = await status(http)

    assert kind(probe) is ErrorKind.UNAVAILABLE
    assert kind(straight_after) is ErrorKind.CIRCUIT_OPEN
    assert kind(second_probe) is ErrorKind.UNAVAILABLE
    assert route.call_count == FAILURES + 2


@respx.mock
async def test_only_one_call_probes_while_the_others_are_still_refused(
    http: OssHttp, clock: ManualClock
) -> None:
    route = respx.get(STATUS)
    await open_the_circuit(http, route)
    answer = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await answer.wait()
        return httpx.Response(200, json=NODE_STATUS_OK)

    route.mock(side_effect=slow)
    clock.advance(REST)

    probe = asyncio.create_task(status(http))
    await asyncio.sleep(0)
    meanwhile = await status(http)
    answer.set()

    assert kind(meanwhile) is ErrorKind.CIRCUIT_OPEN
    assert isinstance(await probe, Ok)
    assert route.call_count == FAILURES + 1


@respx.mock
async def test_a_probe_that_is_cancelled_does_not_leave_the_circuit_waiting_for_it(
    http: OssHttp, clock: ManualClock
) -> None:
    route = respx.get(STATUS)
    await open_the_circuit(http, route)

    async def never(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    route.mock(side_effect=never)
    clock.advance(REST)
    probe = asyncio.create_task(status(http))
    await asyncio.sleep(0.01)
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe

    route.respond(200, json=NODE_STATUS_OK)

    # Nothing was learnt, so nothing is rested again: the next call is the probe.
    assert isinstance(await status(http), Ok)


@respx.mock
async def test_a_probe_that_found_no_free_connection_gives_its_place_to_the_next_call(
    http: OssHttp, clock: ManualClock
) -> None:
    route = respx.get(STATUS)
    await open_the_circuit(http, route)
    clock.advance(REST)
    route.mock(side_effect=httpx.PoolTimeout("no connection free"))

    not_sent = await status(http)
    route.respond(200, json=NODE_STATUS_OK)
    probe = await status(http)

    assert kind(not_sent) is ErrorKind.CIRCUIT_OPEN
    assert isinstance(probe, Ok)


@respx.mock
async def test_each_dependency_has_its_own_circuit(http: OssHttp) -> None:
    await open_the_circuit(http, respx.get(STATUS))
    crm = respx.get(BASE + "/crm/v1/customers/C1/consent").respond(
        200, json={"outage_notifications": True, "channel": "sms", "updated_at": None}
    )
    feed = respx.get(BASE + "/network/v1/events").respond(503)

    consent = await CrmReader(http).consent("C1", correlation_id="corr")
    polled = await NetworkFeedReader(http).events(after_seq=0, correlation_id="corr")

    assert kind(await status(http)) is ErrorKind.CIRCUIT_OPEN
    assert isinstance(consent, Ok)
    assert crm.call_count == 1
    # The event feed is its own dependency: the status API resting does not stop the poll.
    assert kind(polled) is ErrorKind.UNAVAILABLE
    assert feed.call_count == 1


@respx.mock
async def test_an_open_circuit_sends_no_write_and_says_it_was_not_attempted(
    http: OssHttp,
) -> None:
    route = respx.post(BASE + "/billing/v1/credits").respond(503)
    billing = BillingWriter(http)

    async def apply(key: str) -> Result[object]:
        return await billing.apply_credit(CREDIT, idempotency_key=key, correlation_id="corr")

    sent = [await apply(f"credit:S1:OUT-{n}") for n in range(FAILURES)]
    rested = await apply("credit:S1:OUT-9")

    assert all(isinstance(result, Err) and result.outcome_unknown for result in sent)
    assert isinstance(rested, Err)
    assert rested.kind is ErrorKind.CIRCUIT_OPEN
    assert not rested.outcome_unknown
    assert route.call_count == FAILURES


@respx.mock
async def test_a_read_that_fails_fast_is_tried_once_more(retrying: OssHttp) -> None:
    route = respx.get(STATUS).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=NODE_STATUS_OK)]
    )

    result = await status(retrying)

    assert isinstance(result, Ok)
    assert route.call_count == 2


@respx.mock
async def test_a_read_that_fails_twice_is_an_error_and_both_failures_count(
    retrying: OssHttp,
) -> None:
    route = respx.get(STATUS).mock(side_effect=httpx.ConnectError("refused"))

    first = await status(retrying)
    second = await status(retrying)
    third = await status(retrying)

    assert kind(first) is ErrorKind.UNAVAILABLE
    # The second read's first attempt is the third failure in a row: its retry is not sent,
    # and the caller is told what the attempt that was made found.
    assert kind(second) is ErrorKind.UNAVAILABLE
    assert kind(third) is ErrorKind.CIRCUIT_OPEN
    assert route.call_count == FAILURES


@respx.mock
async def test_a_timeout_is_not_tried_again(retrying: OssHttp) -> None:
    route = respx.get(STATUS).mock(side_effect=httpx.ReadTimeout("slow"))

    assert kind(await status(retrying)) is ErrorKind.TIMEOUT
    assert route.call_count == 1


@respx.mock
async def test_a_definite_answer_is_not_tried_again(retrying: OssHttp) -> None:
    route = respx.get(STATUS).respond(404, json={"error": {"code": "NO_NODE", "message": "?"}})

    assert kind(await status(retrying)) is ErrorKind.NOT_FOUND
    assert route.call_count == 1


@respx.mock
async def test_a_write_is_never_tried_again_by_the_http_layer(retrying: OssHttp) -> None:
    route = respx.post(BASE + "/billing/v1/credits").respond(503)

    result = await BillingWriter(retrying).apply_credit(
        CREDIT, idempotency_key="credit:S1:OUT-1", correlation_id="corr"
    )

    assert kind(result) is ErrorKind.UNAVAILABLE
    assert route.call_count == 1


@respx.mock
async def test_the_long_poll_of_the_feed_is_not_tried_again(retrying: OssHttp) -> None:
    route = respx.get(BASE + "/network/v1/events").respond(503)

    result = await NetworkFeedReader(retrying).events(after_seq=0, correlation_id="corr")

    assert kind(result) is ErrorKind.UNAVAILABLE
    assert route.call_count == 1


@respx.mock
async def test_every_client_of_one_process_shares_the_circuits(clock: ManualClock) -> None:
    """What diagnostics learns about the status API, the executor's own reads respect."""
    keys = OssKeys(diagnostics=SecretStr("key-d"), executor=SecretStr("key-x"))
    policy = GatewayPolicy(breaker=BreakerPolicy(failures=FAILURES, rest_seconds=REST))
    gateway = OssGateway(BASE, keys, policy=policy, monotonic=clock)
    route = respx.get(STATUS).respond(503)
    try:
        for _ in range(FAILURES):
            await gateway.network_for_diagnostics().node_status("N1", correlation_id="corr")
        from_executor = await gateway.for_executor().network.node_status(
            "N1", correlation_id="corr"
        )
    finally:
        await gateway.aclose()

    assert kind(from_executor) is ErrorKind.CIRCUIT_OPEN
    assert route.call_count == FAILURES
    assert gateway.circuits.snapshot() == {"network": "open"}


def a_circuit(clock: ManualClock) -> Circuit:
    policy = BreakerPolicy(failures=FAILURES, rest_seconds=REST)
    return Circuits(policy, clock).for_path("/network/v1/nodes/N1/status")


def opened(circuit: Circuit) -> None:
    for _ in range(FAILURES):
        ticket = circuit.admit()
        assert ticket is not None
        circuit.failed(ticket)
    assert circuit.state == "open"


def test_a_call_already_on_its_way_when_the_circuit_opened_cannot_close_it_by_answering_late(
    clock: ManualClock,
) -> None:
    """Found by review: an old answer once closed the circuit with no probe, and the dead
    dependency then cost a full round of timeouts again."""
    circuit = a_circuit(clock)
    early = circuit.admit()
    assert early is not None
    opened(circuit)

    circuit.answered(early)

    assert circuit.state == "open"
    assert circuit.admit() is None


def test_a_late_failure_does_not_stand_in_for_the_probe(clock: ManualClock) -> None:
    """Found by review: a call from before the circuit opened, failing during the probe, once
    freed the probe's place, and a second probe could go out beside the first."""
    circuit = a_circuit(clock)
    early = circuit.admit()
    assert early is not None
    opened(circuit)
    clock.advance(REST)
    probe = circuit.admit()
    assert probe is not None

    circuit.failed(early)
    circuit.abandoned(early)

    assert circuit.state == "half_open"
    assert circuit.admit() is None  # the probe is still out
    circuit.answered(probe)
    assert circuit.state == "closed"


def test_an_answer_from_before_the_circuit_last_opened_means_nothing_after_it_closed(
    clock: ManualClock,
) -> None:
    circuit = a_circuit(clock)
    early = circuit.admit()
    assert early is not None
    opened(circuit)
    clock.advance(REST)
    probe = circuit.admit()
    assert probe is not None
    circuit.answered(probe)

    for _ in range(FAILURES):
        circuit.failed(early)  # old news, however often it is told

    assert circuit.state == "closed"


@respx.mock
async def test_a_call_that_never_finishes_is_given_up_when_its_whole_time_is_spent(
    client: httpx.AsyncClient, clock: ManualClock
) -> None:
    """Found by review: the client's timeouts apply to each read, so an answer that trickles
    in never ended. As the probe of a rested dependency it kept the circuit open for ever."""
    circuits = Circuits(BreakerPolicy(failures=FAILURES, rest_seconds=REST), clock)
    http = OssHttp(
        client, api_key="key", identity="diagnostics", circuits=circuits, whole_call_seconds=0.05
    )
    route = respx.get(STATUS)
    await open_the_circuit(http, route)

    async def never(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    route.mock(side_effect=never)
    clock.advance(REST)
    probe = await status(http)
    route.respond(200, json=NODE_STATUS_OK)
    clock.advance(REST)
    after = await status(http)

    assert isinstance(probe, Err)
    assert probe.kind is ErrorKind.TIMEOUT
    assert probe.outcome_unknown  # it was sent: for a write the outcome would be unknown
    assert isinstance(after, Ok)


@respx.mock
async def test_no_free_connection_is_not_held_against_the_dependency(http: OssHttp) -> None:
    """Nothing was sent, so nothing was learnt about the API the call was meant for."""
    route = respx.get(STATUS).mock(side_effect=httpx.PoolTimeout("no connection free"))

    results = [await status(http) for _ in range(FAILURES + 2)]
    route.respond(200, json=NODE_STATUS_OK)
    after = await status(http)

    assert {kind(result) for result in results} == {ErrorKind.CIRCUIT_OPEN}
    assert all(isinstance(r, Err) and not r.outcome_unknown for r in results)
    assert isinstance(after, Ok)  # the circuit never opened


def test_circuit_names_follow_the_dependency_not_the_path() -> None:
    circuits = Circuits(BreakerPolicy(), ManualClock())

    assert circuits.for_path("/network/v1/nodes/N1/status").name == "network"
    assert circuits.for_path("/network/v1/line-tests").name == "network"
    assert circuits.for_path("/network/v1/events").name == "network_feed"
    assert circuits.for_path("/crm/v1/customers/C1").name == "crm"
    assert circuits.for_path("/billing/v1/credits").name == "billing"
    assert circuits.for_path("/field/v1/slots").name == "field"
    assert circuits.for_path("/incident/v1/incidents").name == "incident"
    assert circuits.for_path("/notify/v1/messages").name == "notify"


@pytest.mark.parametrize(
    "policy",
    [{"failures": 0}, {"failures": -1}, {"rest_seconds": 0}, {"rest_seconds": -5}],
)
def test_a_policy_that_would_never_open_or_never_rest_is_refused(policy: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="must be"):
        BreakerPolicy(**policy)


@respx.mock
async def test_a_process_treats_failing_dependencies_as_its_settings_say() -> None:
    settings = Settings(
        db_password=SecretStr("not-used-here"),
        oss_base_url=BASE,
        oss_breaker_failures=2,
        oss_breaker_rest_seconds=30,
        oss_read_retries=0,
    )
    gateway = open_gateway(settings, OssKeys(diagnostics=SecretStr("key-d")))
    route = respx.get(STATUS).respond(503)
    try:
        reader = gateway.network_for_diagnostics()
        results = [await reader.node_status("N1", correlation_id="corr") for _ in range(3)]
    finally:
        await gateway.aclose()

    assert [kind(result) for result in results] == [
        ErrorKind.UNAVAILABLE,
        ErrorKind.UNAVAILABLE,
        ErrorKind.CIRCUIT_OPEN,
    ]
    assert route.call_count == 2


@pytest.mark.parametrize(
    "setting",
    [
        {"oss_breaker_failures": 0},
        {"oss_breaker_rest_seconds": 0},
        {"oss_read_retries": -1},
        {"oss_read_retries": 4},
    ],
)
def test_settings_that_would_disable_the_breaker_or_retry_without_bound_are_refused(
    setting: dict[str, int],
) -> None:
    with pytest.raises(ValueError, match="oss_"):
        Settings(db_password=SecretStr("not-used-here"), **setting)
