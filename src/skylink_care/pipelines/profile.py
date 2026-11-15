"""Mapping-quality profile: how many services can be tied to exactly one live network node.

This is the readiness-gap measurement for discovery. A service that is not ``resolved`` cannot be
told about an outage from the mapping alone; it depends on a line test to find its node.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from skylink_care.db.database import Database

_QUALITY_SQL = """
WITH per_service AS (
    SELECT s.service_id, s.service_type,
           count(DISTINCT m.node_id) AS node_count,
           bool_and(n.status IS NOT DISTINCT FROM 'ACTIVE') AS all_active
    FROM service_index s
    LEFT JOIN address_node_map m
           ON m.address_id = s.address_id AND m.service_type = s.service_type
          AND m.valid_to IS NULL
    LEFT JOIN nodes n ON n.node_id = m.node_id
    GROUP BY s.service_id, s.service_type
)
SELECT service_type,
       count(*) AS services,
       count(*) FILTER (WHERE node_count = 1 AND all_active) AS resolved,
       count(*) FILTER (WHERE node_count = 0) AS missing,
       count(*) FILTER (WHERE node_count > 1) AS ambiguous,
       count(*) FILTER (WHERE node_count = 1 AND NOT all_active) AS stale
FROM per_service GROUP BY service_type ORDER BY service_type
"""
_NODES_SQL = (
    "SELECT node_type, status, count(*) AS n FROM nodes GROUP BY node_type, status ORDER BY 1, 2"
)


@dataclass(frozen=True)
class MappingQuality:
    service_type: str
    services: int
    resolved: int
    missing: int
    ambiguous: int
    stale: int

    @property
    def coverage(self) -> float:
        return self.resolved / self.services if self.services else 0.0


@dataclass(frozen=True)
class DataProfile:
    quality: tuple[MappingQuality, ...]
    nodes: tuple[tuple[str, str, int], ...]
    map_versions: tuple[str, ...]


async def build_profile(database: Database) -> DataProfile:
    async with database.connection() as conn:
        quality = [
            MappingQuality(**row) for row in await (await conn.execute(_QUALITY_SQL)).fetchall()
        ]
        nodes = [
            (row["node_type"], row["status"], row["n"])
            for row in await (await conn.execute(_NODES_SQL)).fetchall()
        ]
        versions = [
            row["map_version"]
            for row in await (
                await conn.execute("SELECT DISTINCT map_version FROM address_node_map ORDER BY 1")
            ).fetchall()
        ]
    return DataProfile(tuple(quality), tuple(nodes), tuple(versions))


def render_markdown(profile: DataProfile, *, generated_at: datetime, source: str) -> str:
    lines = [
        "# Data profile: customer → service → node mapping",
        "",
        f"Generated {generated_at:%Y-%m-%d %H:%M %Z} from `{source}`."
        f" Mapping version(s): {', '.join(profile.map_versions) or 'none'}.",
        "",
        "A service is **resolved** when its address has exactly one current mapping row and that"
        " row points at an active node. Everything else cannot be matched to an outage from the"
        " mapping alone.",
        "",
        "| Service type | Services | Resolved | Coverage | Missing | Ambiguous | Stale |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    lines += [
        f"| {q.service_type} | {q.services:,} | {q.resolved:,} | {q.coverage:.2%} |"
        f" {q.missing:,} | {q.ambiguous:,} | {q.stale:,} |"
        for q in profile.quality
    ]
    lines += ["", "| Node type | Status | Count |", "|---|---|---:|"]
    lines += [f"| {kind} | {status} | {count:,} |" for kind, status, count in profile.nodes]
    return "\n".join(lines) + "\n"
