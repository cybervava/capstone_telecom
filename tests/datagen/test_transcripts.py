"""transcripts.jsonl: every constraint from section 3 of the data contract."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import timedelta
from typing import Any

import pytest

from tests.datagen.helpers import Dataset, ts

FIELDS = {
    "contact_id", "ts", "channel", "customer_id", "service_id", "label", "outage_id",
    "first_customer_message", "transcript", "legacy_resolution", "technician_visit",
    "visit_necessary", "repeat_within_7d",
}  # fmt: skip
RESOLUTIONS = {"reboot_script", "technician_visit", "resolved_on_call", "billing_adjustment",
               "escalated"}  # fmt: skip
IDENTIFIER = re.compile(r"C\d{7}|@example\.com|[6-9]\d{9}")
BILLING_CUES = ("bill", "charge", "refund", "payment", "paid", "plan", "invoice", "deduct",
                "recharge", "amount", "fee", "paisa", "paise")  # fmt: skip


def _of(data: Dataset, label: str) -> list[dict[str, Any]]:
    return [t for t in data.transcripts if t["label"] == label]


def _true_node(data: Dataset, record: dict[str, Any]) -> str:
    return data.truth[record["service_id"]]["true_node_id"]


def test_fields_ids_and_basic_types(data: Dataset) -> None:
    records = data.transcripts
    assert len(records) == 500
    for i, rec in enumerate(records, start=1):
        assert set(rec) == FIELDS
        assert rec["contact_id"] == f"H{i:07d}"
        assert rec["channel"] in {"chat", "call"}
        assert rec["label"] in {"outage", "device", "billing"}
        assert rec["legacy_resolution"] in RESOLUTIONS
        assert ts(rec["ts"]).utcoffset() == timedelta(hours=5, minutes=30)
        assert data.services[rec["service_id"]]["customer_id"] == rec["customer_id"]
        assert isinstance(rec["technician_visit"], bool)
        assert isinstance(rec["repeat_within_7d"], bool)
    stamps = [ts(r["ts"]) for r in records]
    assert stamps == sorted(stamps)
    assert ts("2026-05-01T00:00:00+05:30") <= stamps[0]
    assert stamps[-1] <= ts("2026-10-31T23:59:59+05:30")


def test_label_and_channel_mix(data: Dataset) -> None:
    n = len(data.transcripts)
    labels = Counter(t["label"] for t in data.transcripts)
    assert labels["outage"] / n == pytest.approx(0.45, abs=0.01)
    assert labels["device"] / n == pytest.approx(0.35, abs=0.01)
    assert labels["billing"] / n == pytest.approx(0.20, abs=0.01)
    chat = sum(t["channel"] == "chat" for t in data.transcripts) / n
    assert chat == pytest.approx(0.40, abs=0.06)


def test_visit_fields_are_consistent(data: Dataset) -> None:
    for rec in data.transcripts:
        assert (rec["visit_necessary"] is None) == (not rec["technician_visit"])
        assert (rec["legacy_resolution"] == "technician_visit") == rec["technician_visit"]
        if rec["technician_visit"]:
            assert data.services[rec["service_id"]]["service_type"] == "broadband"
        if rec["label"] != "outage":
            assert rec["outage_id"] is None


def test_outage_contacts_fall_inside_an_outage_on_the_true_node(data: Dataset) -> None:
    outage_records = _of(data, "outage")
    before = 0
    for rec in outage_records:
        outage = data.outages[rec["outage_id"]]
        moment = ts(rec["ts"])
        assert ts(outage["started_at"]) <= moment <= ts(outage["resolved_at"])
        assert _true_node(data, rec) in outage["affected_node_ids"].split("|")
        service_type = data.services[rec["service_id"]]["service_type"]
        assert service_type in outage["service_types"].split("|")
        before += moment < ts(outage["detected_at"])
    assert before / len(outage_records) == pytest.approx(0.20, abs=0.01)


def test_thirty_percent_of_outage_contacts_end_in_unnecessary_visit(data: Dataset) -> None:
    outage_records = _of(data, "outage")
    visits = [r for r in outage_records if r["technician_visit"]]
    assert len(visits) / len(outage_records) == pytest.approx(0.30, abs=0.01)
    assert all(r["visit_necessary"] is False for r in visits)


def test_device_contacts_have_no_outage_and_mostly_necessary_visits(data: Dataset) -> None:
    device = _of(data, "device")
    for rec in device:
        moment = ts(rec["ts"])
        windows = data.node_outage_windows.get(_true_node(data, rec), [])
        assert not any(start <= moment <= end for start, end, _ in windows), rec["contact_id"]
    visits = [r for r in device if r["technician_visit"]]
    assert len(visits) / len(device) == pytest.approx(0.25, abs=0.02)
    necessary = sum(r["visit_necessary"] is True for r in visits) / len(visits)
    assert necessary == pytest.approx(0.70, abs=0.05)


def test_billing_contacts_have_no_visit(data: Dataset) -> None:
    for rec in _of(data, "billing"):
        assert rec["technician_visit"] is False
        assert rec["visit_necessary"] is None


def test_transcript_shape(data: Dataset) -> None:
    for rec in data.transcripts:
        lines = rec["transcript"].split("\n")
        assert 4 <= len(lines) <= 10
        assert all(line.startswith(("Customer: ", "Agent: ")) for line in lines)
        assert lines[0] == f"Customer: {rec['first_customer_message']}"
        assert any(line.startswith("Agent: ") for line in lines)


def test_identifier_share_and_no_stray_identifiers(data: Dataset) -> None:
    with_pii = 0
    for rec in data.transcripts:
        customer = data.customers[rec["customer_id"]]
        own = (customer["phone"][3:], customer["customer_id"], customer["email"])
        mentions_own = any(value in rec["transcript"] for value in own)
        with_pii += mentions_own
        assert bool(IDENTIFIER.search(rec["transcript"])) == mentions_own, rec["contact_id"]
    assert with_pii / len(data.transcripts) == pytest.approx(0.30, abs=0.01)


def test_repeat_flag_matches_actual_contacts(data: Dataset) -> None:
    by_customer: dict[str, list[Any]] = defaultdict(list)
    for rec in data.transcripts:
        by_customer[rec["customer_id"]].append(ts(rec["ts"]))
    for rec in data.transcripts:
        moment = ts(rec["ts"])
        later = any(
            timedelta(0) < other - moment <= timedelta(days=7)
            for other in by_customer[rec["customer_id"]]
        )
        assert rec["repeat_within_7d"] == later, rec["contact_id"]


@pytest.mark.parametrize(("label", "rate"), [("outage", 0.25), ("device", 0.12), ("billing", 0.06)])
def test_repeat_rates(data: Dataset, label: str, rate: float) -> None:
    records = _of(data, label)
    share = sum(r["repeat_within_7d"] for r in records) / len(records)
    assert share == pytest.approx(rate, abs=0.01)


@pytest.mark.parametrize("label", ["outage", "device", "billing"])
def test_openings_are_varied(data: Dataset, label: str) -> None:
    openings = {r["first_customer_message"] for r in _of(data, label)}
    assert len(openings) >= 40


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def test_outage_and_device_openings_overlap_but_billing_is_distinct(data: Dataset) -> None:
    openings = {label: [r["first_customer_message"] for r in _of(data, label)]
                for label in ("outage", "device", "billing")}  # fmt: skip

    def billing_share(texts: list[str]) -> float:
        return sum(any(cue in t.lower() for cue in BILLING_CUES) for t in texts) / len(texts)

    assert billing_share(openings["billing"]) >= 0.95
    assert billing_share(openings["outage"]) <= 0.03
    assert billing_share(openings["device"]) <= 0.03
    vocab = {label: set().union(*map(_tokens, texts)) for label, texts in openings.items()}
    jaccard = len(vocab["outage"] & vocab["device"]) / len(vocab["outage"] | vocab["device"])
    assert jaccard >= 0.6
    for phrase in ("not working", "red light"):
        for label in ("outage", "device"):
            assert any(phrase in t.lower() for t in openings[label]), (phrase, label)


def test_romanised_hindi_variants_present(data: Dataset) -> None:
    assert any("nahi" in r["first_customer_message"].lower() for r in data.transcripts)


def test_legacy_agents_reboot_outage_customers(data: Dataset) -> None:
    outage = _of(data, "outage")
    rebooted = sum(
        any(w in r["transcript"].lower() for w in ("restart", "switch off", "reboot", "power off"))
        for r in outage
    )
    assert rebooted / len(outage) >= 0.8
    for rec in outage:
        agent_lines = [ln for ln in rec["transcript"].split("\n") if ln.startswith("Agent: ")]
        assert not any("outage" in ln.lower() for ln in agent_lines), rec["contact_id"]
