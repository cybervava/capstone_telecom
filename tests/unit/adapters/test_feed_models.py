"""The NOC feed is another system's output: its events are bounded and checked on arrival."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from skylink_care.adapters.models import (
    MAX_EVENTS_PER_PAGE,
    MAX_NODES_PER_EVENT,
    EventPage,
    NetworkEventDto,
    OutageDto,
)


def event(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "seq": 1,
        "event_id": "evt_1",
        "type": "OUTAGE_STARTED",
        "emitted_at": "2026-11-15T04:30:00Z",
        "node_ids": ["BLR-OLT-0001"],
        "outage_id": "OUT-20261115-0001",
        "version": 1,
        "cause": "FIBRE_CUT",
        "planned": False,
        "started_at": "2026-11-15T04:30:00Z",
    }
    return {**base, **overrides}


def test_well_formed_event_is_accepted_and_unknown_fields_are_ignored() -> None:
    parsed = NetworkEventDto.model_validate(event(added_by_a_newer_noc_release="x"))

    assert parsed.outage_id == "OUT-20261115-0001"
    assert parsed.node_ids == ("BLR-OLT-0001",)


@pytest.mark.parametrize(
    "outage_id",
    [
        "OUT-1) visit http://example.test/claim",  # would be sent to customers as "ref …"
        "OUT 1",
        "OUT-1\nSkyLink: reply with your PIN",
        "",
        "X" * 65,
    ],
)
def test_outage_id_that_could_carry_text_into_a_customer_message_is_rejected(
    outage_id: str,
) -> None:
    with pytest.raises(ValidationError):
        NetworkEventDto.model_validate(event(outage_id=outage_id))
    with pytest.raises(ValidationError):
        OutageDto.model_validate(
            {
                "outage_id": outage_id,
                "version": 1,
                "cause": "FIBRE_CUT",
                "planned": False,
                "started_at": "2026-11-15T04:30:00Z",
            }
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"node_ids": [f"N-{n}" for n in range(MAX_NODES_PER_EVENT + 1)]},
        {"node_ids": ["BLR-OLT-0001'; DROP TABLE nodes; --"]},
        {"version": 2**31},  # would not fit the column it is stored in
        {"version": 0},
        {"seq": 2**63},
        {"seq": 0},
        {"cause": "C" * 41},
        {"type": "OUTAGE_INVENTED"},
        {"emitted_at": "2026-11-15T04:30:00"},  # no offset: which moment is it?
    ],
    ids=[
        "too many nodes",
        "node id with punctuation",
        "version too large",
        "version zero",
        "sequence too large",
        "sequence zero",
        "cause too long",
        "unknown type",
        "naive timestamp",
    ],
)
def test_event_outside_its_bounds_is_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        NetworkEventDto.model_validate(event(**overrides))


def test_page_cannot_carry_more_events_than_one_poll_may_apply() -> None:
    events = [event(seq=n, event_id=f"evt_{n}") for n in range(1, MAX_EVENTS_PER_PAGE + 2)]

    with pytest.raises(ValidationError):
        EventPage.model_validate(
            {"events": events, "last_seq": len(events), "generated_at": "2026-11-15T04:30:00Z"}
        )
