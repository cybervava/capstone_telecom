"""Offer the surge of the brief to the running stack and write the report.

    uv run --env-file .env python -m loadtest --profile quick
    make loadtest          # the profile of the brief, both runs: about an hour and a half

Needs the local stack (`make dev`), freshly seeded from the data pack named by --data. It
declares outages in the simulator and takes its Network Monitoring API down for the second run;
it leaves the simulator with neither. Exits 0 when every target was met, 1 when one was not, 2
when the stack was not as the test needs it.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import platform
import random
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from loadtest.callers import Pace
from loadtest.outcome import Targets
from loadtest.population import Pack, cast, load_pack
from loadtest.profile import PROFILES, Profile, arrival_times
from loadtest.report import build_report
from loadtest.run import Plan, Result, Rig, Waits, run
from loadtest.stack import Noc, StackNotReadyError
from skylink_care.api.main import INTAKE_POOL_MAX
from skylink_care.config import Settings, get_settings
from skylink_care.db.database import Database

HERE = Path(__file__).resolve().parent
RAW = Path("var/loadtest")
RUNS = {
    "both": ("baseline", "network-down"),
    "baseline": ("baseline",),
    "network-down": ("network-down",),
}
TITLES = {"baseline": "Every dependency up", "network-down": "Network Monitoring API down"}
# How the Network Monitoring API can fail in the simulator.
FAULTS = ("down", "timeout", "response_loss")
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def is_local(url: str) -> bool:
    """Whether a URL names this machine. The test resets the simulator, makes a dependency
    fail and signs in with the local staff key, so it runs against nothing else."""
    return urlsplit(url).hostname in LOCAL_HOSTS


def say(text: str) -> None:
    sys.stdout.write(f"{text}\n")
    sys.stdout.flush()


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m loadtest", description=__doc__.splitlines()[0])
    parser.add_argument("--profile", choices=sorted(PROFILES), default="quick")
    parser.add_argument("--runs", choices=sorted(RUNS), default="both")
    parser.add_argument("--fault", choices=FAULTS, default="down")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--data", type=Path, default=Path("data/generated"))
    parser.add_argument("--out", type=Path, help="where the report goes")
    parser.add_argument("--name", help="the report's file name (default: surge-<profile>)")
    parser.add_argument("--stay", type=float, default=180.0, help="seconds a web caller waits")
    parser.add_argument("--settle", type=float, default=20.0, help="seconds before the surge")
    parser.add_argument("--stack", default="the local processes of `make dev`")
    return parser.parse_args()


def plans_for(args: argparse.Namespace, profile: Profile, pack: Pack) -> list[Plan]:
    """The runs asked for. A later run fails other nodes and is called by other customers."""
    rng = random.Random(args.seed)
    plans: list[Plan] = []
    roots: tuple[str, ...] = ()
    customers: set[str] = set()
    for name in RUNS[args.runs]:
        due = tuple(arrival_times(profile, rng))
        drawn = cast(pack, len(due), rng, not_roots=roots, not_customers=customers)
        fault = args.fault if name == "network-down" else None
        plans.append(Plan(name=name, title=TITLES[name], fault=fault, due=due, cast=drawn))
        roots = (*roots, *drawn.roots)
        customers |= {caller.customer_id for caller in drawn.callers}
    return plans


async def ready(api: httpx.AsyncClient, noc: Noc) -> None:
    try:
        (await api.get("/health")).raise_for_status()
        await noc.reset()
    except httpx.HTTPError as exc:
        raise StackNotReadyError(
            f"the stack does not answer ({type(exc).__name__}). Start it with `make dev`."
        ) from exc


async def main(args: argparse.Namespace) -> int:
    settings = get_settings()
    staff_key, admin_key = os.environ.get("CARE_DEV_STAFF_KEY"), os.environ.get("OSS_KEY_ADMIN")
    if not staff_key or not admin_key:
        say("CARE_DEV_STAFF_KEY and OSS_KEY_ADMIN are needed: run with `uv run --env-file .env`.")
        return 2
    if not (is_local(api_url()) and is_local(settings.oss_base_url)):
        say("not run: the load test only runs against a stack on this machine.")
        return 2
    profile, targets = PROFILES[args.profile], Targets()
    say(f"reading the data pack in {args.data}")
    pack = await asyncio.to_thread(load_pack, args.data)
    plans = plans_for(args, profile, pack)
    database = await Database.open(settings.database_conninfo(), max_size=2)
    try:
        results = await offer(plans, pack, args, settings, database, (staff_key, admin_key))
    except StackNotReadyError as exc:
        say(f"not run: {exc}")
        return 2
    finally:
        await database.close()
    report = build_report(
        args.name or f"surge-{args.profile}",
        results,
        profile=profile,
        targets=targets,
        context={
            "machine": f"one {platform.system()} machine with {os.cpu_count()} CPU threads",
            "api_pool": str(INTAKE_POOL_MAX),
            "orchestrator_concurrency": str(settings.worker_concurrency),
            "stack": args.stack,
            "customers": customers_in(args.data),
            "seed": str(args.seed),
            "fault": args.fault,
        },
    )
    out = args.out or (HERE / "reports" if args.profile == "brief" else RAW)
    path = report.write(out)
    say(f"{'MET' if report.met else 'NOT MET'}. {report.headline}")
    say(f"report: {path}")
    return 0 if report.met else 1


async def offer(
    plans: list[Plan],
    pack: Pack,
    args: argparse.Namespace,
    settings: Settings,
    database: Database,
    keys: tuple[str, str],
) -> list[Result]:
    """Run the plans one after the other against the stack."""
    staff_key, admin_key = keys
    limits = httpx.Limits(max_connections=500, max_keepalive_connections=100)
    async with (
        httpx.AsyncClient(base_url=api_url(), timeout=10.0, limits=limits) as api,
        httpx.AsyncClient(
            base_url=settings.oss_base_url, headers={"X-API-Key": admin_key}, timeout=30.0
        ) as sim,
    ):
        noc = Noc(sim)
        await ready(api, noc)
        rig = Rig(
            api=api,
            noc=noc,
            db=database,
            staff_key=staff_key,
            targets=Targets(),
            pace=Pace(stay=args.stay),
            waits=Waits(settle=args.settle, breaker_rest=settings.oss_breaker_rest_seconds + 1),
            nodes_under=pack.under,
            say=say,
        )
        results = []
        for plan in plans:
            say(f"{plan.title}:")
            results.append(await run(plan, rig))
            keep(results[-1], args.profile)
        return results


def api_url() -> str:
    return "http://{}:{}".format(
        os.environ.get("CARE_API_HOST", "127.0.0.1"), os.environ.get("CARE_API_PORT", "8080")
    )


def keep(result: Result, profile: str) -> None:
    """Put what a run measured on disk at once: a long run is not lost to a later mishap."""
    RAW.mkdir(parents=True, exist_ok=True)
    kept = {
        "started_at": result.started_at.isoformat(),
        "ended_at": result.ended_at.isoformat(),
        "outages": [dataclasses.asdict(outage) for outage in result.outages],
        "offered": [dataclasses.asdict(one) for one in result.offered],
        "samples": [dataclasses.asdict(sample) for sample in result.samples],
        "generator_lag": result.generator_lag,
        "drained_after": result.drained_after,
        "facts": dict(result.facts),
        "restoration": dict(result.restoration),
    }
    path = RAW / f"raw-{profile}-{result.plan.name}.json"
    path.write_text(json.dumps(kept, default=str), encoding="utf-8")
    say(f"  kept what was measured in {path}")


def customers_in(data_dir: Path) -> str:
    """How many customers the pack holds, as its manifest says."""
    try:
        manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
        return f"{int(manifest['customers']):,}"
    except (OSError, ValueError, KeyError):
        return "an unknown number of"


if __name__ == "__main__":
    sys.exit(asyncio.run(main(arguments())))
