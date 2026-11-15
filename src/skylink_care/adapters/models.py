"""Wire models for the OSS/BSS gateway (see ``docs/contracts/mock_oss_api.md``).

These describe what the gateway sends, nothing more. They are validated on arrival: an answer
that breaks the contract becomes a ``PROTOCOL`` error instead of travelling further. Unknown
fields are ignored so that an additive change on the other side does not break the platform.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class Wire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


# --- Network Monitoring ------------------------------------------------------------------------


class OutageDto(Wire):
    outage_id: str
    version: int = Field(ge=1)
    cause: str
    planned: bool
    service_types: tuple[str, ...] = ()
    started_at: AwareDatetime
    eta: AwareDatetime | None = None
    eta_source: str | None = None
    resolved_at: AwareDatetime | None = None


class AlarmDto(Wire):
    alarm_id: str
    node_id: str
    alarm_type: str
    severity: Literal["critical", "major", "minor"]
    raised_at: AwareDatetime


class NodeStatusDto(Wire):
    node_id: str
    status: Literal["OUTAGE", "OPERATIONAL"]
    outage: OutageDto | None = None
    active_alarms: tuple[AlarmDto, ...] = ()
    observed_at: AwareDatetime
    feed_seq: int = Field(ge=0)

    @model_validator(mode="after")
    def _status_and_outage_agree(self) -> Self:
        if self.status == "OUTAGE" and (self.outage is None or self.outage.resolved_at):
            raise ValueError("status OUTAGE requires an open outage")
        if self.status == "OPERATIONAL" and self.outage is not None:
            raise ValueError("status OPERATIONAL must not carry an outage")
        return self

    @property
    def has_critical_alarm(self) -> bool:
        return any(alarm.severity == "critical" for alarm in self.active_alarms)


class LineChecks(Wire):
    link: Literal["UP", "DOWN"]
    optical_rx_dbm: float | None = None
    sync: bool
    packet_loss_pct: float


class LineTestDto(Wire):
    test_id: str
    service_id: str
    # The node that actually serves the line. Used to repair a defective mapping.
    node_id: str
    result: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    checks: LineChecks
    tested_at: AwareDatetime


class NetworkEventDto(Wire):
    seq: int = Field(ge=1)
    event_id: str
    type: Literal[
        "OUTAGE_STARTED", "OUTAGE_UPDATED", "OUTAGE_RESOLVED", "ALARM_RAISED", "ALARM_CLEARED"
    ]
    emitted_at: AwareDatetime
    node_ids: tuple[str, ...]
    outage_id: str | None = None
    version: int | None = None
    service_types: tuple[str, ...] = ()
    cause: str | None = None
    planned: bool | None = None
    started_at: AwareDatetime | None = None
    eta: AwareDatetime | None = None
    eta_source: str | None = None
    resolved_at: AwareDatetime | None = None
    alarm_id: str | None = None
    alarm_type: str | None = None
    severity: str | None = None


class EventPage(Wire):
    events: tuple[NetworkEventDto, ...]
    last_seq: int = Field(ge=0)
    # Feed heartbeat: when the gateway produced this page.
    generated_at: AwareDatetime


# --- CRM ---------------------------------------------------------------------------------------


class CustomerMatch(Wire):
    customer_id: str
    name_masked: str
    phone_masked: str
    status: str


class LookupResult(Wire):
    matches: tuple[CustomerMatch, ...]


class ConsentDto(Wire):
    outage_notifications: bool
    channel: Literal["sms", "push", "none"]
    updated_at: AwareDatetime | None = None


class AddressDto(Wire):
    address_id: str
    area: str
    city: str
    pincode: str


class ServiceDto(Wire):
    service_id: str
    service_type: Literal["broadband", "mobile"]
    plan_name: str
    monthly_fee_inr: Decimal = Field(gt=0)
    status: str
    address: AddressDto
    router_model: str | None = None
    activated_on: date | None = None
    billing_cycle_day: int = Field(ge=1, le=28)


class CustomerProfile(Wire):
    customer_id: str
    full_name: str
    phone: str
    email: str
    segment: str
    status: str
    consent: ConsentDto
    services: tuple[ServiceDto, ...]


class VerifyResult(Wire):
    verified: bool


# --- Billing -----------------------------------------------------------------------------------


class CreditDto(Wire):
    credit_id: str
    customer_id: str
    service_id: str
    amount_inr: Decimal
    reason_code: str
    outage_id: str | None = None
    policy_version: str
    complaint_ref: str | None = None
    approved_by: str | None = None
    status: str
    created_at: AwareDatetime
    idempotency_key: str | None = None


class CreditList(Wire):
    credits: tuple[CreditDto, ...]


# --- Field Service -----------------------------------------------------------------------------


class SlotDto(Wire):
    slot_id: str
    start: AwareDatetime
    end: AwareDatetime
    remaining: int = Field(ge=0)


class SlotList(Wire):
    slots: tuple[SlotDto, ...]


class AppointmentDto(Wire):
    appointment_id: str
    slot_id: str
    start: AwareDatetime
    end: AwareDatetime
    status: str
    service_id: str | None = None
    customer_id: str | None = None


class AppointmentList(Wire):
    appointments: tuple[AppointmentDto, ...]


# --- Incident Management -----------------------------------------------------------------------


class IncidentDto(Wire):
    incident_id: str
    status: str
    node_id: str | None = None
    service_type: str | None = None
    kind: str | None = None
    outage_id: str | None = None
    complaint_refs: tuple[str, ...] = ()


class IncidentList(Wire):
    incidents: tuple[IncidentDto, ...]


class ComplaintRegistration(Wire):
    complaint_ref: str
    registered_at: AwareDatetime


class Acknowledged(Wire):
    """A write whose response body carries nothing the platform needs beyond success."""

    model_config = ConfigDict(frozen=True, extra="allow")

    def as_dict(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


# --- Notification gateway ----------------------------------------------------------------------


class MessageReceipt(Wire):
    message_id: str
    status: str
    created_at: AwareDatetime | None = None
