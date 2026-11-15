"""Node episodes: one open episode per node and service, whoever notices first."""

from __future__ import annotations

import asyncio

from skylink_care.db.database import Database
from skylink_care.db.episodes import EpisodeKind, EpisodeRepository

NODE = "BLR-OLT-0012"


async def test_first_report_without_an_outage_id_opens_a_suspected_episode(
    episodes: EpisodeRepository,
) -> None:
    result = await episodes.open_or_join(NODE, "broadband", outage_id=None)

    assert result.created
    assert result.episode.kind is EpisodeKind.SUSPECTED_NODE_FAULT
    assert result.episode.outage_id is None


async def test_later_reports_join_the_open_episode(episodes: EpisodeRepository) -> None:
    first = await episodes.open_or_join(NODE, "broadband", outage_id=None)
    second = await episodes.open_or_join(NODE, "broadband", outage_id=None)

    assert not second.created
    assert second.episode.episode_id == first.episode.episode_id


async def test_declared_outage_upgrades_the_suspected_episode_instead_of_opening_another(
    episodes: EpisodeRepository,
) -> None:
    suspected = await episodes.open_or_join(NODE, "broadband", outage_id=None)

    declared = await episodes.open_or_join(NODE, "broadband", outage_id="OUT-20261115-0003")

    assert declared.upgraded
    assert not declared.created
    assert declared.episode.episode_id == suspected.episode.episode_id
    assert declared.episode.kind is EpisodeKind.NETWORK_OUTAGE
    assert declared.episode.outage_id == "OUT-20261115-0003"


async def test_same_outage_joins_and_is_not_an_upgrade(episodes: EpisodeRepository) -> None:
    first = await episodes.open_or_join(NODE, "broadband", outage_id="OUT-1")
    second = await episodes.open_or_join(NODE, "broadband", outage_id="OUT-1")

    assert first.created
    assert (second.created, second.upgraded) == (False, False)
    assert second.episode.episode_id == first.episode.episode_id


async def test_a_suspected_report_during_a_declared_outage_joins_it(
    episodes: EpisodeRepository,
) -> None:
    declared = await episodes.open_or_join(NODE, "broadband", outage_id="OUT-1")
    suspected = await episodes.open_or_join(NODE, "broadband", outage_id=None)

    assert suspected.episode.episode_id == declared.episode.episode_id
    assert suspected.episode.outage_id == "OUT-1"


async def test_a_new_outage_closes_the_old_episode_and_opens_a_new_one(
    episodes: EpisodeRepository,
) -> None:
    old = await episodes.open_or_join(NODE, "broadband", outage_id="OUT-1")

    new = await episodes.open_or_join(NODE, "broadband", outage_id="OUT-2")

    assert new.created
    assert new.episode.episode_id != old.episode.episode_id
    current = await episodes.find_open(NODE, "broadband")
    assert current is not None
    assert current.outage_id == "OUT-2"
    closed = await episodes.find_for_outage(NODE, "broadband", "OUT-1")
    assert closed is not None
    assert closed.closed_at is not None


async def test_services_on_one_node_have_separate_episodes(episodes: EpisodeRepository) -> None:
    broadband = await episodes.open_or_join(NODE, "broadband", outage_id="OUT-1")
    mobile = await episodes.open_or_join(NODE, "mobile", outage_id="OUT-1")

    assert broadband.episode.episode_id != mobile.episode.episode_id


async def test_resolution_closes_every_episode_of_the_outage_once(
    episodes: EpisodeRepository,
) -> None:
    await episodes.open_or_join("BLR-OLT-0001", "broadband", outage_id="OUT-9")
    await episodes.open_or_join("BLR-OLT-0002", "broadband", outage_id="OUT-9")

    first = await episodes.close_for_outage("OUT-9")
    again = await episodes.close_for_outage("OUT-9")

    assert len(first) == 2
    assert again == []
    assert await episodes.find_open("BLR-OLT-0001", "broadband") is None


async def test_a_node_can_have_a_new_episode_after_the_previous_one_closed(
    episodes: EpisodeRepository,
) -> None:
    first = await episodes.open_or_join(NODE, "broadband", outage_id="OUT-1")
    await episodes.close_for_outage("OUT-1")

    second = await episodes.open_or_join(NODE, "broadband", outage_id=None)

    assert second.created
    assert second.episode.episode_id != first.episode.episode_id


async def test_forty_simultaneous_reports_open_exactly_one_episode(
    episodes: EpisodeRepository, db: Database
) -> None:
    results = await asyncio.gather(
        *(episodes.open_or_join(NODE, "broadband", outage_id="OUT-1") for _ in range(40))
    )

    assert len({result.episode.episode_id for result in results}) == 1
    assert sum(1 for result in results if result.created) == 1
    async with db.connection() as conn:
        cursor = await conn.execute(
            "SELECT count(*) AS n FROM node_episodes WHERE node_id = %s", (NODE,)
        )
        row = await cursor.fetchone()
    assert row is not None
    assert row["n"] == 1


async def test_suspected_and_declared_reports_racing_still_leave_one_episode(
    episodes: EpisodeRepository, db: Database
) -> None:
    calls = [
        episodes.open_or_join(NODE, "broadband", outage_id=None if n % 2 else "OUT-1")
        for n in range(40)
    ]

    await asyncio.gather(*calls)

    async with db.connection() as conn:
        cursor = await conn.execute(
            "SELECT outage_id, kind FROM node_episodes WHERE node_id = %s", (NODE,)
        )
        rows = await cursor.fetchall()
    assert rows == [{"outage_id": "OUT-1", "kind": "NETWORK_OUTAGE"}]
