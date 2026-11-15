"""Live smoke test against the running local stack (`scripts/dev_stack.sh start`).

Drives two real journeys over HTTP and checks what the customer sees and what reached the
enterprise systems:

1. a customer on a node with a declared outage, and
2. another customer on the same node while the Network Monitoring API is down.

Run with:  uv run --env-file .env python scripts/smoke.py
Exits non-zero if any check fails. It changes simulator state and restores it at the end.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from typing import Any

import httpx

API = "http://{}:{}".format(
    os.environ.get("CARE_API_HOST", "127.0.0.1"), os.environ.get("CARE_API_PORT", "8080")
)
OSS = os.environ.get("CARE_OSS_BASE_URL", "http://127.0.0.1:8081")
WAIT_SECONDS = 20.0
failures: list[str] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    sys.stdout.write(f"  [{'ok' if passed else 'FAIL'}] {name}{f' — {detail}' if detail else ''}\n")
    if not passed:
        failures.append(name)


def token(api: httpx.Client, role: str, customer_id: str | None = None) -> dict[str, str]:
    body: dict[str, Any] = {"role": role}
    if customer_id:
        body["customer_id"] = customer_id
    value = api.post("/v1/dev/token", json=body).json()["data"]["token"]
    return {"Authorization": f"Bearer {value}"}


def open_contact(
    api: httpx.Client, headers: dict[str, str], text: str
) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    response = api.post(
        "/v1/contacts",
        json={"channel": "chat", "client_request_id": uuid.uuid4().hex, "text": text},
        headers=headers,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    return response.json()["data"], elapsed_ms


def wait_for_reply(api: httpx.Client, headers: dict[str, str], contact_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        contact = api.get(f"/v1/contacts/{contact_id}", headers=headers).json()["data"]
        if any(message["direction"] == "outbound" for message in contact["messages"]):
            return dict(contact)
        time.sleep(0.3)
    raise TimeoutError(f"no reply for contact {contact_id} within {WAIT_SECONDS}s")


def main() -> int:
    admin_key = os.environ.get("OSS_KEY_ADMIN")
    if not admin_key:
        sys.stderr.write("OSS_KEY_ADMIN is not set; run with --env-file .env\n")
        return 2
    api = httpx.Client(base_url=API, timeout=10.0)
    sim = httpx.Client(base_url=OSS, timeout=10.0, headers={"X-API-Key": admin_key})
    sim.post("/admin/v1/reset").raise_for_status()
    people = sim.get(
        "/admin/v1/customers/search",
        params={
            "service_type": "broadband",
            "mapping_defect": "none",
            "status": "active",
            "limit": 1,
        },
    ).json()["customers"]
    first = people[0]
    node_id = first["true_node_id"]
    neighbours = sim.get(
        "/admin/v1/customers/search",
        params={
            "service_type": "broadband",
            "mapping_defect": "none",
            "status": "active",
            "node_id": node_id,
            "limit": 5,
        },
    ).json()["customers"]
    second = next(person for person in neighbours if person["customer_id"] != first["customer_id"])
    outage_id = None
    try:
        sys.stdout.write(f"\n1. Declared outage on {node_id}\n")
        outage = sim.post(
            "/admin/v1/outages", json={"node_id": node_id, "cause": "FIBRE_CUT", "eta_minutes": 150}
        ).json()
        outage_id = outage["outage_id"]
        headers = token(api, "customer", first["customer_id"])
        accepted, elapsed_ms = open_contact(
            api, headers, "My internet is down, red light on the router"
        )
        check(
            "reference issued at intake", bool(accepted["complaint_ref"]), accepted["complaint_ref"]
        )
        check("reference visible well inside 2 minutes", elapsed_ms < 5000, f"{elapsed_ms:.0f} ms")
        contact = wait_for_reply(api, headers, accepted["contact_id"])
        reply = contact["messages"][-1]["body"]
        check("reply names the outage", outage_id in reply)
        check("reply carries the reference", accepted["complaint_ref"] in reply)
        check("reply gives a sourced ETA", "expected back by" in reply)
        check("contact is marked ACTIVE", contact["network_state"] == "ACTIVE")
        time.sleep(1.5)  # let the executor finish its writes
        tables = sim.get("/admin/v1/state").json()["tables"]
        check(
            "one incident opened for the node", tables["incidents"] == 1, str(tables["incidents"])
        )
        check("complaint registered", tables["complaints"] == 1)
        check("complaint linked to the incident", tables["incident_complaints"] == 1)
        check("no technician booked", tables["appointments"] == 0)
        trail = api.get(
            f"/v1/contacts/{accepted['contact_id']}/audit", headers=token(api, "ops")
        ).json()["data"]
        check("audit trail recorded", len(trail) >= 5, f"{len(trail)} steps")
        sys.stdout.write(f"\n     assistant: {reply}\n")

        sys.stdout.write("\n2. Network Monitoring API down during the outage\n")
        sim.post("/admin/v1/chaos", json={"target": "network", "mode": "down"}).raise_for_status()
        headers = token(api, "customer", second["customer_id"])
        accepted, elapsed_ms = open_contact(api, headers, "internet not working")
        check("reference still issued", bool(accepted["complaint_ref"]), f"{elapsed_ms:.0f} ms")
        contact = wait_for_reply(api, headers, accepted["contact_id"])
        reply = contact["messages"][-1]["body"]
        check(
            "reply says status cannot be verified", "can't verify the live network status" in reply
        )
        check("no all-clear is given", "no outage has been reported" not in reply.lower())
        check("contact is marked UNKNOWN", contact["network_state"] == "UNKNOWN")
        time.sleep(1.0)
        tables = sim.get("/admin/v1/state").json()["tables"]
        check(
            "still one incident, no technician",
            (tables["incidents"], tables["appointments"]) == (1, 0),
        )
        sys.stdout.write(f"\n     assistant: {reply}\n")
    finally:
        sim.delete("/admin/v1/chaos")
        if outage_id:
            sim.post(f"/admin/v1/outages/{outage_id}/resolve")
    sys.stdout.write(
        f"\n{'ALL CHECKS PASSED' if not failures else f'{len(failures)} CHECK(S) FAILED'}\n"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
