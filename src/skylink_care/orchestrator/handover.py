"""What a person is given when the workflow hands a contact over, and what the customer is told.

Summaries are built from structured state only. Free customer text never goes into them: the one
model-written sentence that does is cleaned first.
"""

from __future__ import annotations

from skylink_care.guardrails.pii import redact_for_log
from skylink_care.orchestrator.state import ContactState

MAX_SUMMARY_CHARS = 240
# The state of a credit whose outcome could not be established.
UNCONFIRMED = "UNCONFIRMED"

_WORDING = {
    "CUSTOMER_REQUESTED_HUMAN": "You asked to speak to a person.",
    "BILLING": "Billing questions are handled by our billing team.",
    "CRM_UNAVAILABLE": "I couldn't reach our account system to verify your details.",
    "IDENTITY_UNRESOLVED": "I couldn't match the details you gave to an account.",
    "LOW_CONFIDENCE": "I want to be sure this is handled correctly.",
    "NO_SERVICE_ON_ACCOUNT": "I couldn't find a service on the account to check.",
    "TROUBLESHOOTING": "Your line needs to be checked.",
    "LINE_FAULT": "Your line needs a repair.",
    "LINE_TEST_UNAVAILABLE": (
        "A test found a fault on your line earlier and I couldn't test it again to confirm."
    ),
    "NETWORK_STATUS_UNVERIFIED": "I still can't verify the network status for your area.",
    "CUSTOMER_DISPUTES_OUTAGE": (
        "You've told me again that the fault is with your own equipment, and I can't settle"
        " that here while the outage in your area is active."
    ),
    "VISIT_OFFER_UNANSWERED": (
        "Your line needs a repair and I haven't heard back from you about a technician visit."
    ),
}
_DEFAULT_WORDING = "This needs a member of our team."
# For a write whose replaying stopped without an answer: it may have landed.
CHECK_ACCOUNT_FIRST = (
    "Whether it reached the account is not known: look for a credit under this key on the"
    " account before applying anything by hand."
)
_FLAGS = ("insists_device_fault", "requests_technician", "requests_credit")


def wording_for(reason: str) -> str:
    """Why the contact is being handed over, in words for the customer."""
    return _WORDING.get(reason, _DEFAULT_WORDING)


def clean_summary(text: str) -> str:
    """Make a model-written summary safe to store and to show to staff.

    The prompt asks for a neutral sentence without personal data, but a prompt is not a control:
    contact details are masked again here, markup and control characters are removed, and the
    length is capped.
    """
    redacted = str(redact_for_log(text))
    printable = "".join(char if char.isprintable() else " " for char in redacted)
    without_markup = printable.replace("<", "(").replace(">", ")")
    return " ".join(without_markup.split())[:MAX_SUMMARY_CHARS]


def _credit_lines(state: ContactState) -> list[str]:
    """Every credit of the contact and where it stands."""
    lines = []
    for credit in state.get("credits") or []:
        lines.append(
            f"Service credit: ${credit.get('amount_usd')} ({credit.get('action_key')}),"
            f" {credit.get('status') or 'pending'}, approval {credit.get('approval')}."
        )
        if credit.get("status") == UNCONFIRMED:
            lines.append(CHECK_ACCOUNT_FIRST)
    return lines


def handoff_summary(state: ContactState, reason: str) -> str:
    """What a person needs to take over."""
    intent = state.get("intent") or {}
    identity = state.get("identity") or {}
    network = state.get("network") or {}
    service = state.get("service") or {}
    outage = network.get("outage") or network.get("last_known_outage") or {}
    parts = [
        f"Reason: {reason}.",
        f"Request: {intent.get('summary', 'not read')} (intent {intent.get('intent', '?')},"
        f" confidence {intent.get('confidence', '?')}).",
        f"Identity: {identity.get('status', 'not attempted')}"
        f" via {identity.get('method') or 'n/a'},"
        f" {'verified' if identity.get('verified') else 'not verified'}.",
    ]
    seen = intent.get("rules") or {}
    if intent.get("intent") == "other" and seen.get("plain_fault"):
        parts.append(
            "The message was read as no request of ours, but its plain words report a fault:"
            " read what the customer wrote."
        )
    if identity.get("locked"):
        claimed = (state.get("claim") or {}).get("customer_id") or "that was claimed"
        parts.append(
            f"The identity check for customer ID {claimed} is locked after repeated wrong"
            " answers, here or in other conversations, so the last answer given here was not"
            " checked (operations queue: IDENTITY_ATTEMPTS). Verify the customer another way."
        )
    if service:
        parts.append(
            f"Service: {service.get('service_id')} ({service.get('service_type')}),"
            f" router {service.get('router_model') or 'n/a'}."
        )
    if network:
        parts.append(
            f"Network: {network.get('state')} ({network.get('reason')}),"
            f" node {network.get('node_id') or 'unresolved'},"
            f" outage {outage.get('outage_id') or 'none'},"
            f" evidence at {network.get('evidence_at') or 'n/a'}."
        )
    line = state.get("line_test") or {}
    if line:
        parts.append(
            f"Line test: {line.get('result')} at {line.get('tested_at')}"
            f" (serving node {line.get('node_id')})."
        )
    mapping_status = (state.get("mapping") or {}).get("status")
    if mapping_status and mapping_status != "RESOLVED":
        parts.append(f"Address mapping: {mapping_status}.")
    parts.extend(_credit_lines(state))
    visit = state.get("visit") or {}
    if visit:
        slot = visit.get("slot") or visit.get("chosen") or {}
        parts.append(
            f"Technician visit: {visit.get('status')},"
            f" slot {slot.get('slot_id') or 'none chosen'},"
            f" appointment {visit.get('appointment_id') or 'none'}."
        )
    if state.get("guidance_told"):
        parts.append(f"Guide steps already given: {state['guidance_told']}.")
    if state.get("line_fault_seen_at"):
        parts.append(f"Line fault first seen at {state['line_fault_seen_at']}; held since.")
    rechecks = state.get("network_rechecks", 0)
    if rechecks and reason == "NETWORK_STATUS_UNVERIFIED":
        parts.append(f"Network re-checked {rechecks} time(s) without a verified status.")
    flags = [name for name in _FLAGS if intent.get(name)]
    if flags:
        parts.append(f"Customer flags: {', '.join(flags)}.")
    return " ".join(parts)
