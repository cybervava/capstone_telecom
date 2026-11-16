"""Typed clients for the six enterprise APIs behind the OSS/BSS gateway.

Read methods and write methods are kept on separate classes on purpose. The orchestrator is only
ever handed readers; the classes ending in ``Writer`` are constructed in the executor process
alone, with the executor's key. Every write takes its request body ready-made, because the action
ledger froze it at proposal time and it must be sent unchanged.
"""

from __future__ import annotations

from typing import Any

from skylink_care.adapters.http import OssHttp, segment
from skylink_care.adapters.models import (
    Acknowledged,
    AppointmentDto,
    AppointmentList,
    ComplaintRegistration,
    ConsentDto,
    CreditDto,
    CreditList,
    CustomerProfile,
    EventPage,
    IncidentDto,
    IncidentList,
    LineTestDto,
    LookupResult,
    MessageReceipt,
    NodeStatusDto,
    SlotList,
    VerifyResult,
)
from skylink_care.adapters.result import Result

# A line test exercises real equipment and is slower than a lookup.
LINE_TEST_TIMEOUT_SECONDS = 6.0
# Head-room on top of the long-poll wait before the feed call is declared timed out.
FEED_TIMEOUT_MARGIN_SECONDS = 5.0


class _Client:
    def __init__(self, http: OssHttp) -> None:
        self._http = http


class NetworkReader(_Client):
    async def node_status(self, node_id: str, *, correlation_id: str) -> Result[NodeStatusDto]:
        return await self._http.call(
            "GET",
            f"/network/v1/nodes/{segment(node_id)}/status",
            NodeStatusDto,
            correlation_id=correlation_id,
        )

    async def line_test(self, service_id: str, *, correlation_id: str) -> Result[LineTestDto]:
        return await self._http.call(
            "POST",
            "/network/v1/line-tests",
            LineTestDto,
            correlation_id=correlation_id,
            json={"service_id": service_id},
            timeout_seconds=LINE_TEST_TIMEOUT_SECONDS,
        )


class NetworkFeedReader(_Client):
    async def events(
        self, *, after_seq: int, limit: int = 200, wait_seconds: int = 0, correlation_id: str
    ) -> Result[EventPage]:
        return await self._http.call(
            "GET",
            "/network/v1/events",
            EventPage,
            correlation_id=correlation_id,
            params={"after_seq": after_seq, "limit": limit, "wait_seconds": wait_seconds},
            timeout_seconds=wait_seconds + FEED_TIMEOUT_MARGIN_SECONDS,
            retry=False,  # the consumer polls again by itself
        )


class CrmReader(_Client):
    async def lookup(
        self,
        *,
        correlation_id: str,
        phone: str | None = None,
        customer_id: str | None = None,
        email: str | None = None,
    ) -> Result[LookupResult]:
        given = {"phone": phone, "customer_id": customer_id, "email": email}
        params = {name: value for name, value in given.items() if value}
        if len(params) != 1:
            raise ValueError("lookup needs exactly one of phone, customer_id or email")
        return await self._http.call(
            "GET",
            "/crm/v1/customers/lookup",
            LookupResult,
            correlation_id=correlation_id,
            params=params,
        )

    async def customer(self, customer_id: str, *, correlation_id: str) -> Result[CustomerProfile]:
        return await self._http.call(
            "GET",
            f"/crm/v1/customers/{segment(customer_id)}",
            CustomerProfile,
            correlation_id=correlation_id,
        )

    async def consent(self, customer_id: str, *, correlation_id: str) -> Result[ConsentDto]:
        return await self._http.call(
            "GET",
            f"/crm/v1/customers/{segment(customer_id)}/consent",
            ConsentDto,
            correlation_id=correlation_id,
        )

    async def verify(
        self, customer_id: str, *, phone_last4: str, correlation_id: str
    ) -> Result[VerifyResult]:
        return await self._http.call(
            "POST",
            f"/crm/v1/customers/{segment(customer_id)}/verify",
            VerifyResult,
            correlation_id=correlation_id,
            json={"phone_last4": phone_last4},
        )


class BillingReader(_Client):
    async def credits(
        self, customer_id: str, *, since: str | None = None, correlation_id: str
    ) -> Result[CreditList]:
        return await self._http.call(
            "GET",
            f"/billing/v1/accounts/{segment(customer_id)}/credits",
            CreditList,
            correlation_id=correlation_id,
            params={"since": since} if since else None,
        )

    async def credit_by_key(self, key: str, *, correlation_id: str) -> Result[CreditDto]:
        return await self._http.call(
            "GET",
            f"/billing/v1/credits/by-key/{segment(key)}",
            CreditDto,
            correlation_id=correlation_id,
        )


class BillingWriter(BillingReader):
    async def apply_credit(
        self, body: dict[str, Any], *, idempotency_key: str, correlation_id: str
    ) -> Result[CreditDto]:
        return await self._http.call(
            "POST",
            "/billing/v1/credits",
            CreditDto,
            correlation_id=correlation_id,
            json=body,
            idempotency_key=idempotency_key,
        )


class FieldReader(_Client):
    async def slots(self, *, pincode: str, from_date: str, correlation_id: str) -> Result[SlotList]:
        return await self._http.call(
            "GET",
            "/field/v1/slots",
            SlotList,
            correlation_id=correlation_id,
            params={"pincode": pincode, "from_date": from_date},
        )

    async def booked_for_service(
        self, service_id: str, *, correlation_id: str
    ) -> Result[AppointmentList]:
        return await self._http.call(
            "GET",
            "/field/v1/appointments",
            AppointmentList,
            correlation_id=correlation_id,
            params={"service_id": service_id, "status": "BOOKED"},
        )

    async def appointment_by_key(self, key: str, *, correlation_id: str) -> Result[AppointmentDto]:
        return await self._http.call(
            "GET",
            f"/field/v1/appointments/by-key/{segment(key)}",
            AppointmentDto,
            correlation_id=correlation_id,
        )


class FieldWriter(FieldReader):
    async def book(
        self, body: dict[str, Any], *, idempotency_key: str, correlation_id: str
    ) -> Result[AppointmentDto]:
        return await self._http.call(
            "POST",
            "/field/v1/appointments",
            AppointmentDto,
            correlation_id=correlation_id,
            json=body,
            idempotency_key=idempotency_key,
        )

    async def cancel(self, appointment_id: str, *, correlation_id: str) -> Result[Acknowledged]:
        return await self._http.call(
            "POST",
            f"/field/v1/appointments/{segment(appointment_id)}/cancel",
            Acknowledged,
            correlation_id=correlation_id,
        )


class IncidentReader(_Client):
    async def open_on_node(self, node_id: str, *, correlation_id: str) -> Result[IncidentList]:
        return await self._http.call(
            "GET",
            "/incident/v1/incidents",
            IncidentList,
            correlation_id=correlation_id,
            params={"node_id": node_id, "status": "OPEN"},
        )

    async def incident(self, incident_id: str, *, correlation_id: str) -> Result[IncidentDto]:
        return await self._http.call(
            "GET",
            f"/incident/v1/incidents/{segment(incident_id)}",
            IncidentDto,
            correlation_id=correlation_id,
        )

    async def incident_by_key(self, key: str, *, correlation_id: str) -> Result[IncidentDto]:
        return await self._http.call(
            "GET",
            f"/incident/v1/incidents/by-key/{segment(key)}",
            IncidentDto,
            correlation_id=correlation_id,
        )

    async def complaint(
        self, complaint_ref: str, *, correlation_id: str
    ) -> Result[ComplaintRegistration]:
        return await self._http.call(
            "GET",
            f"/incident/v1/complaints/{segment(complaint_ref)}",
            ComplaintRegistration,
            correlation_id=correlation_id,
        )


class IncidentWriter(IncidentReader):
    async def open(
        self, body: dict[str, Any], *, idempotency_key: str, correlation_id: str
    ) -> Result[IncidentDto]:
        return await self._http.call(
            "POST",
            "/incident/v1/incidents",
            IncidentDto,
            correlation_id=correlation_id,
            json=body,
            idempotency_key=idempotency_key,
        )

    async def update(
        self, incident_id: str, body: dict[str, Any], *, idempotency_key: str, correlation_id: str
    ) -> Result[IncidentDto]:
        """Change an incident. A PATCH with the same body is idempotent by nature; the key is
        sent as well so that every write the platform makes can be traced to its action."""
        return await self._http.call(
            "PATCH",
            f"/incident/v1/incidents/{segment(incident_id)}",
            IncidentDto,
            correlation_id=correlation_id,
            json=body,
            idempotency_key=idempotency_key,
        )

    async def link_complaint(
        self, incident_id: str, body: dict[str, Any], *, idempotency_key: str, correlation_id: str
    ) -> Result[Acknowledged]:
        return await self._http.call(
            "POST",
            f"/incident/v1/incidents/{segment(incident_id)}/complaints",
            Acknowledged,
            correlation_id=correlation_id,
            json=body,
            idempotency_key=idempotency_key,
        )

    async def register_complaint(
        self, body: dict[str, Any], *, idempotency_key: str, correlation_id: str
    ) -> Result[ComplaintRegistration]:
        return await self._http.call(
            "POST",
            "/incident/v1/complaints",
            ComplaintRegistration,
            correlation_id=correlation_id,
            json=body,
            idempotency_key=idempotency_key,
        )


class NotifyWriter(_Client):
    async def send(
        self, body: dict[str, Any], *, idempotency_key: str, correlation_id: str
    ) -> Result[MessageReceipt]:
        return await self._http.call(
            "POST",
            "/notify/v1/messages",
            MessageReceipt,
            correlation_id=correlation_id,
            json=body,
            idempotency_key=idempotency_key,
        )

    async def by_key(self, key: str, *, correlation_id: str) -> Result[MessageReceipt]:
        """The message an idempotency key produced, if any: did an earlier attempt go out?"""
        return await self._http.call(
            "GET",
            f"/notify/v1/messages/by-key/{segment(key)}",
            MessageReceipt,
            correlation_id=correlation_id,
        )
