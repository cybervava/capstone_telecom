"""Proactive notification texts: sourced facts, nothing personal, one message per outage."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from skylink_care.agents import notifications
from skylink_care.domain.models import OutageInfo

NOW = datetime(2026, 11, 15, 5, 1, tzinfo=UTC)  # 10:31 IST
OUTAGE = OutageInfo(
    outage_id="OUT-20261115-0003",
    version=1,
    cause="FIBRE_CUT",
    planned=False,
    started_at=NOW - timedelta(minutes=5),
    eta=NOW + timedelta(hours=2, minutes=29),
    eta_source="noc_estimate",
    resolved_at=None,
)


def test_started_message_names_cause_reference_and_sourced_eta() -> None:
    message = notifications.outage_started(OUTAGE, service_types=["broadband"], area="Indiranagar")

    assert message.template_id == "outage_started.v1"
    assert "affecting your broadband service in Indiranagar" in message.text
    assert "caused by a fibre cut (ref OUT-20261115-0003)" in message.text
    assert (
        "Expected back by 13:00 IST on 15 Nov (network operations centre estimate)" in message.text
    )
    assert "do not need to restart your router or contact us" in message.text


def test_started_message_does_not_invent_an_eta() -> None:
    message = notifications.outage_started(
        replace(OUTAGE, eta=None, eta_source=None), service_types=["broadband"], area=None
    )

    assert "There is no restoration estimate yet." in message.text
    assert "Expected back" not in message.text


def test_planned_maintenance_is_called_what_it_is() -> None:
    planned = replace(OUTAGE, planned=True, cause="PLANNED_MAINTENANCE")

    message = notifications.outage_started(planned, service_types=["broadband"], area="Hebbal")

    assert "planned maintenance is affecting your broadband service in Hebbal" in message.text
    assert "we are aware of an outage" not in message.text


def test_unknown_cause_code_is_never_shown_to_a_customer() -> None:
    message = notifications.outage_started(
        replace(OUTAGE, cause="SOLAR_FLARE_X9"), service_types=["broadband"], area=None
    )

    assert "SOLAR_FLARE_X9" not in message.text
    assert "a network fault" in message.text


def test_customer_with_both_services_affected_gets_one_message_naming_both() -> None:
    message = notifications.outage_started(
        OUTAGE, service_types=["mobile", "broadband", "mobile"], area="Koramangala"
    )

    assert "affecting your broadband and mobile services" in message.text


def test_mobile_only_message_does_not_talk_about_a_router() -> None:
    started = notifications.outage_started(OUTAGE, service_types=["mobile"], area=None)
    resolved = notifications.outage_resolved(
        replace(OUTAGE, resolved_at=NOW + timedelta(hours=1)), service_types=["mobile"]
    )

    assert "router" not in started.text
    assert "restart your phone once" in resolved.text


def test_resolved_message_gives_the_time_and_what_to_do_if_it_is_not_back() -> None:
    message = notifications.outage_resolved(
        replace(OUTAGE, resolved_at=NOW + timedelta(hours=1)), service_types=["broadband"]
    )

    assert message.template_id == "outage_resolved.v1"
    assert "has been resolved at 11:31 IST on 15 Nov (ref OUT-20261115-0003)" in message.text
    assert "restart your router once and contact us if it still does not work" in message.text


def test_request_asks_the_gateway_for_the_customers_own_channel() -> None:
    message = notifications.outage_started(OUTAGE, service_types=["broadband"], area=None)

    body = notifications.request_body(message, customer_id="C0000042", outage_id=OUTAGE.outage_id)

    assert body == {
        "customer_id": "C0000042",
        "channel": "preferred",
        "template_id": "outage_started.v1",
        "body": message.text,
        "outage_id": "OUT-20261115-0003",
    }
    # The customer is addressed by the request, never inside the text.
    assert "C0000042" not in message.text


def test_identity_is_one_message_per_customer_outage_and_template() -> None:
    key = notifications.action_key("C0000042", "OUT-1", notifications.OUTAGE_STARTED)

    assert key == "notify:C0000042:OUT-1:outage_started.v1"
    assert key != notifications.action_key("C0000042", "OUT-1", notifications.OUTAGE_RESOLVED)
    assert key != notifications.action_key("C0000042", "OUT-2", notifications.OUTAGE_STARTED)
