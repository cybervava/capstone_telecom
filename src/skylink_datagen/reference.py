"""Customers, addresses, services, the defective address-node map and its ground truth.

Everything is streamed to disk in one pass over customers; only a compact index of
services (customer, type, true node) is kept in memory for the contact-history stage.
"""

from __future__ import annotations

import random
from array import array
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import accumulate
from pathlib import Path

from skylink_datagen.config import (
    IST,
    WINDOW_END,
    DefectRates,
    derive_rng,
    fmt_bool,
    fmt_ts,
    random_date,
    random_moment,
)
from skylink_datagen.mapping import MappingWriter
from skylink_datagen.nodes import Topology
from skylink_datagen.router_catalog import MODEL_IDS, MODEL_WEIGHTS
from skylink_datagen.wordlists import (
    BUILDING_KINDS,
    CITIES,
    CITY_WEIGHTS,
    FIRST_NAMES,
    LAST_NAMES,
    STREET_KINDS,
    STREET_NAMES,
)
from skylink_datagen.writer import CsvSink

ADDRESS_COLUMNS = ("address_id", "line1", "area", "city", "pincode")
CUSTOMER_COLUMNS = (
    "customer_id", "full_name", "phone", "email", "segment", "status", "created_on",
    "consent_outage_notifications", "consent_channel", "consent_updated_at",
)  # fmt: skip
SERVICE_COLUMNS = (
    "service_id", "customer_id", "service_type", "plan_name", "monthly_fee_usd", "status",
    "address_id", "router_model", "router_serial", "activated_on", "billing_cycle_day",
)  # fmt: skip

CONSUMER_BB = (("Fibre 100", "599.00", 30), ("Fibre 300", "999.00", 40),
               ("Fibre 500", "1499.00", 22), ("Fibre 1G", "3999.00", 8))  # fmt: skip
SME_BB = (("Biz Fibre 500", "4999.00", 50), ("Biz Fibre 1G", "9999.00", 35),
          ("Biz Leased 1G", "14999.00", 15))  # fmt: skip
MOBILE = (("Prepaid 28", "299.00", 50), ("Postpaid 499", "499.00", 35),
          ("Postpaid 999", "999.00", 15))  # fmt: skip
BOTH, BROADBAND_ONLY = 0.27, 0.18  # => 45% broadband, 82% mobile
CREATED_FROM, LAST_DAY = date(2016, 1, 1), WINDOW_END.date()
PHONE_SPACE, PHONE_MULT, PHONE_OFFSET = 4_000_000_000, 2_654_435_761, 1_234_567
SERIAL_SPACE, SERIAL_MULT = 16**10, 0x9E3779B97


def phone_for(index: int) -> str:
    """Bijective index -> phone mapping, so phones are unique by construction."""
    return f"+91{6_000_000_000 + (index * PHONE_MULT + PHONE_OFFSET) % PHONE_SPACE}"


def customer_id(index: int) -> str:
    return f"C{index + 1:07d}"


def service_id(index: int) -> str:
    return f"S{index + 1:07d}"


@dataclass(frozen=True)
class ReferenceIndex:
    """Compact per-service / per-customer arrays used by later stages."""

    service_customer: array[int]
    service_is_mobile: array[int]
    service_node: array[int]
    node_services: dict[int, array[int]]
    customer_first: array[int]
    customer_email: tuple[str, ...]
    rows: dict[str, int]


@dataclass(frozen=True)
class Customer:
    index: int
    first: int
    email: str
    segment: str
    suspended: bool
    created_on: date
    city: str
    area: int


def _cum(weights: tuple[float, ...] | tuple[int, ...]) -> list[float]:
    return list(accumulate(float(w) for w in weights))


CITY_CUM = _cum(CITY_WEIGHTS)
MODEL_CUM = _cum(MODEL_WEIGHTS)


def _pick_plan(rng: random.Random, plans: tuple[tuple[str, str, int], ...]) -> tuple[str, str]:
    name, fee, _ = rng.choices(plans, cum_weights=_cum(tuple(p[2] for p in plans)))[0]
    return name, fee


def _address_line(rng: random.Random) -> str:
    street = f"{rng.choice(STREET_NAMES)} {rng.choice(STREET_KINDS)}"
    if rng.random() < 0.45:
        building = f"{rng.choice(STREET_NAMES)} {rng.choice(BUILDING_KINDS)}"
        return f"Flat {rng.randint(1, 18)}{rng.randint(1, 12):02d}, {building}, {street}"
    return f"No. {rng.randint(1, 999)}, {street}"


class ReferenceBuilder:
    """Streams one customer at a time into the five reference files."""

    def __init__(self, topology: Topology, seed: int, rates: DefectRates) -> None:
        self._topology = topology
        self._rng = {s: derive_rng(seed, f"reference:{s}") for s in ("cust", "addr", "svc")}
        self._mapping = MappingWriter(topology, derive_rng(seed, "reference:map"), rates)
        self._email_counts: dict[tuple[int, int], int] = {}
        self._svc_customer: array[int] = array("i")
        self._svc_mobile: array[int] = array("b")
        self._svc_node: array[int] = array("i")
        self._node_services: dict[int, array[int]] = {}
        self._first: array[int] = array("H")
        self._emails: list[str] = []
        self._broadband = 0

    def _customer(self, index: int) -> tuple[Customer, tuple[str, ...]]:
        rng = self._rng["cust"]
        city = rng.choices(CITIES, cum_weights=CITY_CUM)[0]
        area = rng.randrange(len(self._topology.areas[city]))
        first, last = rng.randrange(len(FIRST_NAMES)), rng.randrange(len(LAST_NAMES))
        n = self._email_counts.get((first, last), 0) + 1
        self._email_counts[(first, last)] = n
        email = f"{FIRST_NAMES[first].lower()}.{LAST_NAMES[last].lower()}{n}@example.com"
        segment = "sme" if rng.random() < 0.08 else "consumer"
        suspended = rng.random() < 0.03
        created = random_date(rng, CREATED_FROM, LAST_DAY - timedelta(days=14))
        consent = rng.random() < 0.78
        channel = ("sms" if rng.random() < 0.65 else "push") if consent else "none"
        since = datetime.combine(created, time(0, 0), tzinfo=IST)
        row = (
            customer_id(index),
            f"{FIRST_NAMES[first]} {LAST_NAMES[last]}",
            phone_for(index),
            email,
            segment,
            "suspended" if suspended else "active",
            created.isoformat(),
            fmt_bool(consent),
            channel,
            fmt_ts(random_moment(rng, since, WINDOW_END)),
        )
        self._first.append(first)
        self._emails.append(email)
        return Customer(index, first, email, segment, suspended, created, city, area), row

    def _service_row(self, cust: Customer, sid: int, mobile: bool) -> tuple[str, ...]:
        rng = self._rng["svc"]
        if mobile:
            plan, fee = _pick_plan(rng, MOBILE)
            model = serial = ""
        else:
            plan, fee = _pick_plan(rng, SME_BB if cust.segment == "sme" else CONSUMER_BB)
            model = rng.choices(MODEL_IDS, cum_weights=MODEL_CUM)[0]
            mixed = (self._broadband * SERIAL_MULT + 0x5A5A5) % SERIAL_SPACE
            serial = f"{model[:2]}{mixed:010X}"
            self._broadband += 1
        suspended = cust.suspended or rng.random() < 0.01
        activated = random_date(rng, cust.created_on, LAST_DAY - timedelta(days=7))
        return (service_id(sid), customer_id(cust.index), "mobile" if mobile else "broadband",
                plan, fee, "suspended" if suspended else "active", f"A{cust.index + 1:07d}",
                model, serial, activated.isoformat(), str(rng.randint(1, 28)))  # fmt: skip

    def _true_node(self, cust: Customer, mobile: bool) -> int:
        stype = "mobile" if mobile else "broadband"
        return self._rng["svc"].choice(self._topology.by_area[(cust.city, cust.area, stype)])

    def _record_service(self, cust: Customer, sid: int, mobile: bool, node: int) -> None:
        self._svc_customer.append(cust.index)
        self._svc_mobile.append(1 if mobile else 0)
        self._svc_node.append(node)
        self._node_services.setdefault(node, array("i")).append(sid)

    def _address_row(self, cust: Customer) -> tuple[str, ...]:
        area, pincode = self._topology.areas[cust.city][cust.area]
        line = _address_line(self._rng["addr"])
        return (f"A{cust.index + 1:07d}", line, area, cust.city, pincode)

    def _kinds(self) -> tuple[bool, ...]:
        roll = self._rng["cust"].random()
        if roll < BOTH:
            return (False, True)
        return (False,) if roll < BOTH + BROADBAND_ONLY else (True,)

    def run(self, out: Path, customers: int) -> ReferenceIndex:
        names = ("addresses.csv", "customers.csv", "services.csv")
        headers = (ADDRESS_COLUMNS, CUSTOMER_COLUMNS, SERVICE_COLUMNS)
        with ExitStack() as stack:
            addr, cust_sink, svc = (
                stack.enter_context(CsvSink(out / n, h))
                for n, h in zip(names, headers, strict=True)
            )
            mapping_sinks = self._mapping.open(stack, out)
            for index in range(customers):
                cust, row = self._customer(index)
                cust_sink.write(row)
                addr.write(self._address_row(cust))
                for mobile in self._kinds():
                    sid = len(self._svc_node)
                    srow = self._service_row(cust, sid, mobile)
                    svc.write(srow)
                    node = self._true_node(cust, mobile)
                    self._mapping.emit(mapping_sinks, srow, node)
                    self._record_service(cust, sid, mobile, node)
            rows = {n: s.rows for n, s in zip(names, (addr, cust_sink, svc), strict=True)}
            rows.update({n: s.rows for n, s in mapping_sinks.items()})
        return self._index(rows)

    def _index(self, rows: dict[str, int]) -> ReferenceIndex:
        return ReferenceIndex(
            service_customer=self._svc_customer,
            service_is_mobile=self._svc_mobile,
            service_node=self._svc_node,
            node_services=self._node_services,
            customer_first=self._first,
            customer_email=tuple(self._emails),
            rows=rows,
        )


def generate_reference(
    out: Path, customers: int, topology: Topology, seed: int, rates: DefectRates
) -> ReferenceIndex:
    return ReferenceBuilder(topology, seed, rates).run(out, customers)
