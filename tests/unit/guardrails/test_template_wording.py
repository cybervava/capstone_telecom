"""Every template's wording, pinned.

What a message may claim about the network is declared with its template
(``guardrails/claims.py``), and the declaration is only as good as the words stay what they
were when it was made. A list of phrases cannot tell every way of saying that all is well, so
the words themselves are held here: a template whose text changes fails this test until the
change has been looked at, with its claim, and the file of wordings has been written again:

    UPDATE_TEMPLATE_WORDING=1 uv run pytest tests/unit/guardrails/test_template_wording.py

Nothing in that file is typed by hand.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from skylink_care.agents import replies, visit_replies
from skylink_care.agents.dispatch import OfferedSlot
from skylink_care.agents.replies import Reply
from skylink_care.domain.models import OutageInfo
from skylink_care.domain.network import DecisionReason
from skylink_care.guardrails.claims import CLAIMS, check
from skylink_care.orchestrator import handover
from skylink_care.orchestrator.worker import APOLOGY

WORDINGS = Path(__file__).with_name("template_wording.json")
REF = "SKL-261115-000001-9"
AT = datetime(2026, 11, 15, 4, 35, tzinfo=UTC)
OUTAGE = OutageInfo(
    outage_id="OUT-1",
    version=1,
    cause="FIBRE_CUT",
    planned=False,
    started_at=AT - timedelta(minutes=20),
    eta=None,
    eta_source=None,
    resolved_at=None,
)
WITH_ETA = replace(OUTAGE, eta=AT + timedelta(hours=2), eta_source="noc_estimate")
PLANNED = replace(OUTAGE, cause="PLANNED_MAINTENANCE", planned=True)
SLOT = OfferedSlot("SL-1", AT + timedelta(hours=20), AT + timedelta(hours=22))
LATER = OfferedSlot("SL-2", AT + timedelta(hours=26), AT + timedelta(hours=28))


def _network_replies() -> dict[str, Reply]:
    said: dict[str, Reply] = {
        "outage_active/broadband": replies.outage_active(
            REF, OUTAGE, evidence_at=AT, service_type="broadband"
        ),
        "outage_active/mobile": replies.outage_active(
            REF, OUTAGE, evidence_at=AT, service_type="mobile"
        ),
        "outage_active/with an estimate": replies.outage_active(REF, WITH_ETA, evidence_at=AT),
        "outage_active/planned": replies.outage_active(REF, PLANNED, evidence_at=AT),
        "outage_active/unverified time": replies.outage_active(REF, OUTAGE, evidence_at=None),
        "outage_disputed/broadband": replies.outage_active(
            REF, OUTAGE, evidence_at=AT, service_type="broadband", disputed=True
        ),
        "outage_disputed/mobile": replies.outage_active(
            REF, OUTAGE, evidence_at=AT, service_type="mobile", disputed=True
        ),
        "no_outage_reported/broadband": replies.no_outage_reported(REF, evidence_at=AT),
        "no_outage_reported/mobile": replies.no_outage_reported(
            REF, evidence_at=AT, service_type="mobile"
        ),
        "line_ok": replies.line_ok(REF, evidence_at=AT, tested_at=AT),
        "checks_clear": replies.checks_clear(REF, evidence_at=AT, tested_at=AT),
        "line_fault_hold/ten minutes": replies.line_fault_hold(
            REF, evidence_at=AT, tested_at=AT, hold_minutes=10
        ),
        "line_fault_hold/one minute": replies.line_fault_hold(
            REF, evidence_at=AT, tested_at=AT, hold_minutes=1
        ),
        "line_fault": replies.line_fault(REF, tested_at=AT),
        "guided_steps": replies.guided_steps(
            REF,
            guide_title="Tarang FG7 guide",
            section_title="No internet",
            steps=["Check the cable.", "Wait two minutes."],
            citation="guide-tg-fg7 v1 #no-internet",
            evidence_at=AT,
            tested_at=AT,
        ),
    }
    for reason in DecisionReason:
        for service in ("broadband", "mobile"):
            said[f"network_unknown/{reason.value}/{service}"] = replies.network_unknown(
                REF, reason, last_known_outage=OUTAGE, evidence_at=AT, service_type=service
            )
    restored = {"outage_id": "OUT-1", "resolved_at": AT, "service_type": "broadband"}
    said["outage_restored/resolved"] = replies.outage_restored(REF, **restored)
    said["outage_restored/mobile"] = replies.outage_restored(
        REF, **{**restored, "service_type": "mobile"}
    )
    said["outage_restored/no time"] = replies.outage_restored(
        REF, **{**restored, "resolved_at": None}
    )
    said["outage_restored/no outage named"] = replies.outage_restored(
        REF, outage_id=None, resolved_at=None, service_type="broadband"
    )
    return said


def _other_replies() -> dict[str, Reply]:
    said: dict[str, Reply] = {
        "credit_applied": replies.credit_applied(REF, amount_inr="120.50", credit_ref="CR-1"),
        "credit_applied/no reference": replies.credit_applied(
            REF, amount_inr="120.50", credit_ref=None
        ),
        "credit_already_there": replies.credit_already_there(REF),
        "credit_not_applied": replies.credit_not_applied(REF),
        "credit_unconfirmed": replies.credit_unconfirmed(REF),
        "closing_after_credit": replies.closing_after_credit(REF),
        "closed_confirmed": replies.closed_confirmed(REF),
        "closed_quiet": replies.closed_quiet(REF),
        "need_identifier": replies.need_identifier(REF),
        "need_verification": replies.need_verification(REF),
        "verification_failed": replies.verification_failed(REF),
        "out_of_scope": replies.out_of_scope(REF),
        "handed_to_human/apology": replies.handed_to_human(REF, APOLOGY),
        "handed_to_human/any other reason": replies.handed_to_human(
            REF, handover.wording_for("A_REASON_NOBODY_WORDED")
        ),
        "visit_offer": visit_replies.visit_offer(REF, [SLOT, LATER], tested_at=AT, evidence_at=AT),
        "visit_choice_unclear": visit_replies.visit_choice_unclear(REF, [SLOT, LATER]),
        "visit_slot_taken": visit_replies.visit_slot_taken(REF, [LATER]),
        "visit_being_booked": visit_replies.visit_being_booked(REF, SLOT),
        "visit_booked": visit_replies.visit_booked(REF, SLOT, appointment_id="AP-1"),
        "visit_booked/no reference": visit_replies.visit_booked(REF, SLOT, appointment_id=None),
        "visit_already_booked": visit_replies.visit_already_booked(REF, SLOT),
        "visit_already_booked/slot unknown": visit_replies.visit_already_booked(REF, None),
        "visit_declined": visit_replies.visit_declined(REF),
        "visit_not_booked": visit_replies.visit_not_booked(REF),
        "visit_still_being_booked": visit_replies.visit_still_being_booked(REF, SLOT),
        "visit_being_arranged_elsewhere": visit_replies.visit_being_arranged_elsewhere(REF),
        "visit_needs_a_person": visit_replies.visit_needs_a_person(REF),
    }
    for reason in handover._WORDING:
        said[f"handed_to_human/{reason}"] = replies.handed_to_human(
            REF, handover.wording_for(reason)
        )
    return said


def rendered() -> dict[str, dict[str, str]]:
    said = {**_network_replies(), **_other_replies()}
    return {
        name: {"template_id": reply.template_id, "text": reply.text}
        for name, reply in sorted(said.items())
    }


def test_no_template_says_anything_it_did_not_say_when_its_claim_was_declared() -> None:
    now = rendered()
    if os.environ.get("UPDATE_TEMPLATE_WORDING") == "1":
        WORDINGS.write_text(json.dumps(now, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    pinned = json.loads(WORDINGS.read_text(encoding="utf-8"))

    changed = sorted(name for name in now if pinned.get(name) != now[name])
    gone = sorted(set(pinned) - set(now))

    assert changed == [], (
        "the wording of these templates has changed: look at what each now claims about the"
        " network (guardrails/claims.py), then write the wordings again"
    )
    assert gone == []


def test_every_declared_template_has_its_wording_pinned() -> None:
    pinned = {entry["template_id"] for entry in rendered().values()}

    assert set(CLAIMS) - pinned == set()
    assert pinned - set(CLAIMS) == set()


@pytest.mark.parametrize("reason", [*handover._WORDING, "A_REASON_NOBODY_WORDED"])
def test_what_a_customer_is_told_when_a_person_takes_over_claims_nothing(reason: str) -> None:
    """It is sent whatever the network looks like, and even when nothing is known of it."""
    reply = replies.handed_to_human(REF, handover.wording_for(reason))

    assert check(reply, None, now=AT, max_age=timedelta(seconds=60)) is None


def test_the_apology_for_a_failed_turn_can_always_be_sent() -> None:
    """It goes out when everything else has failed, and when a message has just been stopped
    for what it claimed. If it could be stopped too, the customer would be told nothing."""
    apology = replies.handed_to_human(REF, APOLOGY)

    assert check(apology, None, now=AT, max_age=timedelta(seconds=60)) is None
