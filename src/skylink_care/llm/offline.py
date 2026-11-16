"""Deterministic triage rules: the provider that needs no network and no key.

It serves three purposes: tests and CI, the fallback when a model call fails or times out, and
the surge path where calling a model for every contact would cost more than it is worth. It is
keyword-based and says so through its confidence: anything it is unsure about is ``low``, which
routes the contact to a person rather than letting a guess stand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from skylink_care.llm.base import TriageReading, Usage

_CONNECTIVITY = (
    r"internet|wi-?fi|broadband|fib(?:re|er)|network|signal|connection|connectivity|router|modem"
    r"|ont\b|outage|offline|no service|not work|isn'?t working|stopped working|down\b|slow"
    r"|does(?:n'?t| not) work|won'?t connect|can(?:'?t|not) connect"
    r"|buffering|disconnect|dropping|drops|red light|los\b|blinking|no range|call drop"
    r"|net nahi|net nahin|nahi chal|band hai|technician|engineer"
    # A phone that cannot call or shows only SOS. Kept to words that name a fault: "mobile
    # data" or "won't open" also turn up in questions about a bill.
    r"|can(?:'?t|not) (?:make|receive|get) (?:any )?calls?|calls? (?:are |is )?not connecting"
    r"|\bsos\b|no coverage"
)
# Pages that do not load. They speak of the connection only when the message is not about a
# bill: "the payment page is not loading" is a question about paying.
_NOTHING_LOADS = r"not loading|nothing loads"
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
# "It works again", and the ways of saying the opposite with the same words.
_WORKING = (
    r"\b(?:working|works|back|fine|restored|fixed|resolved|ok(?:ay)?)"
    r"(?:\s+(?:fine|well|properly|perfectly|up|online))?\s+(?:now|again)\b"
    r"|\b(?:is|it'?s|its|are) (?:back|working|fine|restored|fixed|up and running)"
    r"(?:\s+(?:now|again|fine|well|properly|perfectly|online|thanks?|thank you))*\s*(?:[.!,\n]|$)"
    r"|\bworking fine\b|\ball good\b|\bback online\b|\bchal raha hai\b|\bthik ho gaya\b"
)
_NOT_WORKING = (
    r"\bnot\b|\bcannot\b|\bnever\b"
    r"|\b(?:is|are|was|were|does|do|did|has|have|had|wo|ca|could|would|should)n'?t\b"
    r"|\bno (?:internet|signal|network|service|connection|net)\b|\bstill\b|\bbut\b"
    r"|\bslow\w*|\bdrop\w*|\bintermittent\w*|\bbuffer\w*|\bproblem\w*|\bissue\w*"
    r"|\bnahi\b|\bnahin\b|\bdown\b|\?"
)
_ABUSE = r"\b(?:idiot|stupid|useless|scam(?:mers?)?|fraud(?:s|sters?)?|bastard|damn you)\b"
# Words that address the assistant's own instructions, read with any spacing. They are a
# tripwire for what staff should be able to count, not a wall: a message they miss meets a
# model that has no tool. They are written narrowly, because a message that trips them and
# says nothing else is declined: "the app says you are now offline" must not be.
_INJECTION = (
    r"\b(?:ignore|disregard|forget|override)\s+"
    r"(?:(?:all|any|the|your|my|of|these|those|every)\s+)*"
    r"(?:(?:previous|prior|above|earlier|system)\s+)?(?:instructions?|rules|prompts?|guidelines)\b"
    r"|\bsystem\s+prompt\b"
    r"|\byou\s+are\s+now\s+(?:an?|the|my)\b"
    r"|\b(?:you\s+are|you're|enter|switch\s+to|activate|enable)\s+(?:now\s+)?(?:in\s+|into\s+)?"
    r"(?:developer|debug|maintenance|admin|god)\s+mode\b"
)
# Words that deny or doubt that the service works. Narrower than what makes the rules' own
# reading hold back: a model reads "it's working now, no problem, thanks" rightly, and "no
# problem" must not take that away. What is left is what no "it works again" can be read
# into: a negation, "still", and the names of a fault that goes on.
_DENIES = (
    r"\bnot\b|\bcannot\b|\bnever\b"
    r"|\b(?:is|are|was|were|does|do|did|has|have|had|wo|ca|could|would|should)n'?t\b"
    r"|\bno (?:internet|signal|network|service|connection|net)\b|\bstill\b"
    r"|\bslow\w*|\bdrop\w*|\bintermittent\w*|\bbuffer\w*|\bnahi\b|\bnahin\b"
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
    if billing == 0:
        connectivity += _count(_NOTHING_LOADS, text)
    credit = _has(_CREDIT, text)
    injection = _has(_INJECTION, text)
    off_topic = _has(_ABUSE, text) or injection

    if connectivity == 0 and billing == 0:
        intent, confidence = "other", "low"
    elif connectivity >= billing:
        intent = "connectivity"
        confidence = "high" if connectivity >= 2 and billing == 0 else "medium"
    else:
        intent = "billing"
        confidence = "high" if billing >= 2 and connectivity == 0 else "medium"
    if injection:
        # Words meant to steer the assistant put everything else the message says in doubt.
        # Rudeness does not: an angry customer with a fault has a fault.
        confidence = "low"

    return TriageReading(
        intent=intent,
        confidence=confidence,
        wants_human=_has(_HUMAN, text),
        insists_device_fault=_has(_DEVICE, text),
        requests_technician=_has(_TECHNICIAN, text),
        requests_credit=credit,
        # Deliberately narrow: any doubt, negation or question means "not confirmed".
        confirms_working=_has(_WORKING, text) and not _has(_NOT_WORKING, text),
        abusive_or_off_topic=off_topic,
        summary=_summary(intent, credit),
    )


# This many of the words a fault report is made of, and the message is one in plain words.
PLAIN_FAULT_WORDS = 2


@dataclass(frozen=True)
class SecondLook:
    """What the keyword rules see in a message, kept beside whoever read it.

    A model reads better than keywords do, and a model can be wrong or talked round. Where
    its reading alone would end a conversation (close it, or decline it), these plain facts
    about the customer's own words are asked as well.
    """

    # A fault reported in plain words: two of the words a fault report is made of, and none
    # of a bill. A reading that calls this off-topic goes to a person, and words meant to
    # steer the assistant do not get it declined.
    plain_fault: bool
    # Words of denial ("not", "still", "no internet"). No "it works again" is read into a
    # message that has them.
    denies_working: bool
    # Words meant to steer the assistant ("ignore previous instructions").
    injection: bool
    abuse: bool


def second_look(text: str) -> SecondLook:
    billing = _count(_BILLING, text)
    connectivity = _count(_CONNECTIVITY, text) + (0 if billing else _count(_NOTHING_LOADS, text))
    return SecondLook(
        plain_fault=connectivity >= PLAIN_FAULT_WORDS and billing == 0,
        denies_working=_has(_DENIES, text),
        injection=_has(_INJECTION, text),
        abuse=_has(_ABUSE, text),
    )


def _summary(intent: str, credit: bool) -> str:
    if intent == "connectivity":
        return "Customer reports a service problem" + (
            " and asks for compensation." if credit else "."
        )
    if intent == "billing":
        return "Customer has a billing question."
    return "Customer message did not match a known request type."
