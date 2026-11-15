"""Deterministic triage rules: the provider that needs no network and no key.

It serves three purposes: tests and CI, the fallback when a model call fails or times out, and
the surge path where calling a model for every contact would cost more than it is worth. It is
keyword-based and says so through its confidence: anything it is unsure about is ``low``, which
routes the contact to a person rather than letting a guess stand.
"""

from __future__ import annotations

import re

from skylink_care.llm.base import TriageReading, Usage

_CONNECTIVITY = (
    r"internet|wi-?fi|broadband|fib(?:re|er)|network|signal|connection|connectivity|router|modem"
    r"|ont\b|outage|offline|no service|not work|isn'?t working|stopped working|down\b|slow"
    r"|does(?:n'?t| not) work|won'?t connect|can(?:'?t|not) connect"
    r"|buffering|disconnect|dropping|drops|red light|los\b|blinking|no range|call drop"
    r"|net nahi|net nahin|nahi chal|band hai|technician|engineer"
)
_BILLING = (
    r"bill\b|bills\b|billing|invoice|charge[sd]?\b|overcharg|payment|paid\b|pay\b|refund"
    r"|recharge|tariff|plan change|upgrade my plan|due date|late fee|receipt|auto-?debit"
)
_HUMAN = (
    r"human|real person|agent\b|representative|executive|supervisor|manager"
    r"|speak to (?:some|any)one|talk to (?:some|any)one"
)
_DEVICE = (
    r"(?:my|the) (?:router|modem|device|ont) (?:is|has|seems) (?:faulty|broken|dead|defective)"
    r"|(?:it'?s|it is|problem is|issue is) (?:with )?(?:my|the) (?:router|modem|device)"
    r"|not an outage|no outage|nothing to do with (?:an? )?outage|only my (?:connection|house|home)"
    r"|replace (?:my|the) (?:router|modem)"
)
_TECHNICIAN = r"technician|engineer|send (?:some|any)one|home visit|site visit|field visit"
_CREDIT = (
    r"refund|credit|compensat|rebate|waive|money back|reimburse"
    r"|adjust(?:ment)? (?:in|on) (?:my )?bill"
)
_ABUSE = r"\b(?:idiot|stupid|useless|scam(?:mers?)?|fraud(?:s|sters?)?|bastard|damn you)\b"
_INJECTION = (
    r"ignore (?:all |any |the )?(?:previous|prior|above) (?:instructions|rules)"
    r"|system prompt|you are now|developer mode|jailbreak"
    r"|disregard (?:your|the) (?:rules|instructions)"
)


def _count(pattern: str, text: str) -> int:
    return len(re.findall(pattern, text, flags=re.IGNORECASE))


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


class OfflineProvider:
    name = "offline"
    model = "rules-v1"

    async def read_contact(
        self, masked_text: str, *, system_prompt: str = ""
    ) -> tuple[TriageReading, Usage]:
        return read_with_rules(masked_text), Usage()


def read_with_rules(text: str) -> TriageReading:
    """Classify one message with keyword rules. Pure and deterministic."""
    connectivity = _count(_CONNECTIVITY, text)
    billing = _count(_BILLING, text)
    credit = _has(_CREDIT, text)
    off_topic = _has(_ABUSE, text) or _has(_INJECTION, text)

    if connectivity == 0 and billing == 0:
        intent, confidence = "other", "low"
    elif connectivity >= billing:
        intent = "connectivity"
        confidence = "high" if connectivity >= 2 and billing == 0 else "medium"
    else:
        intent = "billing"
        confidence = "high" if billing >= 2 and connectivity == 0 else "medium"
    if off_topic:
        confidence = "low"

    return TriageReading(
        intent=intent,
        confidence=confidence,
        wants_human=_has(_HUMAN, text),
        insists_device_fault=_has(_DEVICE, text),
        requests_technician=_has(_TECHNICIAN, text),
        requests_credit=credit,
        abusive_or_off_topic=off_topic,
        summary=_summary(intent, credit),
    )


def _summary(intent: str, credit: bool) -> str:
    if intent == "connectivity":
        return "Customer reports a service problem" + (
            " and asks for compensation." if credit else "."
        )
    if intent == "billing":
        return "Customer has a billing question."
    return "Customer message did not match a known request type."
