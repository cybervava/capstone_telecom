"""Live smoke test against the running local stack (`scripts/dev_stack.sh start`).

Drives real journeys over HTTP and checks what the customer sees and what reached the enterprise
systems:

1. a customer on a node with a declared outage, and the neighbours who are told before they call,
2. another customer on that node while the Network Monitoring API is down (the event feed still
   proves the outage),
3. a customer on a different node in the same conditions (no proof either way: no all-clear),
4. the outage is resolved: the waiting customer is told, confirms, and the contact closes,
5. a customer whose own line fails its remote test while the node looks healthy: the fault is
   watched for the NOC's detection window, and no repair is arranged meanwhile.

Run with:  uv run --env-file .env python scripts/smoke.py
Exits non-zero if any check fails. It changes simulator state and restores it at the end.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx

API = "http://{}:{}".format(
    os.environ.get("CARE_API_HOST", "127.0.0.1"), os.environ.get("CARE_API_PORT", "8080")
)
OSS = os.environ.get("CARE_OSS_BASE_URL", "http://127.0.0.1:8081")
WAIT_SECONDS = 20.0
SEARCH = {"service_type": "broadband", "mapping_defect": "none", "status": "active"}
failures: list[str] = []


def say(text: str) -> None:
    sys.stdout.write(f"{text}\n")


def check(name: str, passed: bool, detail: str = "") -> None:
    say(f"  [{'ok' if passed else 'FAIL'}] {name}{f' — {detail}' if detail else ''}")
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


def eventually[T](what: str, probe: Callable[[], T | None]) -> T:
    """Poll until ``probe`` returns something, or fail with what was being waited for."""
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        found = probe()
        if found:
            return found
        time.sleep(0.3)
    raise TimeoutError(f"{what}: nothing within {WAIT_SECONDS}s")


def outbound(contact: dict[str, Any]) -> list[str]:
    return [m["body"] for m in contact["messages"] if m["direction"] == "outbound"]


def wait_for_replies(
    api: httpx.Client, headers: dict[str, str], contact_id: str, count: int = 1
) -> dict[str, Any]:
    def probe() -> dict[str, Any] | None:
        contact = api.get(f"/v1/contacts/{contact_id}", headers=headers).json()["data"]
        return dict(contact) if len(outbound(contact)) >= count else None

    return eventually(f"reply {count} for contact {contact_id}", probe)


def wait_for_status(
    api: httpx.Client, headers: dict[str, str], contact_id: str, wanted: str
) -> bool:
    """A step stores its message a moment before the contact's status, so the status is awaited."""

    def probe() -> bool:
        contact = api.get(f"/v1/contacts/{contact_id}", headers=headers).json()["data"]
        return bool(contact["status"] == wanted)

    try:
        return eventually(f"status {wanted} for contact {contact_id}", probe)
    except TimeoutError:
        return False


def messages_for(sim: httpx.Client, outage_id: str, template_id: str) -> list[dict[str, Any]]:
    found = sim.get("/notify/v1/messages", params={"outage_id": outage_id, "limit": 200}).json()
    return [m for m in found["messages"] if m["template_id"] == template_id]


def decision_reason(api: httpx.Client, contact_id: str) -> str:
    trail = api.get(f"/v1/contacts/{contact_id}/audit", headers=token(api, "ops")).json()["data"]
    decisions = [step for step in trail if step["step"] == "diagnostics.decide"]
    return str(decisions[-1]["outputs"]["reason"]) if decisions else "none"


def pick_customers(sim: httpx.Client) -> tuple[dict[str, Any], ...]:
    """Two customers on one node, and two more on two other nodes."""
    people = sim.get("/admin/v1/customers/search", params={**SEARCH, "limit": 50}).json()[
        "customers"
    ]
    first = people[0]
    node_id = first["true_node_id"]
    same = sim.get(
        "/admin/v1/customers/search", params={**SEARCH, "node_id": node_id, "limit": 5}
    ).json()["customers"]
    second = next(p for p in same if p["customer_id"] != first["customer_id"])
    elsewhere = next(p for p in people if p["true_node_id"] != node_id)
    third_node = next(
        p for p in people if p["true_node_id"] not in {node_id, elsewhere["true_node_id"]}
    )
    return first, second, elsewhere, third_node


def declared_outage(
    api: httpx.Client, sim: httpx.Client, customer: dict[str, Any], outage_id: str
) -> tuple[dict[str, str], dict[str, Any]]:
    headers = token(api, "customer", customer["customer_id"])
    accepted, elapsed_ms = open_contact(
        api, headers, "My internet is down, red light on the router"
    )
    check("reference issued at intake", bool(accepted["complaint_ref"]), accepted["complaint_ref"])
    check("reference visible well inside 2 minutes", elapsed_ms < 5000, f"{elapsed_ms:.0f} ms")
    contact = wait_for_replies(api, headers, accepted["contact_id"])
    reply = outbound(contact)[-1]
    check("reply names the outage", outage_id in reply)
    check("reply carries the reference", accepted["complaint_ref"] in reply)
    check("reply gives a sourced ETA", "expected back by" in reply)
    check("contact is marked ACTIVE", contact["network_state"] == "ACTIVE")
    time.sleep(1.5)  # let the executor finish its writes
    tables = sim.get("/admin/v1/state").json()["tables"]
    check("one incident opened for the node", tables["incidents"] == 1, str(tables["incidents"]))
    check("complaint registered", tables["complaints"] == 1)
    check("complaint linked to the incident", tables["incident_complaints"] == 1)
    check("no technician booked", tables["appointments"] == 0)
    trail = api.get(
        f"/v1/contacts/{accepted['contact_id']}/audit", headers=token(api, "ops")
    ).json()["data"]
    check("audit trail recorded", len(trail) >= 5, f"{len(trail)} steps")
    say(f"\n     assistant: {reply}")

    notices = eventually(
        "proactive notifications",
        lambda: messages_for(sim, outage_id, "outage_started.v1") or None,
    )
    recipients = [m["customer_id"] for m in notices]
    check("neighbours were told before they called", len(notices) >= 1, f"{len(notices)} sent")
    check("nobody was told twice", len(recipients) == len(set(recipients)))
    say(f"\n     sms/push: {notices[0]['body']}")
    return headers, accepted


def api_down_feed_up(
    api: httpx.Client, sim: httpx.Client, customer: dict[str, Any], outage_id: str
) -> None:
    headers = token(api, "customer", customer["customer_id"])
    accepted, elapsed_ms = open_contact(api, headers, "internet not working")
    check("reference still issued", bool(accepted["complaint_ref"]), f"{elapsed_ms:.0f} ms")
    contact = wait_for_replies(api, headers, accepted["contact_id"])
    reply = outbound(contact)[-1]
    check("reply still names the outage", outage_id in reply)
    check(
        "the evidence came from the event feed",
        decision_reason(api, accepted["contact_id"]) == "FEED_OUTAGE",
    )
    time.sleep(1.0)
    tables = sim.get("/admin/v1/state").json()["tables"]
    check(
        "still one incident, no technician", (tables["incidents"], tables["appointments"]) == (1, 0)
    )


def api_down_no_evidence(api: httpx.Client, customer: dict[str, Any]) -> None:
    headers = token(api, "customer", customer["customer_id"])
    accepted, _ = open_contact(api, headers, "wifi not working")
    contact = wait_for_replies(api, headers, accepted["contact_id"])
    reply = outbound(contact)[-1]
    check("reply says status cannot be verified", "can't verify the live network status" in reply)
    check("no all-clear is given", "no outage has been reported" not in reply.lower())
    check("no device steps are given", "restart" not in reply.lower())
    check("contact is marked UNKNOWN", contact["network_state"] == "UNKNOWN")
    say(f"\n     assistant: {reply}")


def restoration(
    api: httpx.Client,
    sim: httpx.Client,
    headers: dict[str, str],
    accepted: dict[str, Any],
    outage_id: str,
) -> None:
    sim.post(f"/admin/v1/outages/{outage_id}/resolve").raise_for_status()
    contact = wait_for_replies(api, headers, accepted["contact_id"], count=2)
    reply = outbound(contact)[-1]
    check("waiting customer is told the outage is resolved", "resolved the outage" in reply)
    check(
        "contact now waits for the customer",
        wait_for_status(api, headers, accepted["contact_id"], "AWAITING_CUSTOMER"),
    )
    say(f"\n     assistant: {reply}")
    restored = eventually(
        "restoration notifications",
        lambda: messages_for(sim, outage_id, "outage_resolved.v1") or None,
    )
    # Newest first, so "started" must come after "resolved" in each customer's own list.
    in_order = [
        [
            m["template_id"]
            for m in sim.get(
                "/notify/v1/messages",
                params={"customer_id": notice["customer_id"], "outage_id": outage_id},
            ).json()["messages"]
        ]
        == ["outage_resolved.v1", "outage_started.v1"]
        for notice in restored[:10]
    ]
    check(
        "restored follows started for each customer told",
        all(in_order),
        f"{len(in_order)} customers checked",
    )

    api.post(
        f"/v1/contacts/{accepted['contact_id']}/messages",
        json={"text": "working now, thanks", "client_message_id": uuid.uuid4().hex},
        headers=headers,
    ).raise_for_status()
    wait_for_replies(api, headers, accepted["contact_id"], count=3)
    check(
        "customer's confirmation closes the contact",
        wait_for_status(api, headers, accepted["contact_id"], "CLOSED"),
    )
    incidents = sim.get("/incident/v1/incidents").json()["incidents"]
    check("the node's incident is resolved", [i["status"] for i in incidents] == ["RESOLVED"])


def failing_line_is_held(api: httpx.Client, sim: httpx.Client, customer: dict[str, Any]) -> None:
    sim.post(
        "/admin/v1/line-faults", json={"service_id": customer["service_id"], "fault": True}
    ).raise_for_status()
    headers = token(api, "customer", customer["customer_id"])
    accepted, _ = open_contact(api, headers, "my broadband is not working")
    contact = wait_for_replies(api, headers, accepted["contact_id"])
    reply = outbound(contact)[-1]
    check("reply reports the failed line test", "found a fault" in reply)
    check("reply says a wider problem is being ruled out first", "wider network problem" in reply)
    check("node is confirmed clear", contact["network_state"] == "CONFIRMED_CLEAR")
    check(
        "contact is held, not handed to dispatch",
        wait_for_status(api, headers, accepted["contact_id"], "AWAITING_NETWORK"),
    )
    tables = sim.get("/admin/v1/state").json()["tables"]
    check("no technician booked", tables["appointments"] == 0)
    say(f"\n     assistant: {reply}")


def main() -> int:
    admin_key = os.environ.get("OSS_KEY_ADMIN")
    if not admin_key:
        sys.stderr.write("OSS_KEY_ADMIN is not set; run with --env-file .env\n")
        return 2
    api = httpx.Client(base_url=API, timeout=10.0)
    sim = httpx.Client(base_url=OSS, timeout=10.0, headers={"X-API-Key": admin_key})
    sim.post("/admin/v1/reset").raise_for_status()
    first, second, elsewhere, on_a_healthy_node = pick_customers(sim)
    node_id = first["true_node_id"]
    outage_id = None
    try:
        say(f"\n1. Declared outage on {node_id}")
        outage_id = sim.post(
            "/admin/v1/outages", json={"node_id": node_id, "cause": "FIBRE_CUT", "eta_minutes": 150}
        ).json()["outage_id"]
        headers, accepted = declared_outage(api, sim, first, outage_id)

        say("\n2. Network Monitoring API down: the event feed still proves the outage")
        sim.post("/admin/v1/chaos", json={"target": "network", "mode": "down"}).raise_for_status()
        api_down_feed_up(api, sim, second, outage_id)

        say(f"\n3. Same failure, a customer on {elsewhere['true_node_id']}: no proof either way")
        api_down_no_evidence(api, elsewhere)

        say("\n4. The outage is resolved")
        sim.delete("/admin/v1/chaos")
        restoration(api, sim, headers, accepted, outage_id)

        say(f"\n5. A failing line on healthy node {on_a_healthy_node['true_node_id']}")
        failing_line_is_held(api, sim, on_a_healthy_node)
    finally:
        sim.delete("/admin/v1/chaos")
        sim.post(
            "/admin/v1/line-faults",
            json={"service_id": on_a_healthy_node["service_id"], "fault": False},
        )
        if outage_id:
            sim.post(f"/admin/v1/outages/{outage_id}/resolve")
    say(f"\n{'ALL CHECKS PASSED' if not failures else f'{len(failures)} CHECK(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
