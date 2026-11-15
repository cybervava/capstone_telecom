"""Node episodes: the care platform's own identity for trouble on one node.

The NOC issues an outage id minutes after customers start calling, so the platform cannot borrow
that id as the identity of its incident ticket. An episode is opened by whoever notices first
(a suspected fault, or a declared outage) and the outage id is attached when it becomes known.
The partial unique index guarantees one open episode per node and service, which is what makes
"zero duplicate tickets per node" a database fact rather than a hope.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from skylink_care.db.base import Repository
from skylink_care.db.database import Connection

_COLUMNS = "episode_id, node_id, service_type, kind, outage_id, opened_at, closed_at"
_MAX_RACE_RETRIES = 5


class EpisodeKind(StrEnum):
    NETWORK_OUTAGE = "NETWORK_OUTAGE"
    SUSPECTED_NODE_FAULT = "SUSPECTED_NODE_FAULT"


@dataclass(frozen=True)
class Episode:
    episode_id: UUID
    node_id: str
    service_type: str
    kind: EpisodeKind
    outage_id: str | None
    opened_at: datetime
    closed_at: datetime | None


@dataclass(frozen=True)
class EpisodeResult:
    episode: Episode
    created: bool
    # True when an existing suspected-fault episode just learned its outage id.
    upgraded: bool


class EpisodeRepository(Repository):
    async def open_or_join(
        self, node_id: str, service_type: str, *, outage_id: str | None
    ) -> EpisodeResult:
        """Return the open episode for the node, creating or upgrading it as needed.

        * no open episode            -> create one (suspected when ``outage_id`` is None)
        * open, same or no new id    -> join it
        * open and suspected, new id -> attach the outage id (the same ticket carries on)
        * open with a different id   -> the earlier outage ended unnoticed: close it, open anew
        """
        for _ in range(_MAX_RACE_RETRIES):
            async with self._db.transaction() as conn:
                current = await _lock_open(conn, node_id, service_type)
                if current is None:
                    created = await _insert(conn, node_id, service_type, outage_id)
                    if created is None:
                        continue  # another transaction opened one first; read it on the next pass
                    return EpisodeResult(created, created=True, upgraded=False)
                if outage_id is None or current.outage_id == outage_id:
                    return EpisodeResult(current, created=False, upgraded=False)
                if current.outage_id is None:
                    upgraded = await _attach_outage(conn, current.episode_id, outage_id)
                    return EpisodeResult(upgraded, created=False, upgraded=True)
                await _close(conn, current.episode_id)
                replacement = await _insert(conn, node_id, service_type, outage_id)
                if replacement is not None:
                    return EpisodeResult(replacement, created=True, upgraded=False)
        raise RuntimeError(f"could not settle the episode for {node_id}/{service_type}")

    async def get(self, episode_id: UUID) -> Episode | None:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} FROM node_episodes WHERE episode_id = %s",  # noqa: S608
                (episode_id,),
            )
            row = await cursor.fetchone()
        return _to_episode(row) if row else None

    async def find_open(self, node_id: str, service_type: str) -> Episode | None:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} FROM node_episodes"  # noqa: S608 - constant column list
                " WHERE node_id = %s AND service_type = %s AND closed_at IS NULL",
                (node_id, service_type),
            )
            row = await cursor.fetchone()
        return _to_episode(row) if row else None

    async def find_for_outage(
        self, node_id: str, service_type: str, outage_id: str
    ) -> Episode | None:
        """The episode (open or closed) that carried this outage on this node."""
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                f"SELECT {_COLUMNS} FROM node_episodes"  # noqa: S608 - constant column list
                " WHERE node_id = %s AND service_type = %s AND outage_id = %s"
                " ORDER BY opened_at DESC LIMIT 1",
                (node_id, service_type, outage_id),
            )
            row = await cursor.fetchone()
        return _to_episode(row) if row else None

    async def attach_outage_to_open(
        self, conn: Connection, node_id: str, service_type: str, outage_id: str
    ) -> Episode | None:
        """The NOC has declared an outage: give its id to the node's open suspected episode.

        Returns the episode when it was upgraded, None when there was nothing to upgrade. No
        episode is created here: tickets are opened when a customer is affected, not for every
        node of every outage.
        """
        current = await _lock_open(conn, node_id, service_type)
        if current is None or current.outage_id is not None:
            return None
        return await _attach_outage(conn, current.episode_id, outage_id)

    async def close_suspected(
        self, conn: Connection, node_id: str, service_type: str
    ) -> Episode | None:
        """The suspicion on this node has ended without an outage being declared: close it.

        Returns the closed episode, or None when the node's open episode (if any) belongs to a
        declared outage, which only that outage's resolution closes.
        """
        current = await _lock_open(conn, node_id, service_type)
        if current is None or current.outage_id is not None:
            return None
        await _close(conn, current.episode_id)
        return current

    async def close_for_outage(
        self, outage_id: str, *, conn: Connection | None = None
    ) -> list[Episode]:
        """Close the open episodes of a resolved outage and return all of its episodes.

        Episodes that were closed earlier are returned too (unchanged): an episode can be closed
        before its outage is resolved, when a newer outage takes over the node, and its ticket
        is still owed its resolution. Idempotent.
        """
        async with self._conn(conn) as active:
            cursor = await active.execute(
                "UPDATE node_episodes SET closed_at = COALESCE(closed_at, clock_timestamp())"  # noqa: S608 - constant column list
                " WHERE outage_id = %s"
                f" RETURNING {_COLUMNS}",
                (outage_id,),
            )
            return [_to_episode(row) for row in await cursor.fetchall()]


async def _lock_open(conn: Connection, node_id: str, service_type: str) -> Episode | None:
    cursor = await conn.execute(
        f"SELECT {_COLUMNS} FROM node_episodes"  # noqa: S608 - constant column list
        " WHERE node_id = %s AND service_type = %s AND closed_at IS NULL FOR UPDATE",
        (node_id, service_type),
    )
    row = await cursor.fetchone()
    return _to_episode(row) if row else None


async def _insert(
    conn: Connection, node_id: str, service_type: str, outage_id: str | None
) -> Episode | None:
    kind = EpisodeKind.NETWORK_OUTAGE if outage_id else EpisodeKind.SUSPECTED_NODE_FAULT
    cursor = await conn.execute(
        "INSERT INTO node_episodes (episode_id, node_id, service_type, kind, outage_id)"  # noqa: S608 - constant column list
        " VALUES (%s, %s, %s, %s, %s)"
        " ON CONFLICT (node_id, service_type) WHERE closed_at IS NULL DO NOTHING"
        f" RETURNING {_COLUMNS}",
        (uuid4(), node_id, service_type, kind.value, outage_id),
    )
    row = await cursor.fetchone()
    return _to_episode(row) if row else None


async def _attach_outage(conn: Connection, episode_id: UUID, outage_id: str) -> Episode:
    cursor = await conn.execute(
        "UPDATE node_episodes SET outage_id = %s, kind = %s WHERE episode_id = %s"  # noqa: S608 - constant column list
        f" RETURNING {_COLUMNS}",
        (outage_id, EpisodeKind.NETWORK_OUTAGE.value, episode_id),
    )
    row = await cursor.fetchone()
    if row is None:  # pragma: no cover - the row is locked by this transaction
        raise RuntimeError("episode vanished while attaching its outage id")
    return _to_episode(row)


async def _close(conn: Connection, episode_id: UUID) -> None:
    await conn.execute(
        "UPDATE node_episodes SET closed_at = clock_timestamp() WHERE episode_id = %s",
        (episode_id,),
    )


def _to_episode(row: dict[str, Any]) -> Episode:
    return Episode(**{**row, "kind": EpisodeKind(row["kind"])})
