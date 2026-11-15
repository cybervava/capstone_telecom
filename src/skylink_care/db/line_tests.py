"""Remote line tests the platform has run, kept for two purposes.

One customer's latest result decides what may be done for them. And the results on one node,
taken together, are evidence about the node: several lines failing within minutes of each other
point to a fault the NOC has not declared yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from skylink_care.db.base import Repository

_COLUMNS = "test_id, service_id, node_id, result, tested_at"

PASS = "PASS"  # noqa: S105 - a test result, not a credential
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class LineTest:
    test_id: str
    service_id: str
    # The node that actually serves the line. It can differ from the mapped node.
    node_id: str
    result: str
    tested_at: datetime


class LineTestRepository(Repository):
    async def record(self, test: LineTest, *, contact_id: UUID | None) -> None:
        """Store a result once. A repeat of the same test id changes nothing."""
        async with self._db.connection() as conn:
            await conn.execute(
                "INSERT INTO line_tests (test_id, contact_id, service_id, node_id, result,"
                " tested_at) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (test_id) DO NOTHING",
                (
                    test.test_id,
                    contact_id,
                    test.service_id,
                    test.node_id,
                    test.result,
                    test.tested_at,
                ),
            )

    async def latest_for_contact(
        self, contact_id: UUID, service_id: str, *, since: datetime
    ) -> LineTest | None:
        """The most recent test this contact ran on this line, if any since ``since``."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} FROM line_tests"  # noqa: S608 - constant column list
                " WHERE contact_id = %s AND service_id = %s AND tested_at >= %s"
                " ORDER BY tested_at DESC, test_id DESC LIMIT 1",
                (contact_id, service_id, since),
            )
            row = await cursor.fetchone()
        return LineTest(**row) if row else None

    async def failing_lines_on_node(self, node_id: str, *, since: datetime) -> int:
        """How many customers have a line failing on the node, by each line's latest test.

        A line that failed and then passed is not counted, a line that has since been found on
        another node is not counted here, and one customer is counted once however many lines
        or tests they have: a single customer cannot make a node look faulty.
        """
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT count(DISTINCT COALESCE(s.customer_id, latest.service_id)) AS n FROM ("
                "  SELECT DISTINCT ON (t.service_id) t.service_id, t.node_id, t.result"
                "  FROM line_tests t"
                "  WHERE t.tested_at >= %s AND t.service_id IN ("
                "    SELECT service_id FROM line_tests WHERE node_id = %s AND tested_at >= %s)"
                "  ORDER BY t.service_id, t.tested_at DESC, t.test_id DESC"
                " ) latest LEFT JOIN service_index s ON s.service_id = latest.service_id"
                " WHERE latest.node_id = %s AND latest.result = 'FAIL'",
                (since, node_id, since, node_id),
            )
            row = await cursor.fetchone()
        return int(row["n"]) if row else 0
