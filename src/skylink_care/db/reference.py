"""Reference data: network nodes and the operational address → node mapping.

The mapping is known to be imperfect. ``resolve_mapping`` never guesses: an address with no
current row, several candidate nodes, or a row pointing at a decommissioned node is reported as
such, and the caller treats it as unresolved.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from skylink_care.db.base import Repository
from skylink_care.db.database import Connection
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
class AffectedService:
    service_id: str
    customer_id: str
    service_type: str
    node_id: str
    area: str


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

    async def get_node(self, node_id: str, *, conn: Connection | None = None) -> NodeRecord | None:
        async with self._conn(conn) as active:
            cursor = await active.execute(
                "SELECT node_id, node_type, service_type, parent_node_id, city, area, pincode,"
                " status FROM nodes WHERE node_id = %s",
                (node_id,),
            )
            row = await cursor.fetchone()
        return NodeRecord(**row) if row else None

    async def access_nodes(
        self, node_id: str, *, conn: Connection | None = None
    ) -> list[NodeRecord]:
        """The customer-facing nodes a network node stands for.

        An access node (OLT, tower) is itself; an aggregation node is its active children. Alarms
        and outages are raised on either kind, customers are always attached to the first.
        """
        async with self._conn(conn) as active:
            cursor = await active.execute(
                "SELECT node_id, node_type, service_type, parent_node_id, city, area, pincode,"
                " status FROM nodes"
                " WHERE status = 'ACTIVE' AND service_type IS NOT NULL"
                "   AND (node_id = %s OR parent_node_id = %s) ORDER BY node_id",
                (node_id, node_id),
            )
            return [NodeRecord(**row) for row in await cursor.fetchall()]

    async def services_on_nodes(
        self, node_ids: Sequence[str], *, conn: Connection | None = None
    ) -> list[AffectedService]:
        """Active services whose address maps to one of these nodes and to no other node.

        Ambiguously mapped addresses are left out on purpose: telling an unaffected customer that
        their service is down is worse than not telling an affected one before they ask.
        """
        if not node_ids:
            return []
        async with self._conn(conn) as active:
            cursor = await active.execute(
                "SELECT s.service_id, s.customer_id, s.service_type, m.node_id, n.area"
                " FROM address_node_map m"
                " JOIN nodes n ON n.node_id = m.node_id AND n.service_type = m.service_type"
                " JOIN service_index s"
                "   ON s.address_id = m.address_id AND s.service_type = m.service_type"
                " WHERE m.node_id = ANY(%s) AND m.valid_to IS NULL AND s.status = 'active'"
                "   AND NOT EXISTS ("
                "     SELECT 1 FROM address_node_map other"
                "     WHERE other.address_id = m.address_id AND other.service_type = m.service_type"
                "       AND other.valid_to IS NULL AND other.node_id <> m.node_id)"
                " ORDER BY s.customer_id, s.service_type, s.service_id",
                (list(node_ids),),
            )
            return [AffectedService(**row) for row in await cursor.fetchall()]
