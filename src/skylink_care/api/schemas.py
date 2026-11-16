"""Request and response shapes of the care API. All input is validated here, at the boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

RequestId = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9_.:\-]{8,128}$", strip_whitespace=True)
]
MessageText = Annotated[
    str, StringConstraints(min_length=1, max_length=20_000, strip_whitespace=True)
]
Phone = Annotated[str, StringConstraints(pattern=r"^\+?\d{10,13}$", strip_whitespace=True)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContactIn(_In):
    channel: Literal["chat", "call_transcript"]
    # Chosen by the channel for one customer request; a retry must reuse it.
    client_request_id: RequestId
    text: MessageText
    session_id: Annotated[str, StringConstraints(max_length=128)] | None = None
    # Caller line from telephony metadata. Only a trusted channel integration may set it.
    caller_phone: Phone | None = None


class MessageIn(_In):
    client_message_id: RequestId
    text: MessageText


class TokenIn(_In):
    role: Literal["guest", "customer", "channel", "agent", "supervisor", "ops"]
    customer_id: Annotated[str, StringConstraints(pattern=r"^C\d{7}$")] | None = None
    # Staff roles only: the local staff key, and a handle that says who is signing in.
    staff_key: Annotated[str, StringConstraints(max_length=200)] | None = None
    name: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9.-]{1,39}$")] | None = None


class HandoffDoneIn(_In):
    note: Annotated[str, StringConstraints(max_length=500, strip_whitespace=True)] | None = None


class ContactAccepted(BaseModel):
    contact_id: UUID
    complaint_ref: str
    status: str
    received_at: datetime
    reference_issued_at: datetime
    replayed: bool


class MessageView(BaseModel):
    message_id: int
    direction: str
    author: str
    body: str
    created_at: datetime
    template_id: str | None = None


class ContactView(BaseModel):
    contact_id: UUID
    complaint_ref: str
    status: str
    channel: str
    received_at: datetime
    reference_issued_at: datetime
    # What the last look at the network found, and when. "None reported" is given only while
    # that look is recent; after that the state reads as not verified.
    network_state: str | None
    network_checked_at: datetime | None
    outage_id: str | None
    messages: list[MessageView]


class Envelope(BaseModel):
    """Every response uses this shape: ``data`` on success, ``error`` otherwise."""

    success: bool
    data: Any = None
    error: dict[str, Any] | None = None


def ok(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def failed(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"success": False, "data": None, "error": {"code": code, "message": message, **extra}}
