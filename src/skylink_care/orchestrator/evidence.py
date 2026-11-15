"""Network decisions to and from the JSON-plain form kept in workflow state."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from skylink_care.domain.models import OutageInfo
from skylink_care.domain.network import NetworkDecision


def outage_to_state(outage: OutageInfo | None) -> dict[str, Any] | None:
    if outage is None:
        return None
    return {
        "outage_id": outage.outage_id,
        "version": outage.version,
        "cause": outage.cause,
        "planned": outage.planned,
        "started_at": outage.started_at.isoformat(),
        "eta": _iso(outage.eta),
        "eta_source": outage.eta_source,
        "resolved_at": _iso(outage.resolved_at),
    }


def outage_from_state(data: dict[str, Any] | None) -> OutageInfo | None:
    if not data:
        return None
    return OutageInfo(
        outage_id=data["outage_id"],
        version=data["version"],
        cause=data["cause"],
        planned=data["planned"],
        started_at=datetime.fromisoformat(data["started_at"]),
        eta=_parse(data.get("eta")),
        eta_source=data.get("eta_source"),
        resolved_at=_parse(data.get("resolved_at")),
    )


def decision_to_state(decision: NetworkDecision) -> dict[str, Any]:
    return {
        "state": decision.state.value,
        "reason": decision.reason.value,
        "source": decision.source.value,
        "node_id": decision.node_id,
        "outage": outage_to_state(decision.outage),
        "last_known_outage": outage_to_state(decision.last_known_outage),
        "evidence_at": _iso(decision.evidence_at),
        "decided_at": decision.decided_at.isoformat(),
    }


def parse_time(value: str | None) -> datetime | None:
    return _parse(value)


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
