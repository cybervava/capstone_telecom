"""The typed adapters against the real gateway contract (mock_oss, in process).

These tests are the place where a change on either side of the boundary shows up. They also pin
least privilege: each agent identity can reach only what its scopes allow.
"""

from __future__ import annotations

from mock_oss.clock import ManualClock
from skylink_care.adapters.convert import to_live_status
from skylink_care.adapters.gateway import OssGateway
from skylink_care.adapters.result import Err, ErrorKind, Ok
from tests.support.stack import Simulator

CORR = "contract-test"


# --- least privilege ---------------------------------------------------------------------------


async def test_triage_identity_can_read_crm(gateway: OssGateway, simulator: Simulator) -> None:
    customer = await simulator.find_customer(service_type="broadband", mapping_defect="none")
    crm = gateway.crm_for_triage()

    lookup = await crm.lookup(correlation_id=CORR, customer_id=customer["customer_id"])
    profile = await crm.customer(customer["customer_id"], correlation_id=CORR)

    assert isinstance(lookup, Ok)
    assert [match.customer_id for match in lookup.value.matches] == [customer["customer_id"]]
    assert "*" in lookup.value.matches[0].phone_masked
    assert isinstance(profile, Ok)
    assert any(s.service_id == customer["service_id"] for s in profile.value.services)
    assert profile.value.services[0].monthly_fee_usd > 0


async def test_diagnostics_identity_cannot_read_crm(
    gateway: OssGateway, simulator: Simulator
) -> None:
    customer = await simulator.find_customer(service_type="broadband")
    # Build a CRM reader on the diagnostics key: the gateway must refuse it.
    from skylink_care.adapters.clients import CrmReader

    crm_with_wrong_key = CrmReader(gateway._as("diagnostics"))

    result = await crm_with_wrong_key.customer(customer["customer_id"], correlation_id=CORR)

    assert isinstance(result, Err)
    assert result.kind is ErrorKind.FORBIDDEN


async def test_orchestrator_identities_cannot_write(gateway: OssGateway) -> None:
    from skylink_care.adapters.clients import BillingWriter, IncidentWriter

    body = {
        "node_id": "X",
        "service_type": "broadband",
        "kind": "NETWORK_OUTAGE",
        "outage_id": None,
        "summary": "s",
    }
    for identity in ("triage", "diagnostics", "resolution", "dispatch"):
        incident = await IncidentWriter(gateway._as(identity)).open(
            body, idempotency_key=f"neg-{identity}", correlation_id=CORR
        )
        credit = await BillingWriter(gateway._as(identity)).apply_credit(
            {}, idempotency_key=f"neg-credit-{identity}", correlation_id=CORR
        )
        assert isinstance(incident, Err), identity
        assert incident.kind is ErrorKind.FORBIDDEN, identity
        assert isinstance(credit, Err), identity
        assert credit.kind is ErrorKind.FORBIDDEN, identity


# --- network status as evidence ------------------------------------------------------------------


async def test_healthy_node_is_operational_without_alarms(
    gateway: OssGateway, simulator: Simulator
) -> None:
    customer = await simulator.find_customer(service_type="broadband", mapping_defect="none")

    result = await gateway.network_for_diagnostics().node_status(
        customer["true_node_id"], correlation_id=CORR
    )

    assert isinstance(result, Ok)
    assert result.value.status == "OPERATIONAL"
    assert result.value.active_alarms == ()
    live = to_live_status(result)
    assert live.available
    assert live.outage is None
    assert not live.critical_alarm


async def test_declared_outage_is_visible_with_eta_and_source(
    gateway: OssGateway, simulator: Simulator
) -> None:
    customer = await simulator.find_customer(service_type="broadband", mapping_defect="none")
    started = await simulator.start_outage(customer["true_node_id"], eta_minutes=90)

    live = to_live_status(
        await gateway.network_for_diagnostics().node_status(
            customer["true_node_id"], correlation_id=CORR
        )
    )

    assert live.outage is not None
    assert live.outage.outage_id == started["outage_id"]
    assert live.outage.cause == "FIBRE_CUT"
    assert live.outage.eta is not None
    assert live.outage.eta_source is not None


async def test_before_detection_the_node_looks_operational_but_carries_a_critical_alarm(
    gateway: OssGateway, simulator: Simulator, stack_clock: ManualClock
) -> None:
    customer = await simulator.find_customer(service_type="broadband", mapping_defect="none")
    network = gateway.network_for_diagnostics()
    await simulator.start_outage(customer["true_node_id"], detect_after_seconds=300)

    before = await network.node_status(customer["true_node_id"], correlation_id=CORR)
    line = await network.line_test(customer["service_id"], correlation_id=CORR)
    stack_clock.advance(301)
    after = await network.node_status(customer["true_node_id"], correlation_id=CORR)

    assert isinstance(before, Ok)
    assert before.value.status == "OPERATIONAL"
    assert to_live_status(before).critical_alarm
    assert isinstance(line, Ok)
    assert line.value.result == "FAIL"
    assert isinstance(after, Ok)
    assert after.value.status == "OUTAGE"


async def test_line_test_reports_the_true_node_even_when_the_mapping_is_defective(
    gateway: OssGateway, simulator: Simulator
) -> None:
    customer = await simulator.find_customer(service_type="broadband", mapping_defect="missing")

    result = await gateway.network_for_diagnostics().line_test(
        customer["service_id"], correlation_id=CORR
    )

    assert customer["mapped_node_ids"] == []
    assert isinstance(result, Ok)
    assert result.value.node_id == customer["true_node_id"]


async def test_unknown_and_decommissioned_nodes_are_distinct_errors(
    gateway: OssGateway, simulator: Simulator
) -> None:
    stale = await simulator.find_customer(service_type="broadband", mapping_defect="stale")
    network = gateway.network_for_diagnostics()

    unknown = await network.node_status("ZZZ-OLT-9999", correlation_id=CORR)
    retired = await network.node_status(stale["mapped_node_ids"][0], correlation_id=CORR)

    assert isinstance(unknown, Err)
    assert unknown.kind is ErrorKind.NOT_FOUND
    assert isinstance(retired, Err)
    assert retired.kind is ErrorKind.GONE
    assert not to_live_status(retired).available


async def test_event_feed_parses_and_orders_alarm_before_outage(
    gateway: OssGateway, simulator: Simulator
) -> None:
    customer = await simulator.find_customer(service_type="broadband", mapping_defect="none")
    await simulator.start_outage(customer["true_node_id"])

    page = await gateway.feed_for_ingest().events(after_seq=0, correlation_id=CORR)

    assert isinstance(page, Ok)
    kinds = [event.type for event in page.value.events]
    assert kinds.index("ALARM_RAISED") < kinds.index("OUTAGE_STARTED")
    assert page.value.last_seq == page.value.events[-1].seq
    started = next(event for event in page.value.events if event.type == "OUTAGE_STARTED")
    assert customer["true_node_id"] in started.node_ids
    assert started.version == 1


# --- failure modes -----------------------------------------------------------------------------


async def test_network_api_down_is_unavailable_and_therefore_no_evidence(
    gateway: OssGateway, simulator: Simulator
) -> None:
    customer = await simulator.find_customer(service_type="broadband", mapping_defect="none")
    await simulator.fault("network", "down")

    result = await gateway.network_for_diagnostics().node_status(
        customer["true_node_id"], correlation_id=CORR
    )

    assert isinstance(result, Err)
    assert result.kind is ErrorKind.UNAVAILABLE
    assert result.retryable
    assert not to_live_status(result).available


async def test_feed_and_status_faults_are_independent(
    gateway: OssGateway, simulator: Simulator
) -> None:
    customer = await simulator.find_customer(service_type="broadband", mapping_defect="none")
    await simulator.fault("network_feed", "down")

    feed = await gateway.feed_for_ingest().events(after_seq=0, correlation_id=CORR)
    status = await gateway.network_for_diagnostics().node_status(
        customer["true_node_id"], correlation_id=CORR
    )

    assert isinstance(feed, Err)
    assert isinstance(status, Ok)


# --- writes: frozen body, idempotent replay, ambiguous outcome -----------------------------------


async def test_complaint_registration_is_idempotent(
    gateway: OssGateway, simulator: Simulator
) -> None:
    incident = gateway.for_executor().incident
    body = {
        "complaint_ref": "SKL-261115-000001-7",
        "received_at": "2026-11-15T04:30:00+00:00",
        "channel": "chat",
        "category": "UNCLASSIFIED",
        "customer_id": None,
    }

    first = await incident.register_complaint(
        body, idempotency_key="complaint:1", correlation_id=CORR
    )
    replay = await incident.register_complaint(
        body, idempotency_key="complaint:1", correlation_id=CORR
    )
    fetched = await incident.complaint("SKL-261115-000001-7", correlation_id=CORR)

    assert isinstance(first, Ok)
    assert not first.replayed
    assert isinstance(replay, Ok)
    assert replay.replayed
    assert replay.value.registered_at == first.value.registered_at
    assert isinstance(fetched, Ok)
    assert (await simulator.state())["tables"]["complaints"] == 1


async def test_incident_open_link_and_upgrade(gateway: OssGateway, simulator: Simulator) -> None:
    customer = await simulator.find_customer(service_type="broadband", mapping_defect="none")
    incident = gateway.for_executor().incident
    opened = await incident.open(
        {
            "node_id": customer["true_node_id"],
            "service_type": "broadband",
            "kind": "SUSPECTED_NODE_FAULT",
            "outage_id": None,
            "summary": "Customer-reported impact",
        },
        idempotency_key="incident:ep-1",
        correlation_id=CORR,
    )
    assert isinstance(opened, Ok)
    incident_id = opened.value.incident_id

    link = await incident.link_complaint(
        incident_id,
        {"complaint_ref": "SKL-261115-000002-5", "contact_id": "c-1", "customer_id": None},
        idempotency_key="incident-link:ep-1:SKL-261115-000002-5",
        correlation_id=CORR,
    )
    upgraded = await incident.update(
        incident_id,
        {"kind": "NETWORK_OUTAGE", "outage_id": "OUT-1"},
        idempotency_key="incident-update:ep-1:OUT-1",
        correlation_id=CORR,
    )
    fetched = await incident.incident(incident_id, correlation_id=CORR)

    assert isinstance(link, Ok)
    assert isinstance(upgraded, Ok)
    assert (upgraded.value.kind, upgraded.value.outage_id) == ("NETWORK_OUTAGE", "OUT-1")
    assert isinstance(fetched, Ok)
    assert fetched.value.complaint_refs == ("SKL-261115-000002-5",)


async def test_write_lost_on_the_way_back_is_ambiguous_and_safe_to_replay(
    gateway: OssGateway, simulator: Simulator
) -> None:
    customer = await simulator.find_customer(
        service_type="broadband", mapping_defect="none", status="active"
    )
    billing = gateway.for_executor().billing
    body = {
        "customer_id": customer["customer_id"],
        "service_id": customer["service_id"],
        "amount_usd": "120.50",
        "reason_code": "OUTAGE_CREDIT",
        "outage_id": "OUT-1",
        "policy_version": "v0-placeholder",
        "complaint_ref": "SKL-261115-000003-3",
        "approved_by": None,
    }
    key = f"credit:{customer['service_id']}:OUT-1"
    await simulator.fault("billing", "response_loss")

    lost = await billing.apply_credit(body, idempotency_key=key, correlation_id=CORR)
    await simulator.fault("billing", "ok")
    replay = await billing.apply_credit(body, idempotency_key=key, correlation_id=CORR)

    assert isinstance(lost, Err)
    assert lost.outcome_unknown
    assert isinstance(replay, Ok)
    assert replay.replayed
    assert str(replay.value.amount_usd) == "120.50"
    assert (await simulator.state())["tables"]["credits"] == 1


async def test_same_key_with_a_changed_body_is_a_conflict(
    gateway: OssGateway, simulator: Simulator
) -> None:
    customer = await simulator.find_customer(
        service_type="broadband", mapping_defect="none", status="active"
    )
    billing = gateway.for_executor().billing
    body = {
        "customer_id": customer["customer_id"],
        "service_id": customer["service_id"],
        "amount_usd": "100.00",
        "reason_code": "OUTAGE_CREDIT",
        "outage_id": "OUT-2",
        "policy_version": "v0-placeholder",
        "complaint_ref": "SKL-261115-000004-1",
        "approved_by": None,
    }

    await billing.apply_credit(body, idempotency_key="credit:k", correlation_id=CORR)
    changed = await billing.apply_credit(
        {**body, "amount_usd": "900.00"}, idempotency_key="credit:k", correlation_id=CORR
    )

    assert isinstance(changed, Err)
    assert changed.kind is ErrorKind.CONFLICT
    assert changed.code == "IDEMPOTENCY_KEY_REUSED"
    assert not changed.retryable


async def test_the_executor_reads_consent_and_nothing_else_of_the_crm(
    gateway: OssGateway, simulator: Simulator
) -> None:
    """It sends the notices, so it must know whether it may. It has no use for a name, a
    number or an address, and cannot check what a caller knows."""
    customer = await simulator.find_customer(service_type="broadband", consent="false")
    crm = gateway.for_executor().crm

    consent = await crm.consent(customer["customer_id"], correlation_id=CORR)
    profile = await crm.customer(customer["customer_id"], correlation_id=CORR)
    found = await crm.lookup(customer_id=customer["customer_id"], correlation_id=CORR)
    checked = await crm.verify(customer["customer_id"], phone_last4="0000", correlation_id=CORR)

    assert isinstance(consent, Ok)
    assert consent.value.outage_notifications is False
    for refused in (profile, found, checked):
        assert isinstance(refused, Err)
        assert refused.kind is ErrorKind.FORBIDDEN


async def test_triage_reads_a_profile_and_cannot_ask_for_consent_alone(
    gateway: OssGateway, simulator: Simulator
) -> None:
    """Each scope is what one tool needs. Triage has the profile, which says what it needs
    to know; the route the executor uses is not its tool."""
    customer = await simulator.find_customer(service_type="broadband")
    crm = gateway.crm_for_triage()

    profile = await crm.customer(customer["customer_id"], correlation_id=CORR)
    consent = await crm.consent(customer["customer_id"], correlation_id=CORR)

    assert isinstance(profile, Ok)
    assert isinstance(consent, Err)
    assert consent.kind is ErrorKind.FORBIDDEN
