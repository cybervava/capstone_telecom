"""App factory and the ``mock-oss`` command line (``serve`` | ``seed``)."""

from __future__ import annotations

import argparse
import logging
import os
import random
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request

from mock_oss import admin, admin_search, billing, crm, field, incident, network, notify
from mock_oss.auth import KeyRing
from mock_oss.chaos import ChaosRules
from mock_oss.clock import Clock, SystemClock, iso
from mock_oss.config import DEFAULT_DB, Settings
from mock_oss.db import Database, read_counter
from mock_oss.errors import install_error_handlers
from mock_oss.middleware import RequestCounters, install_gateway_middleware
from mock_oss.noc import EVENT_SEQ, ensure_detected
from mock_oss.seed import SeedError, seed_database

log = logging.getLogger("mock_oss")
ROUTERS = [
    network.router,
    crm.router,
    billing.router,
    field.router,
    incident.router,
    notify.router,
    admin.router,
    admin_search.router,
]


def _health(request: Request) -> dict[str, Any]:
    db: Database = request.app.state.db
    clock: Clock = request.app.state.clock
    ensure_detected(db, iso(clock.now()))
    with db.read() as conn:
        meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
        last_seq = read_counter(conn, EVENT_SEQ)
    return {
        "status": "ok",
        "seeded": meta.get("seeded") == "true",
        "customers": int(meta.get("customers", "0")),
        "last_event_seq": last_seq,
    }


def create_app(settings: Settings, *, clock: Clock | None = None) -> FastAPI:
    """Build the gateway. Creates an empty schema if the database has not been seeded."""
    db = Database(settings.db_path)
    db.initialise()
    app = FastAPI(title="SkyLink mock OSS/BSS gateway", version="1.0")
    app.state.settings = settings
    app.state.db = db
    app.state.clock = clock or SystemClock()
    app.state.keyring = KeyRing(settings.api_keys)
    app.state.chaos = ChaosRules()
    app.state.counters = RequestCounters()
    app.state.rng = random.Random()
    install_error_handlers(app)
    install_gateway_middleware(app)
    for router in ROUTERS:
        app.include_router(router)
    app.add_api_route("/health", _health, methods=["GET"])
    return app


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mock-oss", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the HTTP gateway (default)")
    seed = sub.add_parser("seed", help="(re)build the store from a data pack")
    seed.add_argument("--data", type=Path, required=True, help="directory with the CSV files")
    seed.add_argument("--db", type=Path, default=None, help="SQLite path (default $MOCK_OSS_DB)")
    seed.add_argument("--seed", type=int, default=42, help="seed for line-fault selection")
    return parser


def _serve() -> int:
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        log.error("invalid configuration: %s", exc)
        return 2
    if not settings.api_keys:
        log.warning("no OSS_KEY_* variables set: every authenticated call will get 401")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")
    return 0


def _seed(data: Path, db: Path | None, seed: int) -> int:
    target = db or Path(os.environ.get("MOCK_OSS_DB", DEFAULT_DB))
    try:
        stats = seed_database(data, target, seed=seed)
    except SeedError as exc:
        log.error("seed failed: %s", exc)
        return 2
    log.info("seeded %s in %.1fs (%s customers)", target, stats.seconds, stats.customers)
    return 0


def run(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parser().parse_args(argv)
    if args.command == "seed":
        return _seed(args.data, args.db, args.seed)
    return _serve()
