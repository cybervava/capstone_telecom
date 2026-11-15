"""Run every generation stage in order and write the manifest."""

from __future__ import annotations

import logging
from typing import Any

from skylink_datagen.config import GenerateConfig, derive_rng
from skylink_datagen.contacts import ContactPlanner, transcript_count
from skylink_datagen.events import build_events, render_events
from skylink_datagen.network import OUTAGE_COLUMNS, generate_outages
from skylink_datagen.nodes import NODE_COLUMNS, build_topology
from skylink_datagen.reference import generate_reference
from skylink_datagen.transcripts import build_transcripts
from skylink_datagen.writer import build_manifest, write_csv, write_jsonl, write_manifest

log = logging.getLogger(__name__)
FILE_ORDER = (
    "nodes.csv", "addresses.csv", "customers.csv", "services.csv", "address_node_map.csv",
    "truth_service_node.csv", "outages.csv", "network_events.jsonl", "transcripts.jsonl",
)  # fmt: skip


def run_generate(cfg: GenerateConfig) -> dict[str, Any]:
    out, seed = cfg.out, cfg.seed
    out.mkdir(parents=True, exist_ok=True)
    rows: dict[str, int] = {}

    topology = build_topology(cfg.customers, derive_rng(seed, "nodes"))
    rows["nodes.csv"] = write_csv(
        out / "nodes.csv", NODE_COLUMNS, (n.row() for n in topology.nodes)
    )
    log.info("nodes: %d", rows["nodes.csv"])

    ref = generate_reference(out, cfg.customers, topology, seed, cfg.rates)
    rows.update(ref.rows)
    log.info("reference: %s", ref.rows)

    outages = generate_outages(cfg.customers, topology, derive_rng(seed, "outages"))
    rows["outages.csv"] = write_csv(out / "outages.csv", OUTAGE_COLUMNS, (o.row() for o in outages))
    pending = build_events(outages, topology, derive_rng(seed, "events"))
    events = render_events(pending, derive_rng(seed, "event-ids"))
    rows["network_events.jsonl"] = write_jsonl(out / "network_events.jsonl", events)
    log.info("outages: %d, events: %d", rows["outages.csv"], rows["network_events.jsonl"])

    planner = ContactPlanner(ref, topology, outages, derive_rng(seed, "contacts"))
    contacts = planner.plan(transcript_count(cfg.customers))
    records = build_transcripts(
        contacts, ref, derive_rng(seed, "outcomes"), derive_rng(seed, "dialogue")
    )
    rows["transcripts.jsonl"] = write_jsonl(out / "transcripts.jsonl", records)
    log.info("transcripts: %d", rows["transcripts.jsonl"])

    ordered = {name: rows[name] for name in FILE_ORDER}
    manifest = build_manifest(out, seed, cfg.customers, cfg.rates.as_dict(), ordered)
    write_manifest(out, manifest)
    return manifest
