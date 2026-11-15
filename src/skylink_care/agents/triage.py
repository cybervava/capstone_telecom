"""Contact Triage Agent: who is this, which service, and what are they asking for.

It may read the CRM and nothing else. Identity comes from the strongest source available:

1. the authenticated channel (the token says who is signed in),
2. the caller's line, stated by a trusted channel integration,
3. otherwise two things the customer knows: the customer ID and the last four digits of the
   registered mobile number, checked together by the CRM.

A customer ID typed into a message is only a claim. Nothing is looked up, disclosed or attached
to the contact on the strength of a claim, and the answer to a wrong claim is the same whether
or not that ID exists, so the conversation cannot be used to discover accounts.

The language model reads the message for intent only, on masked text, and only once the customer
is identified: before that the platform has no name to mask, so the text stays inside.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import StrEnum

from skylink_care.adapters.clients import CrmReader
from skylink_care.adapters.models import CustomerProfile, ServiceDto
from skylink_care.adapters.result import ErrorKind, Ok
from skylink_care.db.contacts import Contact
from skylink_care.guardrails.pii import extract_identifiers, mask_pii
from skylink_care.llm.base import CallRecord, TriageReading
from skylink_care.llm.gateway import LLMGateway
from skylink_care.observability.audit import Auditor

ACTOR = "agent:triage"
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


@dataclass(frozen=True)
class Identity:
    status: IdentityStatus
    customer_id: str | None = None
    verified: bool = False
    method: str | None = None


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
    monthly_fee_inr: str
    active: bool
    billing_cycle_day: int


@dataclass(frozen=True)
class TriageOutcome:
    identity: Identity
    claim: Claim
    service: ServiceChoice | None
    reading: TriageReading
    call: CallRecord


class TriageAgent:
    def __init__(self, crm: CrmReader, llm: LLMGateway) -> None:
        self._crm = crm
        self._llm = llm

    async def run(
        self,
        contact: Contact,
        text: str,
        audit: Auditor,
        *,
        claim: Claim | None = None,
        awaiting_digits: bool = False,
    ) -> TriageOutcome:
        merged = merge_claim(claim or Claim(), text, awaiting_digits=awaiting_digits)
        identity, profile, claim_after = await self._identify(contact, merged, audit)
        known = {"NAME": profile.full_name} if profile else {}
        reading, call = await self._llm.read_contact(
            text,
            known_values=known,
            allow_external=profile is not None,
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
        service = choose_service(profile, text) if profile else None
        return TriageOutcome(
            identity=identity, claim=claim_after, service=service, reading=reading, call=call
        )

    async def _identify(
        self, contact: Contact, claim: Claim, audit: Auditor
    ) -> tuple[Identity, CustomerProfile | None, Claim]:
        if contact.authenticated_customer_id:
            identity, profile = await self._load(
                contact.authenticated_customer_id, audit, method="channel_auth"
            )
            return identity, profile, claim
        if contact.caller_phone:
            by_line = await self._by_caller_line(contact.caller_phone, audit)
            if by_line is not None:
                return (*by_line, claim)
        return await self._by_knowledge(claim, audit)

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
        self, claim: Claim, audit: Auditor
    ) -> tuple[Identity, CustomerProfile | None, Claim]:
        if claim.customer_id is None:
            return Identity(IdentityStatus.NEED_CUSTOMER_ID), None, claim
        if claim.phone_last4 is None:
            return Identity(IdentityStatus.NEED_VERIFICATION), None, claim
        result = await audit.tool(
            actor=ACTOR,
            tool="crm.verify",
            inputs={"factor": "customer_id + phone_last4"},
            call=self._crm.verify(
                claim.customer_id,
                phone_last4=claim.phone_last4,
                correlation_id=audit.correlation_id,
            ),
        )
        rejected = isinstance(result, Ok) and not result.value.verified
        unknown_account = not isinstance(result, Ok) and result.kind in {
            ErrorKind.NOT_FOUND,
            ErrorKind.INVALID,
        }
        if rejected or unknown_account:
            # Forget the digits, keep the claimed ID: the customer may only have mistyped them.
            return (
                Identity(IdentityStatus.VERIFICATION_FAILED),
                None,
                replace(claim, phone_last4=None),
            )
        if not isinstance(result, Ok):
            return Identity(IdentityStatus.CRM_UNAVAILABLE, method="knowledge"), None, claim
        identity, profile = await self._load(claim.customer_id, audit, method="knowledge")
        return identity, profile, claim

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
        monthly_fee_inr=str(service.monthly_fee_inr),
        active=service.status == "active",
        billing_cycle_day=service.billing_cycle_day,
    )
