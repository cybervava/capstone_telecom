"""Reference data: network nodes and the operational address → node mapping.

The mapping is known to be imperfect. ``resolve_mapping`` never guesses: an address with no
current row, several candidate nodes, or a row pointing at a decommissioned node is reported as
such, and the caller treats it as unresolved.
"""

from __future__ import annotations

from dataclasses import dataclass

from skylink_care.db.base import Repository
from skylink_care.domain.models import MappingStatus, NodeMapping

ACTIVE = "ACTIVE"


@dataclass(frozen=True)
class NodeRecord:
    node_id: str
    node_type: str
    service_type: str | None
    parent_node_id: str | None
    city: str
    area: str
    pincode: str
    status: str


@dataclass(frozen=True)
class ServiceRef:
    service_id: str
    customer_id: str


class ReferenceRepository(Repository):
    async def resolve_mapping(self, address_id: str, service_type: str) -> NodeMapping:
        """Resolve a service address to its access node as far as the mapping allows."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT m.node_id, m.map_version, n.status AS node_status"
                " FROM address_node_map m LEFT JOIN nodes n ON n.node_id = m.node_id"
                " WHERE m.address_id = %s AND m.service_type = %s AND m.valid_to IS NULL"
                " ORDER BY m.node_id",
                (address_id, service_type),
            )
            rows = await cursor.fetchall()

        by_node = {row["node_id"]: row for row in rows}
        candidates = tuple(by_node)
        if not candidates:
            return NodeMapping(MappingStatus.MISSING, None, (), None)
        version = rows[0]["map_version"]
        if len(candidates) > 1:
            return NodeMapping(MappingStatus.AMBIGUOUS, None, candidates, version)
        only = by_node[candidates[0]]
        if only["node_status"] != ACTIVE:
            return NodeMapping(MappingStatus.STALE, None, candidates, version)
        return NodeMapping(MappingStatus.RESOLVED, candidates[0], candidates, version)

    async def get_node(self, node_id: str) -> NodeRecord | None:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT node_id, node_type, service_type, parent_node_id, city, area, pincode,"
                " status FROM nodes WHERE node_id = %s",
                (node_id,),
            )
            row = await cursor.fetchone()
        return NodeRecord(**row) if row else None

    async def services_on_node(self, node_id: str, service_type: str) -> list[ServiceRef]:
        """Active services whose address maps to this node and to no other.

        Ambiguously mapped addresses are left out on purpose: telling an unaffected customer that
        their service is down is worse than not telling an affected one before they ask.
        """
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT s.service_id, s.customer_id FROM service_index s"
                " JOIN address_node_map m"
                "   ON m.address_id = s.address_id AND m.service_type = s.service_type"
                " WHERE m.node_id = %s AND m.service_type = %s AND m.valid_to IS NULL"
                "   AND s.status = 'active'"
                "   AND NOT EXISTS ("
                "     SELECT 1 FROM address_node_map other"
                "     WHERE other.address_id = m.address_id AND other.service_type = m.service_type"
                "       AND other.valid_to IS NULL AND other.node_id <> m.node_id)"
                " ORDER BY s.service_id",
                (node_id, service_type),
            )
            return [ServiceRef(**row) for row in await cursor.fetchall()]
