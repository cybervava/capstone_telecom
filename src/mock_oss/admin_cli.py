"""The simulator's controls from a terminal, for demos and for trying the platform out.

Each command is one call to the gateway's admin API (the calls the smoke run makes). With the
container stack nothing is needed on the host, and the command is the same on Windows, macOS
and Linux:

    docker compose exec mock-oss mock-oss outage-start --node-id BLR-OLT-0001

On the stack run on the host: ``uv run --env-file .env mock-oss outage-start --node-id ...``.

The admin key is read from ``OSS_KEY_ADMIN`` and never printed. A command prints the gateway's
answer as JSON and exits 0; 1 if the gateway refused the request; 2 if it could not be asked.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, get_args
from urllib.parse import quote

import httpx

from mock_oss.chaos import Mode, Target
from mock_oss.config import DEFAULT_PORT
from mock_oss.schemas import Cause, ServiceType

ADMIN = "/admin/v1"
TIMEOUT_SECONDS = 10.0
COMMANDS = frozenset(
    {
        "customers",
        "outages",
        "outage-start",
        "outage-update",
        "outage-resolve",
        "line-fault",
        "chaos",
        "chaos-list",
        "chaos-clear",
        "messages",
    }
)


@dataclass(frozen=True)
class Call:
    """One request to the gateway."""

    method: str
    path: str
    params: dict[str, Any] = field(default_factory=dict)
    body: dict[str, Any] | None = None


Builder = Callable[[argparse.Namespace], Call]


def _given(values: Mapping[str, Any]) -> dict[str, Any]:
    """The options that were given: the gateway's own defaults stand for the rest."""
    return {name: value for name, value in values.items() if value is not None}


def _segment(identifier: str) -> str:
    return quote(identifier, safe="")


def _customers(args: argparse.Namespace) -> Call:
    params = {
        "service_type": args.service_type,
        "mapping_defect": args.mapping_defect,
        "node_id": args.node_id,
        "router_model": args.router_model,
        "segment": args.segment,
        "consent": args.consent,
        "status": args.status,
        "service_status": args.service_status,
        "min_monthly_fee": args.min_monthly_fee,
        "limit": args.limit,
    }
    return Call("GET", f"{ADMIN}/customers/search", _given(params))


def _outages(args: argparse.Namespace) -> Call:
    if args.active is None:
        return Call("GET", f"{ADMIN}/outages")
    return Call("GET", f"{ADMIN}/outages", {"active": "true" if args.active else "false"})


def _outage_start(args: argparse.Namespace) -> Call:
    body = _given(
        {
            "node_id": args.node_id,
            "cause": args.cause,
            "planned": args.planned,
            "eta_minutes": args.eta_minutes,
            "detect_after_seconds": args.detect_after_seconds,
            "started_minutes_ago": args.started_minutes_ago,
        }
    )
    return Call("POST", f"{ADMIN}/outages", body=body)


def _outage_update(args: argparse.Namespace) -> Call:
    path = f"{ADMIN}/outages/{_segment(args.outage_id)}/update"
    return Call("POST", path, body={"eta_minutes": args.eta_minutes})


def _outage_resolve(args: argparse.Namespace) -> Call:
    return Call("POST", f"{ADMIN}/outages/{_segment(args.outage_id)}/resolve")


def _line_fault(args: argparse.Namespace) -> Call:
    body: dict[str, Any] = {"service_id": args.service_id, "fault": not args.clear}
    if not args.clear:
        body["result"] = "INCONCLUSIVE" if args.inconclusive else "FAIL"
    return Call("POST", f"{ADMIN}/line-faults", body=body)


def _chaos(args: argparse.Namespace) -> Call:
    body = _given(
        {
            "target": args.target,
            "mode": args.mode,
            "latency_ms": args.latency_ms,
            "error_rate": args.error_rate,
            "hold_seconds": args.hold_seconds,
            "ttl_seconds": args.for_seconds,
        }
    )
    return Call("POST", f"{ADMIN}/chaos", body=body)


def _chaos_list(_args: argparse.Namespace) -> Call:
    return Call("GET", f"{ADMIN}/chaos")


def _chaos_clear(_args: argparse.Namespace) -> Call:
    return Call("DELETE", f"{ADMIN}/chaos")


def _messages(args: argparse.Namespace) -> Call:
    params = {"outage_id": args.outage_id, "customer_id": args.customer_id, "limit": args.limit}
    return Call("GET", "/notify/v1/messages", _given(params))


def add_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the simulator controls to the ``mock-oss`` command line."""
    customers = sub.add_parser("customers", help="find synthetic customers to play")
    customers.add_argument("--service-type", choices=get_args(ServiceType))
    customers.add_argument("--mapping-defect", choices=("none", "missing", "ambiguous", "stale"))
    customers.add_argument("--node-id", help="the node that really serves the line")
    customers.add_argument("--router-model")
    customers.add_argument("--segment", choices=("consumer", "sme"))
    customers.add_argument("--consent", choices=("true", "false"))
    customers.add_argument("--status", choices=("active", "suspended"), help="the account's")
    customers.add_argument("--service-status", choices=("active", "suspended"), help="the line's")
    customers.add_argument("--min-monthly-fee", metavar="INR")
    customers.add_argument("--limit", type=int, default=5)
    customers.set_defaults(build=_customers)

    outages = sub.add_parser("outages", help="list the outages")
    which = outages.add_mutually_exclusive_group()
    which.add_argument("--active", dest="active", action="store_const", const=True)
    which.add_argument("--resolved", dest="active", action="store_const", const=False)
    outages.set_defaults(build=_outages, active=None)

    start = sub.add_parser(
        "outage-start", help="start an outage on a node (it is announced on the NOC feed)"
    )
    start.add_argument("--node-id", required=True)
    start.add_argument("--cause", choices=get_args(Cause), default="FIBRE_CUT")
    start.add_argument("--planned", action="store_true", default=None)
    start.add_argument("--eta-minutes", type=int, help="the NOC's estimate (default 180)")
    start.add_argument(
        "--detect-after-seconds", type=float, help="how long before the NOC declares it"
    )
    start.add_argument(
        "--started-minutes-ago",
        type=int,
        help="backdate the start: a longer outage is owed a larger credit",
    )
    start.set_defaults(build=_outage_start)

    update = sub.add_parser("outage-update", help="give an outage a new ETA")
    update.add_argument("outage_id")
    update.add_argument("--eta-minutes", type=int, required=True)
    update.set_defaults(build=_outage_update)

    resolve = sub.add_parser("outage-resolve", help="end an outage")
    resolve.add_argument("outage_id")
    resolve.set_defaults(build=_outage_resolve)

    line = sub.add_parser("line-fault", help="make a line fail its remote test, or pass again")
    line.add_argument("service_id")
    outcome = line.add_mutually_exclusive_group()
    outcome.add_argument("--clear", action="store_true", help="the line passes again")
    outcome.add_argument("--inconclusive", action="store_true", help="the test cannot tell")
    line.set_defaults(build=_line_fault)

    chaos = sub.add_parser(
        "chaos", help="make one API of the gateway fail (network down: the curveball)"
    )
    chaos.add_argument("target", choices=get_args(Target))
    chaos.add_argument("mode", choices=get_args(Mode))
    chaos.add_argument("--latency-ms", type=int)
    chaos.add_argument("--error-rate", type=float)
    chaos.add_argument("--hold-seconds", type=float, help="how long a timeout holds a call")
    chaos.add_argument("--for-seconds", type=float, help="end the failure after this long")
    chaos.set_defaults(build=_chaos)

    sub.add_parser("chaos-list", help="list the failures in force").set_defaults(build=_chaos_list)
    sub.add_parser("chaos-clear", help="end every failure").set_defaults(build=_chaos_clear)

    messages = sub.add_parser(
        "messages", help="read the SMS and push messages the gateway was asked to send"
    )
    messages.add_argument("--outage-id")
    messages.add_argument("--customer-id")
    messages.add_argument("--limit", type=int, default=10)
    messages.set_defaults(build=_messages)


def gateway_url(environ: Mapping[str, str]) -> str:
    """``MOCK_OSS_URL`` if set, else the gateway on this machine at ``MOCK_OSS_PORT``."""
    explicit = environ.get("MOCK_OSS_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    port = environ.get("MOCK_OSS_PORT", "").strip() or str(DEFAULT_PORT)
    return f"http://127.0.0.1:{port}"


def _render(response: httpx.Response) -> str:
    try:
        return json.dumps(response.json(), indent=2, ensure_ascii=False)
    except ValueError:
        return response.text


def execute(
    args: argparse.Namespace,
    *,
    client: httpx.Client | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Send the call the parsed command stands for and print the answer."""
    env = os.environ if environ is None else environ
    key = env.get("OSS_KEY_ADMIN", "").strip()
    if not key:
        sys.stderr.write(
            "OSS_KEY_ADMIN is not set. Run the command in the mock-oss container "
            "(docker compose exec mock-oss mock-oss ...) or with `uv run --env-file .env`.\n"
        )
        return 2
    build: Builder = args.build
    call = build(args)
    # The client's line for every request is not wanted on a demo screen.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    http = client or httpx.Client(base_url=gateway_url(env), timeout=TIMEOUT_SECONDS)
    try:
        response = http.request(
            call.method,
            call.path,
            params=call.params or None,
            json=call.body,
            headers={"X-API-Key": key},
        )
    except httpx.HTTPError as exc:
        sys.stderr.write(f"Could not reach the gateway at {http.base_url}: {type(exc).__name__}\n")
        return 2
    finally:
        if client is None:
            http.close()
    if response.is_success:
        sys.stdout.write(_render(response) + "\n")
        return 0
    sys.stderr.write(f"The gateway refused it ({response.status_code}):\n{_render(response)}\n")
    return 1


def main(
    argv: Sequence[str] | None = None,
    *,
    client: httpx.Client | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """The simulator controls alone, for tests; ``mock-oss`` itself has them as subcommands."""
    parser = argparse.ArgumentParser(prog="mock-oss")
    add_commands(parser.add_subparsers(dest="command", required=True))
    return execute(parser.parse_args(argv), client=client, environ=environ)
