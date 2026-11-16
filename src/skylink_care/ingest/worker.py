"""Entry point for the feed consumer process (``care-ingest``).

It holds one gateway key, which can read the event feed and nothing else, and it writes only to
the platform's own database.
"""

from __future__ import annotations

import asyncio
import signal
from datetime import timedelta

import structlog

from skylink_care.adapters.gateway import OssGateway, open_gateway
from skylink_care.clock import Clock, utc_now
from skylink_care.config import Settings, get_oss_keys, get_settings
from skylink_care.db.actions import ActionRepository
from skylink_care.db.contacts import ContactRepository
from skylink_care.db.database import Database
from skylink_care.db.episodes import EpisodeRepository
from skylink_care.db.read_model import ReadModelRepository
from skylink_care.db.reference import ReferenceRepository
from skylink_care.ingest.consumer import FeedConsumer
from skylink_care.ingest.reactions import OutageReactions
from skylink_care.observability.logging import configure_logging

log = structlog.get_logger(__name__)

POOL_SIZE = 4


def build_consumer(
    settings: Settings, database: Database, gateway: OssGateway, *, now: Clock = utc_now
) -> FeedConsumer:
    read_model = ReadModelRepository(database)
    reactions = OutageReactions(
        ReferenceRepository(database),
        ActionRepository(database),
        EpisodeRepository(database),
        ContactRepository(database),
        read_model,
        announce_within=timedelta(seconds=settings.notify_max_event_age_seconds),
    )
    return FeedConsumer(gateway.feed_for_ingest(), database, read_model, reactions, now=now)


async def _main() -> None:
    settings = get_settings()
    configure_logging()
    database = await Database.open(settings.database_conninfo(), max_size=POOL_SIZE)
    gateway = open_gateway(settings, get_oss_keys())
    try:
        consumer = build_consumer(settings, database, gateway)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)
        log.info("ingest_started")
        await consumer.run_forever(stop)
    finally:
        await gateway.aclose()
        await database.close()


def run() -> None:
    asyncio.run(_main())
