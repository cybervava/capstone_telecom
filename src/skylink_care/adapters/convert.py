"""Wire models to domain values. The only place that knows both vocabularies."""

from __future__ import annotations

from skylink_care.adapters.models import NodeStatusDto, OutageDto
from skylink_care.adapters.result import Ok, Result
from skylink_care.domain.models import OutageInfo
from skylink_care.domain.network import LiveStatus


def to_outage_info(dto: OutageDto) -> OutageInfo:
    return OutageInfo(
        outage_id=dto.outage_id,
        version=dto.version,
        cause=dto.cause,
        planned=dto.planned,
        started_at=dto.started_at,
        eta=dto.eta,
        eta_source=dto.eta_source,
        resolved_at=dto.resolved_at,
    )


def to_live_status(result: Result[NodeStatusDto]) -> LiveStatus:
    """Turn the outcome of a status call into evidence. A failed call is *no* evidence."""
    if not isinstance(result, Ok):
        return LiveStatus.unavailable(result.kind.value)
    status = result.value
    if status.outage is not None:
        return LiveStatus.in_outage(
            to_outage_info(status.outage),
            observed_at=status.observed_at,
            critical_alarm=status.has_critical_alarm,
        )
    return LiveStatus.operational(
        observed_at=status.observed_at, critical_alarm=status.has_critical_alarm
    )
