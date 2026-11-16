"""What an outgoing message may claim about the network, against the evidence of its turn."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from skylink_care.agents import replies, visit_replies
from skylink_care.agents.dispatch import OfferedSlot
from skylink_care.agents.replies import Reply
from skylink_care.domain.models import OutageInfo
from skylink_care.domain.network import DecisionReason
from skylink_care.guardrails.claims import CLAIMS, Claim, check

NOW = datetime(2026, 11, 15, 4, 35, tzinfo=UTC)
MAX_AGE = timedelta(seconds=60)
REF = "SKL-261115-000001-9"
OUTAGE = OutageInfo(
    outage_id="OUT-1",
    version=1,
    cause="FIBRE_CUT",
    planned=False,
    started_at=NOW - timedelta(minutes=20),
    eta=None,
    eta_source=None,
    resolved_at=None,
)
SLOT = OfferedSlot("SL-1", NOW + timedelta(hours=20), NOW + timedelta(hours=22))


def evidence(
    state: str,
    *,
    decided: timedelta = timedelta(0),
    outage_id: str | None = None,
    reason: DecisionReason | None = None,
    observed: timedelta | None = None,
) -> dict[str, Any]:
    """A network decision as the conversation state holds it: made ``decided`` ago, on a
    status observed ``observed`` ago (at the moment of the decision unless said)."""
    return {
        "state": state,
        "reason": reason.value if reason else None,
        "decided_at": (NOW - decided).isoformat(),
        "evidence_at": (NOW - (decided if observed is None else observed)).isoformat(),
        "outage": {"outage_id": outage_id} if outage_id else None,
    }


CLEAR = evidence("CONFIRMED_CLEAR", reason=DecisionReason.LIVE_CLEAR)
ACTIVE = evidence("ACTIVE", outage_id="OUT-1", reason=DecisionReason.LIVE_OUTAGE)
UNKNOWN = evidence("UNKNOWN", reason=DecisionReason.NO_FRESH_EVIDENCE)
LONG_AGO = MAX_AGE + timedelta(seconds=1)


def said(reply: Reply, network: dict[str, Any] | None) -> str | None:
    problem = check(reply, network, now=NOW, max_age=MAX_AGE)
    return problem.code if problem else None


# The messages that say "no outage has been reported", as the workflow really builds them.
SAY_CLEAR = [
    replies.no_outage_reported(REF, evidence_at=NOW),
    replies.line_ok(REF, evidence_at=NOW, tested_at=NOW),
    replies.checks_clear(REF, evidence_at=NOW, tested_at=NOW),
    replies.line_fault_hold(REF, evidence_at=NOW, tested_at=NOW, hold_minutes=10),
    replies.line_fault(REF, tested_at=NOW),
    replies.guided_steps(
        REF,
        guide_title="Tarang FG7 guide",
        section_title="No internet",
        steps=["Check the cable."],
        citation="guide-tg-fg7 v1 #no-internet",
        evidence_at=NOW,
        tested_at=NOW,
    ),
    visit_replies.visit_offer(REF, [SLOT], tested_at=NOW, evidence_at=NOW),
]


@pytest.mark.parametrize("reply", SAY_CLEAR, ids=lambda reply: reply.template_id)
def test_a_message_that_says_no_outage_is_reported_needs_a_clear_network_decided_just_now(
    reply: Reply,
) -> None:
    assert said(reply, CLEAR) is None
    assert said(reply, UNKNOWN) == "CLEAR_NOT_PROVEN"
    assert said(reply, ACTIVE) == "CLEAR_NOT_PROVEN"
    assert said(reply, None) == "CLEAR_NOT_PROVEN"


@pytest.mark.parametrize("reply", SAY_CLEAR[:2], ids=lambda reply: reply.template_id)
def test_a_clear_network_decided_a_while_ago_proves_nothing_now(reply: Reply) -> None:
    just_inside = evidence("CONFIRMED_CLEAR", decided=MAX_AGE)
    too_old = evidence("CONFIRMED_CLEAR", decided=MAX_AGE + timedelta(seconds=1))
    from_the_future = evidence("CONFIRMED_CLEAR", decided=-timedelta(minutes=1))

    assert said(reply, just_inside) is None
    assert said(reply, too_old) == "CLEAR_NOT_PROVEN"
    assert said(reply, from_the_future) == "CLEAR_NOT_PROVEN"
    assert said(reply, {"state": "CONFIRMED_CLEAR"}) == "CLEAR_NOT_PROVEN"  # undated


def test_a_clear_network_decided_now_on_a_status_that_was_already_old_proves_nothing() -> None:
    """Found by review: a decision may rest on an answer up to a minute old, and the message
    may follow the decision by up to a minute. The two ages must not add up."""
    reply = replies.no_outage_reported(REF, evidence_at=NOW)
    old_status = evidence("CONFIRMED_CLEAR", observed=MAX_AGE + timedelta(seconds=1))
    no_status_time = {**CLEAR, "evidence_at": None}

    assert said(reply, old_status) == "CLEAR_NOT_PROVEN"
    assert said(reply, no_status_time) == "CLEAR_NOT_PROVEN"


@pytest.mark.parametrize("disputed", [False, True])
def test_a_message_that_announces_an_outage_needs_that_outage_proven_just_now(
    disputed: bool,
) -> None:
    reply = replies.outage_active(
        REF, OUTAGE, evidence_at=NOW, service_type="broadband", disputed=disputed
    )
    earlier = evidence("ACTIVE", outage_id="OUT-1", decided=LONG_AGO)

    assert said(reply, ACTIVE) is None
    assert said(reply, CLEAR) == "OUTAGE_NOT_PROVEN"
    assert said(reply, UNKNOWN) == "OUTAGE_NOT_PROVEN"
    assert said(reply, None) == "OUTAGE_NOT_PROVEN"
    assert said(reply, evidence("ACTIVE", outage_id="OUT-2")) == "OUTAGE_NOT_PROVEN"
    assert said(reply, earlier) == "OUTAGE_NOT_PROVEN"


RESTORED = replies.outage_restored(
    REF, outage_id="OUT-1", resolved_at=NOW, service_type="broadband"
)


def test_service_restored_needs_the_network_confirmed_clear_just_now() -> None:
    assert said(RESTORED, CLEAR) is None
    assert said(RESTORED, ACTIVE) == "RESTORED_NOT_PROVEN"
    assert said(RESTORED, None) == "RESTORED_NOT_PROVEN"
    assert said(RESTORED, evidence("CONFIRMED_CLEAR", decided=LONG_AGO)) == "RESTORED_NOT_PROVEN"


def test_service_restored_may_rest_on_the_feeds_report_not_on_a_status_nobody_could_read() -> None:
    """Found by review, and the curveball itself: with the Network API down the state is
    unknown. "Your service should be working again" is an all-clear in effect. It is said only
    when the feed is current, carries the resolution and shows nothing against it."""
    reported = evidence("UNKNOWN", reason=DecisionReason.CLEAR_NOT_CONFIRMED)
    no_time_from_the_noc = replies.outage_restored(
        REF, outage_id="OUT-1", resolved_at=None, service_type="broadband"
    )

    assert said(RESTORED, reported) is None
    # Without the NOC's own record of when it ended there is no report to rest on.
    assert said(no_time_from_the_noc, reported) == "RESTORED_NOT_PROVEN"
    assert said(no_time_from_the_noc, CLEAR) is None
    for reason in (
        DecisionReason.NO_FRESH_EVIDENCE,
        DecisionReason.STALE_LAST_KNOWN_OUTAGE,
        DecisionReason.SUSPECTED_NODE_FAULT,
        DecisionReason.CONFLICTING_EVIDENCE,
        DecisionReason.MAPPING_UNRESOLVED,
    ):
        cannot_tell = evidence("UNKNOWN", reason=reason)
        assert said(RESTORED, cannot_tell) == "RESTORED_NOT_PROVEN", reason
    late = evidence("UNKNOWN", reason=DecisionReason.CLEAR_NOT_CONFIRMED, decided=LONG_AGO)
    assert said(RESTORED, late) == "RESTORED_NOT_PROVEN"


@pytest.mark.parametrize(
    "reply",
    [
        visit_replies.visit_being_booked(REF, SLOT),
        visit_replies.visit_choice_unclear(REF, [SLOT]),
    ],
    ids=lambda reply: reply.template_id,
)
def test_word_of_a_technician_being_arranged_needs_a_clear_network_too(reply: Reply) -> None:
    """Found by review: these say nothing of the network in so many words, and promise a
    visit. No visit is arranged on a status that is not proven."""
    assert said(reply, CLEAR) is None
    assert said(reply, UNKNOWN) == "VISIT_NOT_PROVEN"
    assert said(reply, ACTIVE) == "VISIT_NOT_PROVEN"
    assert said(reply, None) == "VISIT_NOT_PROVEN"


@pytest.mark.parametrize(
    "reply",
    [
        replies.network_unknown(REF, DecisionReason.NO_FRESH_EVIDENCE),
        replies.network_unknown(REF, DecisionReason.SUSPECTED_NODE_FAULT),
        replies.network_unknown(
            REF, DecisionReason.STALE_LAST_KNOWN_OUTAGE, last_known_outage=OUTAGE, evidence_at=NOW
        ),
        replies.network_unknown(REF, DecisionReason.MAPPING_UNRESOLVED, service_type="mobile"),
        replies.handed_to_human(REF, "I couldn't reach our account system."),
        replies.need_identifier(REF),
        replies.out_of_scope(REF),
        replies.credit_not_applied(REF),
        replies.closed_quiet(REF),
        visit_replies.visit_booked(REF, SLOT, appointment_id="AP-1"),
        visit_replies.visit_needs_a_person(REF),
        visit_replies.visit_still_being_booked(REF, SLOT),
        # Lists what is free after the chosen slot went, and promises nothing.
        visit_replies.visit_slot_taken(REF, [SLOT]),
    ],
    ids=lambda reply: reply.template_id,
)
@pytest.mark.parametrize(
    "network", [None, CLEAR, ACTIVE, UNKNOWN], ids=["none", "clear", "active", "unknown"]
)
def test_a_message_that_says_nothing_about_the_network_needs_no_proof(
    reply: Reply, network: dict[str, Any] | None
) -> None:
    assert said(reply, network) is None


@pytest.mark.parametrize(
    "text",
    [
        "Good news: no outage has been reported for your area.",
        "No network outage has been reported, so please restart your router.",
        "I've checked and there is no outage in your area.",
        "Everything is all clear on our side.",
        "The network is fine, the problem must be your router.",
        "Our network is working normally.",
        "No outage was reported for your area.",
        "There are no outages in your area.",
        "no outage  has been\nreported",
        "We found no problem on the network.",
        "The network is operating normally.",
    ],
)
def test_all_clear_wording_is_caught_in_a_message_that_declared_no_such_claim(text: str) -> None:
    """The second lock: a template edited to say more than it declares is stopped by its
    words, whatever its declaration says."""
    reply = Reply(text, "handed_to_human.v1")

    assert said(reply, UNKNOWN) == "CLEAR_NOT_PROVEN"
    assert said(reply, ACTIVE) == "CLEAR_NOT_PROVEN"
    assert said(reply, CLEAR) is None


def test_outage_wording_is_caught_in_a_message_that_declared_no_such_claim() -> None:
    reply = Reply("There is a confirmed outage in your area.", "handed_to_human.v1")

    assert said(reply, CLEAR) == "OUTAGE_NOT_PROVEN"
    assert said(reply, ACTIVE) is None


@pytest.mark.parametrize(
    "text",
    ["Service is running.", "Everything looks fine at our end.", "You should be good to go."],
)
def test_the_wording_check_is_a_tripwire_and_these_get_past_it(text: str) -> None:
    """Said plainly, so that nobody takes the list of phrases for a net: there is always
    another way to say that all is well. What stops a template's wording from changing
    unnoticed is the test that pins every template's text (test_template_wording.py)."""
    assert said(Reply(text, "handed_to_human.v1"), UNKNOWN) is None


def test_a_template_nobody_declared_is_not_sent() -> None:
    assert said(Reply("Hello.", "brand_new.v1"), CLEAR) == "UNDECLARED_TEMPLATE"


def test_every_template_the_workflow_can_send_has_its_claim_declared() -> None:
    """A new template must say what it claims before it can be stored."""
    sources = [Path(module.__file__ or "").read_text() for module in (replies, visit_replies)]
    written = set(re.findall(r'"([a-z_]+\.v\d+)"', "\n".join(sources)))

    assert written, "no template ids found: the test looks in the wrong place"
    assert written - set(CLAIMS) == set()
    assert set(CLAIMS) - written == set()


def test_the_degraded_mode_message_claims_nothing_in_any_of_its_wordings() -> None:
    assert CLAIMS["network_unknown.v1"] is Claim.NOTHING
    for reason in DecisionReason:
        reply = replies.network_unknown(
            REF, reason, last_known_outage=OUTAGE, evidence_at=NOW, service_type="broadband"
        )
        assert said(reply, UNKNOWN) is None, reason


def test_only_one_place_in_the_platform_stores_a_message_for_the_customer() -> None:
    """The check is at one door. This makes sure nobody has cut a second one."""
    package = Path(replies.__file__ or "").resolve().parents[1]
    writers = sorted(
        str(path.relative_to(package))
        for path in package.rglob("*.py")
        if re.search(r"direction\s*=\s*[\"\']outbound[\"\']", path.read_text(encoding="utf-8"))
    )

    assert writers == ["orchestrator/outgoing.py"]
