"""Pydantic request models. Response bodies are built by small pure functions per router."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from mock_oss.chaos import Mode, Target

Text = Annotated[str, StringConstraints(min_length=1, max_length=200)]
LongText = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
ServiceType = Literal["broadband", "mobile"]
IncidentKind = Literal["NETWORK_OUTAGE", "SUSPECTED_NODE_FAULT"]
IncidentStatus = Literal["OPEN", "RESOLVED"]
Cause = Literal[
    "FIBRE_CUT", "POWER_FAILURE", "EQUIPMENT_FAULT", "TOWER_FAULT", "PLANNED_MAINTENANCE"
]
EtaSource = Literal["noc_estimate", "field_update"]
AlarmType = Literal["LOS", "LINK_DOWN", "HIGH_BER", "POWER", "HIGH_TEMP"]
Severity = Literal["critical", "major", "minor"]


class LineTestIn(BaseModel):
    service_id: Text


class VerifyIn(BaseModel):
    phone_last4: Annotated[str, StringConstraints(pattern=r"^\d{4}$")]


class CreditIn(BaseModel):
    customer_id: Text
    service_id: Text
    amount_inr: Annotated[Decimal, Field(allow_inf_nan=True)]
    reason_code: Text
    outage_id: Text | None = None
    policy_version: Text
    complaint_ref: Text
    approved_by: Text | None = None


class AppointmentIn(BaseModel):
    customer_id: Text
    service_id: Text
    slot_id: Text
    reason: LongText
    complaint_ref: Text | None = None


class IncidentIn(BaseModel):
    node_id: Text
    service_type: ServiceType
    kind: IncidentKind
    outage_id: Text | None = None
    summary: LongText


class IncidentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: IncidentKind | None = None
    outage_id: Text | None = None
    status: IncidentStatus | None = None
    summary: LongText | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("body must contain at least one of kind, outage_id, status, summary")
        for name in ("kind", "status", "summary"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class ComplaintLinkIn(BaseModel):
    complaint_ref: Text
    contact_id: Text
    customer_id: Text | None = None


class ComplaintIn(BaseModel):
    complaint_ref: Text
    received_at: AwareDatetime
    channel: Text
    category: Text
    customer_id: Text | None = None


class MessageIn(BaseModel):
    customer_id: Text
    # "preferred" asks the gateway to use the channel on the customer's consent record.
    channel: Literal["sms", "push", "preferred"]
    template_id: Text
    body: LongText
    outage_id: Text | None = None


class OutageIn(BaseModel):
    node_id: Text
    cause: Cause
    planned: bool = False
    eta_minutes: Annotated[int, Field(ge=0, le=60 * 24 * 30)] = 180
    detect_after_seconds: Annotated[float, Field(ge=0, le=86400)] = 0


class OutageUpdateIn(BaseModel):
    eta_minutes: Annotated[int, Field(ge=0, le=60 * 24 * 30)]
    eta_source: EtaSource = "field_update"


class LineFaultIn(BaseModel):
    service_id: Text
    fault: bool
    result: Literal["FAIL", "INCONCLUSIVE"] = "FAIL"


class ConsentIn(BaseModel):
    customer_id: Text
    outage_notifications: bool
    channel: Literal["sms", "push"] | None = None


class ChaosIn(BaseModel):
    target: Target
    mode: Mode
    latency_ms: Annotated[int, Field(ge=0, le=120_000)] = 0
    error_rate: Annotated[float, Field(ge=0, le=1)] = 0.0
    hold_seconds: Annotated[float, Field(gt=0, le=600)] = 30.0
    ttl_seconds: Annotated[float, Field(gt=0)] | None = None


class AlarmIn(BaseModel):
    node_id: Text
    alarm_type: AlarmType
    severity: Severity
