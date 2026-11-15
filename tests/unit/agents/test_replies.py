"""Customer messages: all carry the reference; degraded ones never imply a healthy network."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from skylink_care.agents import replies
from skylink_care.domain.models import OutageInfo
from skylink_care.domain.network import DecisionReason

REF = "SKL-261115-000123-7"
NOW = datetime(2026, 11, 15, 5, 1, tzinfo=UTC)  # 10:31 IST
OUTAGE = OutageInfo(
    outage_id="OUT-20261115-0003",
    version=2,
    cause="FIBRE_CUT",
    planned=False,
    started_at=NOW - timedelta(minutes=51),
    eta=NOW + timedelta(hours=2, minutes=29),
    eta_source="field_update",
    resolved_at=None,
)
# Phrases that would tell a customer the network is fine.
ALL_CLEAR = re.compile(
    r"no outage(?! has been declared yet)|all clear|working normally"
    r"|network is (?:fine|ok|up|healthy)"
    r"|no (?:known )?(?:issue|problem|fault)s? (?:in|with|on)|nothing wrong",
    re.IGNORECASE,
)


def test_outage_reply_states_cause_sourced_eta_verification_time_and_reference() -> None:
    reply = replies.outage_active(REF, OUTAGE, evidence_at=NOW)

    assert "OUT-20261115-0003" in reply.text
    assert "a fibre cut" in reply.text
    assert "expected back by 13:00 IST on 15 Nov (update from the field team)" in reply.text
    assert "verified at 10:31 IST on 15 Nov" in reply.text
    assert REF in reply.text
    assert reply.facts["eta_source"] == "field_update"
    assert reply.facts["outage_version"] == 2


def test_outage_reply_tells_the_customer_not_to_troubleshoot_or_expect_a_visit() -> None:
    text = replies.outage_active(REF, OUTAGE, evidence_at=NOW).text

    assert "don't need to restart or reset your router" in text
    assert "no technician visit is needed" in text


def test_outage_reply_without_an_eta_does_not_invent_one() -> None:
    no_eta = OutageInfo(**{**OUTAGE.__dict__, "eta": None, "eta_source": None})

    text = replies.outage_active(REF, no_eta, evidence_at=NOW).text

    assert "no restoration estimate yet" in text
    assert "expected back" not in text


def test_planned_work_is_described_as_planned() -> None:
    planned = OutageInfo(**{**OUTAGE.__dict__, "cause": "PLANNED_MAINTENANCE", "planned": True})

    text = replies.outage_active(REF, planned, evidence_at=NOW).text

    assert "planned maintenance" in text
    assert "fibre cut" not in text


def test_every_message_discloses_that_it_comes_from_an_automated_assistant() -> None:
    messages = [
        replies.outage_active(REF, OUTAGE, evidence_at=NOW),
        replies.network_unknown(REF, DecisionReason.NO_FRESH_EVIDENCE),
        replies.no_outage_reported(REF, evidence_at=NOW),
        replies.need_identifier(REF),
        replies.need_verification(REF),
        replies.verification_failed(REF),
        replies.handed_to_human(REF, "This needs a billing specialist."),
        replies.out_of_scope(REF),
    ]

    assert all("automated assistant" in message.text for message in messages)
    assert all(REF in message.text for message in messages)


UNKNOWN_REASONS = [
    DecisionReason.MAPPING_UNRESOLVED,
    DecisionReason.SUSPECTED_NODE_FAULT,
    DecisionReason.CONFLICTING_EVIDENCE,
    DecisionReason.CLEAR_NOT_CONFIRMED,
    DecisionReason.STALE_LAST_KNOWN_OUTAGE,
    DecisionReason.NO_FRESH_EVIDENCE,
]


@pytest.mark.parametrize("reason", UNKNOWN_REASONS, ids=lambda reason: reason.value)
def test_degraded_messages_never_imply_the_network_is_healthy(reason: DecisionReason) -> None:
    reply = replies.network_unknown(REF, reason, last_known_outage=OUTAGE, evidence_at=NOW)

    assert ALL_CLEAR.search(reply.text) is None, reply.text
    assert "won't ask you to change anything on your router or book a technician" in reply.text


def test_stale_evidence_message_shows_the_last_verified_outage_and_its_time() -> None:
    reply = replies.network_unknown(
        REF, DecisionReason.STALE_LAST_KNOWN_OUTAGE, last_known_outage=OUTAGE, evidence_at=NOW
    )

    assert "last verified information at 10:31 IST on 15 Nov showed an outage" in reply.text
    assert "OUT-20261115-0003" in reply.text
    assert "can't confirm whether it has ended" in reply.text


def test_suspected_fault_message_says_nothing_is_declared_yet() -> None:
    text = replies.network_unknown(REF, DecisionReason.SUSPECTED_NODE_FAULT).text

    assert "signs of a possible network problem" in text
    assert "No outage has been declared yet" in text


def test_confirmed_clear_message_says_reported_not_absent() -> None:
    text = replies.no_outage_reported(REF, evidence_at=NOW).text

    assert "no outage has been reported for your area as of 10:31 IST on 15 Nov" in text
    assert "there is no outage" not in text.lower()


def test_identifier_request_does_not_disclose_anything() -> None:
    text = replies.need_identifier(REF).text

    assert "customer ID" in text
    assert "outage" not in text.lower()


def test_identity_prompts_reveal_nothing_about_any_account() -> None:
    for reply in (replies.need_verification(REF), replies.verification_failed(REF)):
        lowered = reply.text.lower()
        assert "outage" not in lowered
        assert "found" not in lowered
        assert "exist" not in lowered


def test_outage_reply_does_not_claim_a_ticket_link_it_cannot_guarantee() -> None:
    text = replies.outage_active(REF, OUTAGE, evidence_at=NOW).text

    assert "recorded against this outage" in text
    assert "linked" not in text


def test_restored_reply_reports_what_the_network_team_recorded() -> None:
    reply = replies.outage_restored(
        REF, outage_id=OUTAGE.outage_id, resolved_at=NOW, service_type="broadband"
    )

    assert "resolved the outage in your area (outage OUT-20261115-0003) at 10:31 IST" in reply.text
    assert "restart your router once" in reply.text
    assert "reply here and I'll have your line checked" in reply.text
    assert REF in reply.text
    assert reply.template_id == "outage_restored.v1"
    assert reply.facts == {"outage_id": OUTAGE.outage_id, "resolved_at": NOW.isoformat()}
    # It reports the outage, it does not vouch for this customer's line.
    assert ALL_CLEAR.search(reply.text) is None


def test_restored_reply_without_a_resolution_time_does_not_invent_one() -> None:
    reply = replies.outage_restored(
        REF, outage_id=OUTAGE.outage_id, resolved_at=None, service_type="mobile"
    )

    assert "is no longer active on our network" in reply.text
    assert " at " not in reply.text.split("Your service")[0]
    assert "restart your phone once" in reply.text


def test_restored_reply_without_any_outage_on_record_claims_only_what_was_checked() -> None:
    reply = replies.outage_restored(REF, outage_id=None, resolved_at=None, service_type=None)

    assert "Our network checks no longer show a problem in your area." in reply.text
    assert "outage" not in reply.text.lower().split("your service")[0]


@pytest.mark.parametrize(
    ("build", "phrase", "template_id"),
    [
        (replies.closed_confirmed, "Thank you for confirming", "closed_confirmed.v1"),
        (replies.closed_quiet, "haven't heard back from you", "closed_quiet.v1"),
    ],
)
def test_closing_messages_keep_the_reference_and_say_how_to_come_back(
    build: object, phrase: str, template_id: str
) -> None:
    reply = build(REF)  # type: ignore[operator]

    assert phrase in reply.text
    assert REF in reply.text
    assert "quote this reference" in reply.text
    assert reply.text.startswith(replies.AI_DISCLOSURE)
    assert reply.template_id == template_id


def test_line_ok_reply_names_both_checks_and_their_times() -> None:
    reply = replies.line_ok(REF, evidence_at=NOW, tested_at=NOW + timedelta(seconds=30))

    assert "no outage has been reported for your area as of 10:31 IST" in reply.text
    assert "a remote test of your line at 10:31 IST on 15 Nov passed" in reply.text
    assert REF in reply.text
    assert reply.template_id == "line_ok.v1"


def test_line_fault_hold_reply_promises_a_check_not_a_technician() -> None:
    reply = replies.line_fault_hold(REF, evidence_at=NOW, tested_at=NOW, hold_minutes=10)

    assert "found a fault" in reply.text
    assert "up to 10 minutes" in reply.text
    assert "you don't need to do anything meanwhile" in reply.text
    assert "technician" not in reply.text.lower()
    assert "restart" not in reply.text.lower()
    assert reply.facts["hold_minutes"] == 10


def test_line_fault_reply_says_the_line_was_tested_again() -> None:
    reply = replies.line_fault(REF, tested_at=NOW)

    assert "still there (tested again at 10:31 IST on 15 Nov)" in reply.text
    assert "the line itself needs a repair" in reply.text
    assert REF in reply.text


def test_guided_steps_reply_quotes_the_steps_and_says_where_they_come_from() -> None:
    reply = replies.guided_steps(
        REF,
        guide_title="Aerolink AX1800 troubleshooting guide",
        section_title="Wi-Fi problems",
        steps=["Find the network name.", "Check the password on the label."],
        citation="guide-al-ax18 §wifi v1.1",
        evidence_at=NOW,
        tested_at=NOW,
    )

    assert 'from the Aerolink AX1800 troubleshooting guide, section "Wi-Fi problems":' in reply.text
    assert "\n1. Find the network name.\n2. Check the password on the label.\n" in reply.text
    assert "Source: guide-al-ax18 §wifi v1.1." in reply.text
    assert "Reply here to tell me whether that fixed it" in reply.text
    assert REF in reply.text
    assert reply.facts["citation"] == "guide-al-ax18 §wifi v1.1"


def test_checks_clear_notice_asks_and_does_not_instruct() -> None:
    reply = replies.checks_clear(REF, evidence_at=NOW, tested_at=NOW)

    assert "no outage has been reported for your area as of 10:31 IST" in reply.text
    assert "passed" in reply.text
    assert "If your service is still not working, reply here" in reply.text
    assert "restart" not in reply.text.lower()
    assert "\n1. " not in reply.text
    assert REF in reply.text


def test_credit_sentence_states_amount_policy_and_what_happens_next() -> None:
    waiting = replies.credit_line("120.50", policy_version="v0-placeholder", needs_approval=True)
    direct = replies.credit_line("50.00", policy_version="v0-placeholder", needs_approval=False)

    assert "service credit of INR 120.50" in waiting
    assert "(v0-placeholder)" in waiting
    assert "sent for approval" in waiting
    assert "It is being applied to your account" in direct
    assert "approval" not in direct


@pytest.mark.parametrize(
    ("reason", "phrase"),
    [
        ("BELOW_MINIMUM_DURATION", "shorter than the minimum the policy covers"),
        ("PLANNED_WORK", "planned maintenance that was announced in advance is not covered"),
        ("ALREADY_CREDITED", "a credit for this outage is already on your account"),
        ("CAP_REACHED", "reached the limit for this billing cycle"),
        ("SOMETHING_NEW", "does not meet the conditions of the policy"),
    ],
)
def test_customer_who_asked_is_told_why_no_credit_is_due(reason: str, phrase: str) -> None:
    line = replies.no_credit_line(reason, policy_version="v0-placeholder")

    assert phrase in line
    assert "(v0-placeholder)" in line
    assert "a member of our team will review it" in line
    assert reason not in line  # never the internal code


def test_restored_reply_carries_the_credit_sentence_before_the_reference() -> None:
    line = replies.credit_line("120.50", policy_version="v0-placeholder", needs_approval=True)

    reply = replies.outage_restored(
        REF, outage_id="OUT-1", resolved_at=NOW, service_type="broadband", about_credit=line
    )

    assert reply.text.index("service credit of INR 120.50") < reply.text.index(REF)


@pytest.mark.parametrize(
    ("build", "phrase"),
    [
        (
            lambda: replies.credit_applied(REF, amount_inr="120.50", credit_ref="CR-9"),
            "INR 120.50 for your outage has been applied to your account (credit reference CR-9)",
        ),
        (lambda: replies.credit_already_there(REF), "already on your account"),
        (lambda: replies.credit_not_applied(REF), "has not been applied"),
        (lambda: replies.credit_unconfirmed(REF), "could not confirm that the service credit"),
        (lambda: replies.closing_after_credit(REF), "open until the service credit is settled"),
    ],
)
def test_credit_outcome_messages_carry_the_reference(build: object, phrase: str) -> None:
    reply = build()  # type: ignore[operator]

    assert phrase in reply.text
    assert REF in reply.text
    assert reply.text.startswith(replies.AI_DISCLOSURE)


def test_unconfirmed_credit_is_not_said_to_be_applied_or_not_applied() -> None:
    text = replies.credit_unconfirmed(REF).text

    assert "could not confirm" in text
    assert "has not been applied" not in text
    assert "check your account" in text


def test_credit_already_on_record_is_described_by_its_own_amount() -> None:
    assert "INR 75.00 for this outage is already being processed" in (
        replies.credit_in_progress_line("75.00")
    )
    assert "INR 75.00 for this outage has already been applied" in (
        replies.credit_already_applied_line("75.00")
    )
    for line in (
        replies.CREDIT_WITH_ANOTHER_CONTACT,
        replies.CREDIT_ALREADY_ON_ACCOUNT,
        replies.CREDIT_NOT_APPLIED_EARLIER,
        replies.CREDIT_AMOUNT_REVIEW,
    ):
        assert line.startswith(" ")  # each is added to the message about the restoration
        assert line.endswith(".")
