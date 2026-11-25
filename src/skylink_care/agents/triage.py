"""Contact Triage Agent: who is this, which service, and what are they asking for.

It may read the CRM and nothing else. Identity comes from the strongest source available:

1. the authenticated channel (the token says who is signed in),
2. the caller's line, stated by a trusted channel integration,
3. otherwise two things the customer knows: the customer ID and the last four digits of the
   registered mobile number, checked together by the CRM.

A customer ID typed into a message is only a claim. Nothing is looked up, disclosed or attached
to the contact on the strength of a claim, and the answer to a wrong claim is the same whether
or not that ID exists, so the conversation cannot be used to discover accounts. Wrong answers
are counted for the ID that was claimed, across conversations, and too many of them stop the
check for a while (``identity_guard``).

A contact that has been verified as one customer stays theirs. The ID of another customer
offered in it later is not looked at: another account is another conversation.

The language model reads the message for intent only, on masked text, and only once the customer
is identified: before that the platform has no name to mask, so the text stays inside.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum

from skylink_care.adapters.clients import CrmReader
from skylink_care.adapters.models import CustomerProfile, ServiceDto
from skylink_care.adapters.result import ErrorKind, Ok
from skylink_care.agents.identity_guard import IdentityGuard
from skylink_care.db.contacts import Contact
from skylink_care.guardrails.pii import extract_identifiers, mask_pii
from skylink_care.llm.base import CallRecord, TriageReading
from skylink_care.llm.gateway import LLMGateway
from skylink_care.llm.offline import SecondLook, second_look
from skylink_care.observability.audit import Auditor

ACTOR = "agent:triage"
GUARDRAIL = "guardrail:input"
# How a contact is identified once it has been verified earlier in the same conversation.
VERIFIED_EARLIER = "verified_earlier"
_MOBILE_HINTS = re.compile(
    r"mobile|\bsim\b|signal|tower|call drop|calls?\b|\b[45]g\b|data pack", re.IGNORECASE
)
_BROADBAND_HINTS = re.compile(
    r"wi-?fi|router|modem|broadband|fib(?:re|er)|\bont\b|red light|\blos\b|\blan\b",
    re.IGNORECASE,
)
_FOUR_DIGITS = re.compile(r"(?<!\d)\d{4}(?!\d)")


class IdentityStatus(StrEnum):
    IDENTIFIED = "IDENTIFIED"
    NEED_CUSTOMER_ID = "NEED_CUSTOMER_ID"
    NEED_VERIFICATION = "NEED_VERIFICATION"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    CRM_UNAVAILABLE = "CRM_UNAVAILABLE"


AWAITING_DIGITS = frozenset({IdentityStatus.NEED_VERIFICATION, IdentityStatus.VERIFICATION_FAILED})


class _Answer(StrEnum):
    """What became of a customer ID and four digits offered as proof of identity."""

    RIGHT = "right"
    # The CRM knows the customer, and these are not the digits.
    WRONG = "wrong"
    # The CRM knows no such customer. The customer hears the same as for wrong digits.
    NOBODY = "nobody"
    # The CRM could not say.
    UNCHECKED = "unchecked"
    # Not put to the CRM: the claimed ID has had too many wrong answers.
    REFUSED = "refused"


_NOT_VERIFIED = frozenset({_Answer.WRONG, _Answer.NOBODY, _Answer.REFUSED})
# What the CRM answers when the ID is no customer's to verify: unknown, malformed, retired.
_NO_SUCH_CUSTOMER = frozenset(
    {ErrorKind.NOT_FOUND, ErrorKind.INVALID, ErrorKind.GONE, ErrorKind.CONFLICT}
)


@dataclass(frozen=True)
class Identity:
    status: IdentityStatus
    customer_id: str | None = None
    verified: bool = False
    method: str | None = None
    # The answer was not checked: the claimed ID has had too many wrong ones. The customer is
    # told what any wrong answer is told; this is for the person who takes the contact over.
    locked: bool = False


@dataclass(frozen=True)
class Claim:
    """What the customer has told us about who they are. Not yet believed."""

    customer_id: str | None = None
    phone_last4: str | None = None


@dataclass(frozen=True)
class ServiceChoice:
    service_id: str
    service_type: str
    address_id: str
    pincode: str
    router_model: str | None
    monthly_fee_usd: str
    active: bool
    billing_cycle_day: int


@dataclass(frozen=True)
class TriageOutcome:
    identity: Identity
    claim: Claim
    service: ServiceChoice | None
    reading: TriageReading
    call: CallRecord
    # What the keyword rules see in the same message, whoever read it.
    second: SecondLook


class TriageAgent:
    def __init__(
        self,
        crm: CrmReader,
        llm: LLMGateway,
        *,
        behind: Callable[[], bool] | None = None,
        guard: IdentityGuard | None = None,
    ) -> None:
        self._crm = crm
        self._llm = llm
        # Whether the orchestrator is behind (surge mode): messages are then read by the rules.
        self._behind = behind
        # Counts wrong answers to the identity check across conversations. None: not counted.
        self._guard = guard

    async def run(
        self,
        contact: Contact,
        text: str,
        audit: Auditor,
        *,
        claim: Claim | None = None,
        awaiting_digits: bool = False,
    ) -> TriageOutcome:
        identity, profile, claim_after = await self._identify(
            contact, text, claim or Claim(), audit, awaiting_digits=awaiting_digits
        )
        known = personal_values(profile) if profile else {}
        reading, call = await self._llm.read_contact(
            text,
            known_values=known,
            allow_external=profile is not None,
            shed_load=self._behind() if self._behind else False,
            correlation_id=audit.correlation_id,
        )
        masked = mask_pii(text, known_values=known)
        await audit.record(
            actor=ACTOR,
            step="triage.read_contact",
            outcome="DEGRADED" if call.degraded else "OK",
            inputs={
                # The text itself is not copied here: its length and what was masked are enough.
                "text_chars": len(text),
                "pii_masked": list(masked.found),
                "provider_error": call.error,
            },
            outputs={**reading.model_dump(), "identity": identity.status.value},
            call=call,
        )
        second = second_look(text)
        if reading.abusive_or_off_topic or second.injection or second.abuse:
            # Declined politely or handled like any other, a message that was flagged leaves
            # a trace of its own: how often the assistant is being worked on can be counted.
            await audit.record(
                actor=GUARDRAIL,
                step="guardrail.input",
                outcome="INJECTION_SUSPECTED" if second.injection else "ABUSIVE_OR_OFF_TOPIC",
                # Not the text: who read it, and what each reader saw in it.
                inputs={"text_chars": len(text), "read_by": call.provider},
                outputs={
                    "read_as_abusive_or_off_topic": reading.abusive_or_off_topic,
                    "rules_see_injection": second.injection,
                    "rules_see_abuse": second.abuse,
                },
            )
        service = choose_service(profile, text) if profile else None
        return TriageOutcome(
            identity=identity,
            claim=claim_after,
            service=service,
            reading=reading,
            call=call,
            second=second,
        )

    async def _identify(
        self,
        contact: Contact,
        text: str,
        claim: Claim,
        audit: Auditor,
        *,
        awaiting_digits: bool,
    ) -> tuple[Identity, CustomerProfile | None, Claim]:
        known = contact.authenticated_customer_id or contact.customer_id
        if known:
            # Signed in, or verified earlier in this conversation: it is this customer's, and
            # nothing typed into it can make it anyone else's. What was offered as
            # identification is no longer kept either.
            await self._note_other_claims(text, known, audit)
            method = "channel_auth" if contact.authenticated_customer_id else VERIFIED_EARLIER
            identity, profile = await self._load(known, audit, method=method)
            return identity, profile, Claim()
        if contact.caller_phone:
            by_line = await self._by_caller_line(contact.caller_phone, audit)
            if by_line is not None:
                return (*by_line, claim)
        merged = merge_claim(claim, text, awaiting_digits=awaiting_digits)
        return await self._by_knowledge(contact, merged, audit)

    async def _note_other_claims(self, text: str, known: str, audit: Auditor) -> None:
        """The ID of another customer, offered in a contact that already belongs to one. It
        is not checked. That it was offered is recorded, for whoever looks into it later."""
        others = [
            offered
            for offered in extract_identifiers(text).customer_ids
            if offered != known.upper()
        ]
        if others:
            await audit.record(
                actor=ACTOR,
                step="identity.claim_ignored",
                outcome="KEPT",
                inputs={"claimed": others[0]},
                outputs={"kept": known},
            )

    async def _by_caller_line(
        self, phone: str, audit: Auditor
    ) -> tuple[Identity, CustomerProfile | None] | None:
        """The line a trusted channel says the call came from. None when it settles nothing."""
        found = await audit.tool(
            actor=ACTOR,
            tool="crm.lookup",
            inputs={"by": "caller_line"},
            call=self._crm.lookup(correlation_id=audit.correlation_id, phone=phone),
        )
        if not isinstance(found, Ok):
            if found.kind in {ErrorKind.NOT_FOUND, ErrorKind.INVALID}:
                return None
            return Identity(IdentityStatus.CRM_UNAVAILABLE, method="caller_line"), None
        if len(found.value.matches) != 1:
            return None
        return await self._load(found.value.matches[0].customer_id, audit, method="caller_line")

    async def _by_knowledge(
        self, contact: Contact, claim: Claim, audit: Auditor
    ) -> tuple[Identity, CustomerProfile | None, Claim]:
        if claim.customer_id is None:
            return Identity(IdentityStatus.NEED_CUSTOMER_ID), None, claim
        if claim.phone_last4 is None:
            return Identity(IdentityStatus.NEED_VERIFICATION), None, claim
        answer = await self._answer(contact, claim.customer_id, claim.phone_last4, audit)
        if answer in _NOT_VERIFIED:
            # Forget the digits, keep the claimed ID: the customer may only have mistyped them.
            failed = Identity(IdentityStatus.VERIFICATION_FAILED, locked=answer is _Answer.REFUSED)
            return failed, None, replace(claim, phone_last4=None)
        if answer is _Answer.UNCHECKED:
            return Identity(IdentityStatus.CRM_UNAVAILABLE, method="knowledge"), None, claim
        identity, profile = await self._load(claim.customer_id, audit, method="knowledge")
        return identity, profile, claim

    async def _answer(
        self, contact: Contact, customer_id: str, digits: str, audit: Auditor
    ) -> _Answer:
        """Put the answer to the CRM, unless the claimed ID has had too many wrong ones.

        A try is taken before the answer is checked and given back unless it was wrong, so
        that conversations answering at the same moment cannot each be the last one allowed.
        """
        guard = self._guard
        if guard is None:
            return await self._ask_the_crm(customer_id, digits, audit)
        attempt = await guard.take(customer_id, contact.contact_id)
        if attempt is None:
            # Too many wrong answers for this ID of late, here or elsewhere. This one is not
            # put to the CRM, right or wrong, and the customer hears what any wrong one hears.
            await audit.record(
                actor=ACTOR,
                step="identity.locked",
                outcome="REFUSED",
                inputs={"customer_id": customer_id},
                outputs={
                    "limit": guard.limits.per_window,
                    "window_seconds": guard.limits.window_seconds,
                    "limit_in_a_day": guard.limits.per_day,
                },
            )
            await guard.refused(customer_id)
            return _Answer.REFUSED
        try:
            answer = await self._ask_the_crm(customer_id, digits, audit)
        except Exception:
            # A failure of ours is not an answer. The turn is tried again and needs its try.
            await guard.give_back(attempt)
            raise
        if answer in {_Answer.WRONG, _Answer.NOBODY}:
            await guard.wrong(attempt, customer_id, account_known=answer is _Answer.WRONG)
        else:
            # Right, or the CRM could not say: neither counts against the ID.
            await guard.give_back(attempt)
        return answer

    async def _ask_the_crm(self, customer_id: str, digits: str, audit: Auditor) -> _Answer:
        result = await audit.tool(
            actor=ACTOR,
            tool="crm.verify",
            inputs={"factor": "customer_id + phone_last4"},
            call=self._crm.verify(
                customer_id, phone_last4=digits, correlation_id=audit.correlation_id
            ),
        )
        if isinstance(result, Ok):
            return _Answer.RIGHT if result.value.verified else _Answer.WRONG
        # An answer about the ID, as against a call that failed. Only these are counted as
        # wrong: counting a failure of ours would lock customers out by our fault, and not
        # counting an answer would be a way to ask the CRM about an ID for nothing.
        return _Answer.NOBODY if result.kind in _NO_SUCH_CUSTOMER else _Answer.UNCHECKED

    async def _load(
        self, customer_id: str, audit: Auditor, *, method: str
    ) -> tuple[Identity, CustomerProfile | None]:
        result = await audit.tool(
            actor=ACTOR,
            tool="crm.customer",
            inputs={"customer_id": customer_id},
            call=self._crm.customer(customer_id, correlation_id=audit.correlation_id),
        )
        if isinstance(result, Ok):
            identity = Identity(
                IdentityStatus.IDENTIFIED, customer_id, verified=True, method=method
            )
            return identity, result.value
        return Identity(IdentityStatus.CRM_UNAVAILABLE, method=method), None


def personal_values(profile: CustomerProfile) -> dict[str, str]:
    """What the platform knows to be this customer's, to be masked by value before a model
    reads their message (``guardrails.pii.mask_pii`` finds each in the spellings people use).

    The name whole and in each of its parts. The registered mobile, and its last four to nine
    digits: people quote the end of a number, and the last four are half of the identity
    check. The name part of the email address, the numbers of the customer and service ids,
    and where each service is. Whole phone numbers, email addresses and ids are also caught
    by their shape wherever they appear; these are for the spellings a shape misses.
    """
    values = {"NAME": profile.full_name}
    for number, part in enumerate(_words(profile.full_name), start=1):
        values[f"NAME_{number}"] = part
    phone = _digits(profile.phone)[-PHONE_DIGITS:]
    if len(phone) == PHONE_DIGITS:
        values["PHONE"] = phone
        for kept in range(PHONE_DIGITS - 1, LAST_DIGITS_FROM - 1, -1):
            values[f"PHONE_LAST_{kept}"] = phone[-kept:]
    mail_name = profile.email.partition("@")[0]
    if mail_name:
        values["EMAIL_NAME"] = mail_name
    values["CUSTOMER_NUMBER"] = _digits(profile.customer_id)
    for number, service in enumerate(profile.services, start=1):
        values[_numbered("SERVICE_NUMBER", number)] = _digits(service.service_id)
    values |= _places(profile)
    return {label: value for label, value in values.items() if value}


PHONE_DIGITS, LAST_DIGITS_FROM = 10, 4
# A word of an area is masked by itself when it has this many letters ("Bandra" of "Bandra
# West", "BTM" of "BTM Layout"): people shorten a place to one of its words.
AREA_WORD_LETTERS = 2


def _places(profile: CustomerProfile) -> dict[str, str]:
    """Area, city and pincode of every service address, and the longer words of each area."""
    values: dict[str, str] = {}
    seen: dict[str, list[str]] = {"AREA": [], "CITY": [], "PINCODE": [], "AREA_WORD": []}

    def add(kind: str, value: str) -> None:
        if value and value not in seen[kind]:
            seen[kind].append(value)
            values[_numbered(kind, len(seen[kind]))] = value

    for service in profile.services:
        address = service.address
        add("AREA", address.area)
        add("CITY", address.city)
        add("PINCODE", address.pincode)
        for word in _words(address.area):
            if len(word) >= AREA_WORD_LETTERS:
                add("AREA_WORD", word)
    return values


def _numbered(kind: str, number: int) -> str:
    return kind if number == 1 else f"{kind}_{number}"


def _words(text: str) -> list[str]:
    return [word for word in re.split(r"[\W_]+", text) if word]


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def merge_claim(claim: Claim, text: str, *, awaiting_digits: bool) -> Claim:
    """Fold what this message offers as identification into what earlier messages offered."""
    offered = extract_identifiers(text)
    customer_id = offered.customer_ids[0] if offered.customer_ids else claim.customer_id
    last4 = claim.phone_last4
    if offered.customer_ids and offered.customer_ids[0] != claim.customer_id:
        last4 = None  # digits belonged to the earlier claim
    if offered.phones:
        last4 = offered.phones[0][-4:]
    elif awaiting_digits:
        without_ids = text
        for value in offered.customer_ids:
            without_ids = re.sub(re.escape(value), " ", without_ids, flags=re.IGNORECASE)
        digits = _FOUR_DIGITS.findall(without_ids)
        if digits:
            last4 = digits[-1]
    return Claim(customer_id=customer_id, phone_last4=last4)


def choose_service(profile: CustomerProfile, text: str) -> ServiceChoice | None:
    """Pick the service the message is about.

    Active services only. Words in the message decide between broadband and mobile; with no
    hint, broadband is preferred because it is the first slice in scope.
    """
    active = [service for service in profile.services if service.status == "active"]
    candidates = active or list(profile.services)
    if not candidates:
        return None
    by_type = {service.service_type: service for service in reversed(candidates)}
    wants_mobile = bool(_MOBILE_HINTS.search(text)) and not _BROADBAND_HINTS.search(text)
    order = ("mobile", "broadband") if wants_mobile else ("broadband", "mobile")
    chosen = next((by_type[kind] for kind in order if kind in by_type), candidates[0])
    return _to_choice(chosen)


def _to_choice(service: ServiceDto) -> ServiceChoice:
    return ServiceChoice(
        service_id=service.service_id,
        service_type=service.service_type,
        address_id=service.address.address_id,
        pincode=service.address.pincode,
        router_model=service.router_model,
        monthly_fee_usd=str(service.monthly_fee_usd),
        active=service.status == "active",
        billing_cycle_day=service.billing_cycle_day,
    )
