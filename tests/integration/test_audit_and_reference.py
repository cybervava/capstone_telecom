"""Audit trail immutability and mapping resolution."""

from __future__ import annotations

from uuid import uuid4

import pytest
from psycopg import errors

from skylink_care.db.audit import AuditEntry, AuditRepository
from skylink_care.db.database import Database
from skylink_care.db.reference import ReferenceRepository
from skylink_care.domain.models import MappingStatus


async def test_audit_entries_are_returned_in_order_with_versions(audit: AuditRepository) -> None:
    contact_id = uuid4()
    for step in ("triage.classify", "diagnostics.decide"):
        await audit.append(
            AuditEntry(
                correlation_id="corr-1",
                contact_id=contact_id,
                actor="agent:triage",
                step=step,
                outcome="OK",
                inputs={"text": "<masked>"},
                outputs={"intent": "connectivity"},
                model="offline",
                prompt_version="triage@abc123",
                policy_version="v0-placeholder",
                duration_ms=12,
            )
        )

    trail = await audit.for_contact(contact_id)

    assert [record.step for record in trail] == ["triage.classify", "diagnostics.decide"]
    assert trail[0].prompt_version == "triage@abc123"
    assert trail[0].outputs == {"intent": "connectivity"}


@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_log SET outcome = 'X'", "DELETE FROM audit_log", "TRUNCATE audit_log"],
)
async def test_audit_log_cannot_be_rewritten(
    audit: AuditRepository, db: Database, statement: str
) -> None:
    await audit.append(AuditEntry(correlation_id="c", actor="system", step="s", outcome="OK"))

    async with db.connection() as conn:
        with pytest.raises(errors.RaiseException, match="append-only"):
            await conn.execute(statement)  # type: ignore[arg-type]


async def seed_reference(db: Database) -> None:
    async with db.transaction() as conn:
        await conn.execute(
            "INSERT INTO nodes (node_id, node_type, service_type, parent_node_id, city, area,"
            " pincode, status) VALUES"
            " ('BLR-AGG-0001', 'AGG', NULL, NULL, 'BLR', 'Core', '560001', 'ACTIVE'),"
            " ('BLR-OLT-0001', 'OLT', 'broadband', 'BLR-AGG-0001', 'BLR', 'A', '560001', 'ACTIVE'),"
            " ('BLR-OLT-0002', 'OLT', 'broadband', 'BLR-AGG-0001', 'BLR', 'B', '560002', 'ACTIVE'),"
            " ('BLR-OLT-0009', 'OLT', 'broadband', 'BLR-AGG-0001', 'BLR', 'C', '560003',"
            "  'DECOMMISSIONED')"
        )
        await conn.execute(
            "INSERT INTO address_node_map (address_id, service_type, node_id, source, valid_from,"
            " valid_to, map_version) VALUES"
            " ('A-GOOD', 'broadband', 'BLR-OLT-0001', 'inventory', '2024-01-01', NULL, 'v1'),"
            " ('A-GOOD', 'broadband', 'BLR-OLT-0002', 'gis', '2022-01-01', '2023-12-31', 'v1'),"
            " ('A-AMBIG', 'broadband', 'BLR-OLT-0001', 'inventory', '2024-01-01', NULL, 'v1'),"
            " ('A-AMBIG', 'broadband', 'BLR-OLT-0002', 'gis', '2024-01-01', NULL, 'v1'),"
            " ('A-STALE', 'broadband', 'BLR-OLT-0009', 'manual', '2021-01-01', NULL, 'v1'),"
            " ('A-GHOST', 'broadband', 'BLR-OLT-4040', 'manual', '2021-01-01', NULL, 'v1')"
        )
        await conn.execute(
            "INSERT INTO service_index (service_id, customer_id, service_type, address_id, status)"
            " VALUES ('S1', 'C1', 'broadband', 'A-GOOD', 'active'),"
            " ('S2', 'C2', 'broadband', 'A-AMBIG', 'active'),"
            " ('S3', 'C3', 'broadband', 'A-GOOD', 'suspended')"
        )


async def test_single_current_row_to_an_active_node_resolves(
    db: Database, reference: ReferenceRepository
) -> None:
    await seed_reference(db)

    mapping = await reference.resolve_mapping("A-GOOD", "broadband")

    assert mapping.status is MappingStatus.RESOLVED
    assert mapping.node_id == "BLR-OLT-0001"
    assert mapping.map_version == "v1"
    assert mapping.is_resolved


@pytest.mark.parametrize(
    ("address_id", "status", "candidates"),
    [
        ("A-NONE", MappingStatus.MISSING, ()),
        ("A-AMBIG", MappingStatus.AMBIGUOUS, ("BLR-OLT-0001", "BLR-OLT-0002")),
        ("A-STALE", MappingStatus.STALE, ("BLR-OLT-0009",)),
        ("A-GHOST", MappingStatus.STALE, ("BLR-OLT-4040",)),
    ],
    ids=["no row", "two current rows", "decommissioned node", "node not in inventory"],
)
async def test_defective_mappings_are_reported_not_guessed(
    db: Database,
    reference: ReferenceRepository,
    address_id: str,
    status: MappingStatus,
    candidates: tuple[str, ...],
) -> None:
    await seed_reference(db)

    mapping = await reference.resolve_mapping(address_id, "broadband")

    assert mapping.status is status
    assert mapping.node_id is None
    assert mapping.candidate_node_ids == candidates
    assert not mapping.is_resolved


async def test_mapping_is_per_service_type(db: Database, reference: ReferenceRepository) -> None:
    await seed_reference(db)

    assert (await reference.resolve_mapping("A-GOOD", "mobile")).status is MappingStatus.MISSING


async def test_fan_out_lists_only_active_unambiguously_mapped_services(
    db: Database, reference: ReferenceRepository
) -> None:
    await seed_reference(db)

    services = await reference.services_on_node("BLR-OLT-0001", "broadband")

    assert [(ref.service_id, ref.customer_id) for ref in services] == [("S1", "C1")]


async def test_node_lookup_returns_the_parent(db: Database, reference: ReferenceRepository) -> None:
    await seed_reference(db)

    node = await reference.get_node("BLR-OLT-0001")

    assert node is not None
    assert node.parent_node_id == "BLR-AGG-0001"
    assert await reference.get_node("NOPE") is None
