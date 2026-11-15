"""What a person is given when the workflow hands a contact over, and what the customer is told.

Summaries are built from structured state only. Free customer text never goes into them: the one
model-written sentence that does is cleaned first.
"""

from __future__ import annotations

from skylink_care.guardrails.pii import redact_for_log
from skylink_care.orchestrator.state import ContactState

MAX_SUMMARY_CHARS = 240

_WORDING = {
    "CUSTOMER_REQUESTED_HUMAN": "You asked to speak to a person.",
    "BILLING": "Billing questions are handled by our billing team.",
    "CRM_UNAVAILABLE": "I couldn't reach our account system to verify your details.",
    "IDENTITY_UNRESOLVED": "I couldn't match the details you gave to an account.",
    "LOW_CONFIDENCE": "I want to be sure this is handled correctly.",
    "NO_SERVICE_ON_ACCOUNT": "I couldn't find a service on the account to check.",
    "TROUBLESHOOTING": "Your line needs to be checked.",
    "NETWORK_STATUS_UNVERIFIED": "I still can't verify the network status for your area.",
}
_DEFAULT_WORDING = "This needs a member of our team."
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
    rechecks = state.get("network_rechecks", 0)
    if rechecks:
        parts.append(f"Network re-checked {rechecks} time(s) without a verified status.")
    flags = [name for name in _FLAGS if intent.get(name)]
    if flags:
        parts.append(f"Customer flags: {', '.join(flags)}.")
    return " ".join(parts)
