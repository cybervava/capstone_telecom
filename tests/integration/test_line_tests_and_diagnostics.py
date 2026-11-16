"""Line-test records and the diagnostics agent, with Network Monitoring scripted.

The agent's rule: gather evidence, never invent it. A test that could not be run is no result,
and a node the mapping cannot name is found by asking the line, not by guessing.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from skylink_care.adapters.models import LineTestDto, NodeStatusDto
from skylink_care.adapters.result import Err, ErrorKind, Ok, Result
from skylink_care.adapters.shared import SharedNodeStatus
from skylink_care.agents.diagnostics import DiagnosticsAgent, LineTestPolicy, Subject
from skylink_care.db.audit import AuditRepository
from skylink_care.db.contacts import ContactRepository, NewContact
from skylink_care.db.database import Database
from skylink_care.db.line_tests import LineTest, LineTestRepository, NodeLines
from skylink_care.db.read_model import ReadModelRepository
from skylink_care.db.reference import ReferenceRepository
from skylink_care.domain.models import Channel, MappingStatus, NetworkState
from skylink_care.domain.network import DecisionLimits, DecisionReason
from skylink_care.observability.audit import Auditor
from tests.integration.test_audit_and_reference import seed_reference
from tests.integration.test_read_model_and_timers import outage_event

NOW = datetime(2026, 11, 15, 4, 35, tzinfo=UTC)
NODE = "BLR-OLT-0001"
OTHER = "BLR-OLT-0002"
LIMITS = DecisionLimits(
    status_max_age=timedelta(seconds=60),
    feed_max_age=timedelta(seconds=90),
    line_test_cluster_threshold=3,
    line_own_fault_rate=0.02,
    line_test_cluster_chance=0.001,
)
POLICY = LineTestPolicy(cluster_window=timedelta(minutes=15), retest_after=timedelta(minutes=2))


def line_result(
    service_id: str, result: str, *, node_id: str = NODE, minutes_ago: float = 0
) -> LineTest:
    return LineTest(
        test_id=f"LT-{uuid4().hex[:10]}",
        service_id=service_id,
        node_id=node_id,
        result=result,
        tested_at=NOW - timedelta(minutes=minutes_ago),
    )


def operational(node_id: str) -> Result[NodeStatusDto]:
    return Ok(
        NodeStatusDto(node_id=node_id, status="OPERATIONAL", observed_at=NOW, feed_seq=0),
        status_code=200,
    )


def in_outage(node_id: str) -> Result[NodeStatusDto]:
    return Ok(
        NodeStatusDto.model_validate(
            {
                "node_id": node_id,
                "status": "OUTAGE",
                "outage": {
                    "outage_id": "OUT-1",
                    "version": 1,
                    "cause": "FIBRE_CUT",
                    "planned": False,
                    "started_at": NOW - timedelta(minutes=5),
                },
                "observed_at": NOW,
                "feed_seq": 3,
            }
        ),
        status_code=200,
    )


class ScriptedNetwork:
    def __init__(
        self,
        statuses: dict[str, Result[NodeStatusDto]],
        line: Result[LineTestDto] | None = None,
    ) -> None:
        self._statuses = statuses
        self._line = line or Err(ErrorKind.UNAVAILABLE, "line tests are down", status_code=503)
        self.status_calls: list[str] = []
        self.line_calls: list[str] = []

    async def node_status(self, node_id: str, *, correlation_id: str) -> Result[NodeStatusDto]:
        self.status_calls.append(node_id)
        return self._statuses.get(node_id, Err(ErrorKind.TIMEOUT, "no answer"))

    async def line_test(self, service_id: str, *, correlation_id: str) -> Result[LineTestDto]:
        self.line_calls.append(service_id)
        return self._line


def line_answer(node_id: str, result: str = "PASS", service_id: str = "S1") -> Result[LineTestDto]:
    return Ok(
        LineTestDto.model_validate(
            {
                "test_id": f"LT-{uuid4().hex[:10]}",
                "service_id": service_id,
                "node_id": node_id,
                "result": result,
                "checks": {"link": "UP", "sync": True, "packet_loss_pct": 0.1},
                "tested_at": NOW,
            }
        ),
        status_code=200,
    )


def agent(db: Database, network: ScriptedNetwork) -> DiagnosticsAgent:
    return DiagnosticsAgent(
        network,  # type: ignore[arg-type]
        ReferenceRepository(db),
        ReadModelRepository(db),
        LineTestRepository(db),
        LIMITS,
        POLICY,
        lambda: NOW,
    )


def subject(
    address_id: str = "A-GOOD", service_id: str = "S1", *, contact_id: UUID | None = None
) -> Subject:
    return Subject(
        contact_id=contact_id,
        service_id=service_id,
        service_type="broadband",
        address_id=address_id,
    )


async def a_contact(contacts: ContactRepository) -> UUID:
    intake = await contacts.accept(
        NewContact(
            channel=Channel.CHAT,
            client_request_id=uuid4().hex,
            text="internet down",
            correlation_id=uuid4().hex,
            received_at=NOW,
        )
    )
    return intake.contact_id


def auditor(db: Database) -> Auditor:
    return Auditor(AuditRepository(db), contact_id=None, correlation_id=uuid4().hex)


# --- records -------------------------------------------------------------------------------------


async def test_lines_are_counted_once_each_by_their_latest_result(db: Database) -> None:
    tests = LineTestRepository(db)
    since = NOW - timedelta(minutes=15)
    for record in (
        line_result("S1", "FAIL", minutes_ago=9),
        line_result("S1", "FAIL", minutes_ago=5),  # the same line again: still one line
        line_result("S2", "FAIL", minutes_ago=8),
        line_result("S3", "FAIL", minutes_ago=7),
        line_result("S3", "PASS", minutes_ago=1),  # recovered: no longer failing
        line_result("S4", "INCONCLUSIVE", minutes_ago=2),
        line_result("S5", "FAIL", minutes_ago=30),  # before the window
        line_result("S6", "FAIL", node_id=OTHER, minutes_ago=3),
    ):
        await tests.record(record, contact_id=None)

    # S1 and S2 fail; S3 passes now; S4 said nothing either way and counts as neither.
    assert await tests.lines_on_node(NODE, since=since) == NodeLines(failing=2, passing=1)
    assert await tests.lines_on_node(OTHER, since=since) == NodeLines(failing=1, passing=0)
    assert await tests.lines_on_node("BLR-OLT-0099", since=since) == NodeLines(0, 0)


async def test_line_found_on_another_node_since_is_not_counted_on_the_old_one(
    db: Database,
) -> None:
    tests = LineTestRepository(db)
    since = NOW - timedelta(minutes=15)
    await tests.record(line_result("S1", "FAIL", minutes_ago=9), contact_id=None)
    await tests.record(line_result("S1", "FAIL", node_id=OTHER, minutes_ago=2), contact_id=None)

    assert await tests.lines_on_node(NODE, since=since) == NodeLines(failing=0, passing=0)
    assert await tests.lines_on_node(OTHER, since=since) == NodeLines(failing=1, passing=0)


async def test_customer_with_several_lines_on_a_node_counts_once(db: Database) -> None:
    await seed_reference(db)
    async with db.transaction() as conn:
        await conn.execute(
            "INSERT INTO service_index (service_id, customer_id, service_type, address_id, status)"
            " VALUES ('S10', 'C1', 'broadband', 'A-GOOD', 'active'),"
            " ('S11', 'C1', 'broadband', 'A-GOOD', 'active')"
        )
    tests = LineTestRepository(db)
    for service_id in ("S1", "S10", "S11"):  # all three belong to customer C1
        await tests.record(line_result(service_id, "FAIL", minutes_ago=3), contact_id=None)
    await tests.record(line_result("S-UNLISTED", "FAIL", minutes_ago=3), contact_id=None)

    lines = await tests.lines_on_node(NODE, since=NOW - timedelta(minutes=15))

    assert lines.failing == 2  # customer C1, and the line whose owner is not on record


async def test_customer_with_one_line_failing_and_one_passing_counts_as_failing_only(
    db: Database,
) -> None:
    await seed_reference(db)
    async with db.transaction() as conn:
        await conn.execute(
            "INSERT INTO service_index (service_id, customer_id, service_type, address_id, status)"
            " VALUES ('S10', 'C1', 'broadband', 'A-GOOD', 'active')"
        )
    tests = LineTestRepository(db)
    await tests.record(line_result("S1", "FAIL", minutes_ago=3), contact_id=None)  # C1's
    await tests.record(line_result("S10", "PASS", minutes_ago=3), contact_id=None)  # C1's too
    await tests.record(line_result("S-OTHER", "PASS", minutes_ago=3), contact_id=None)

    lines = await tests.lines_on_node(NODE, since=NOW - timedelta(minutes=15))

    assert lines == NodeLines(failing=1, passing=1)


async def test_contact_finds_its_own_recent_result_and_nobody_elses(
    db: Database, contacts: ContactRepository
) -> None:
    tests = LineTestRepository(db)
    mine, theirs = await a_contact(contacts), await a_contact(contacts)
    older, newer = (
        line_result("S1", "FAIL", minutes_ago=10),
        line_result("S1", "PASS", minutes_ago=3),
    )
    await tests.record(older, contact_id=mine)
    await tests.record(newer, contact_id=mine)
    await tests.record(newer, contact_id=mine)  # the same test stored twice changes nothing

    recent = await tests.latest_for_contact(mine, "S1", since=NOW - timedelta(minutes=5))

    assert recent == newer
    assert await tests.latest_for_contact(mine, "S1", since=NOW - timedelta(minutes=1)) is None
    assert await tests.latest_for_contact(mine, "S9", since=NOW - timedelta(hours=1)) is None
    # Another conversation about the same line does not inherit the result.
    assert await tests.latest_for_contact(theirs, "S1", since=NOW - timedelta(hours=1)) is None


# --- the agent -----------------------------------------------------------------------------------


async def test_clear_node_gets_a_line_test_and_its_result_is_recorded(db: Database) -> None:
    await seed_reference(db)
    network = ScriptedNetwork({NODE: operational(NODE)}, line_answer(NODE, "FAIL"))

    diagnosis = await agent(db, network).run(subject(), audit=auditor(db))

    assert diagnosis.decision.state is NetworkState.CONFIRMED_CLEAR
    assert diagnosis.line_test is not None
    assert diagnosis.line_test.result == "FAIL"
    assert diagnosis.failing_lines_on_node == 1
    assert (network.status_calls, network.line_calls) == ([NODE], ["S1"])


async def test_no_line_test_is_run_when_the_live_status_reports_an_outage(db: Database) -> None:
    await seed_reference(db)
    network = ScriptedNetwork({NODE: in_outage(NODE)}, line_answer(NODE))

    diagnosis = await agent(db, network).run(subject(), audit=auditor(db))

    assert diagnosis.decision.state is NetworkState.ACTIVE
    assert (diagnosis.line_test, network.line_calls) == (None, [])


async def test_no_line_test_is_run_when_only_the_feed_knows_of_the_outage(db: Database) -> None:
    await seed_reference(db)
    model = ReadModelRepository(db)
    async with db.transaction() as conn:
        await model.apply(conn, outage_event(1))
        await model.record_poll(
            conn, applied_seq=1, source_last_seq=1, heartbeat_at=NOW, polled_at=NOW
        )
    network = ScriptedNetwork({}, line_answer(NODE))  # the status call times out

    diagnosis = await agent(db, network).run(subject(), audit=auditor(db))

    assert diagnosis.decision.reason is DecisionReason.FEED_OUTAGE
    assert network.line_calls == []


async def test_missing_mapping_is_repaired_from_the_node_the_line_test_names(db: Database) -> None:
    await seed_reference(db)
    network = ScriptedNetwork({OTHER: in_outage(OTHER)}, line_answer(OTHER, "FAIL"))

    diagnosis = await agent(db, network).run(subject("A-NOWHERE"), audit=auditor(db))

    assert (diagnosis.mapping.status, diagnosis.mapping.node_id) == (MappingStatus.REPAIRED, OTHER)
    assert diagnosis.decision.state is NetworkState.ACTIVE
    assert network.status_calls == [OTHER]
    assert network.line_calls == ["S1"]  # the repair test is not repeated for the customer


async def test_mapping_to_equipment_the_gateway_calls_retired_is_repaired_the_same_way(
    db: Database,
) -> None:
    """The reference data still lists the node as active; Network Monitoring knows better."""
    await seed_reference(db)
    gone: Result[NodeStatusDto] = Err(
        ErrorKind.GONE, "decommissioned", status_code=410, code="NODE_DECOMMISSIONED"
    )
    network = ScriptedNetwork({NODE: gone, OTHER: operational(OTHER)}, line_answer(OTHER))

    diagnosis = await agent(db, network).run(subject(), audit=auditor(db))

    assert (diagnosis.mapping.status, diagnosis.mapping.node_id) == (MappingStatus.REPAIRED, OTHER)
    assert diagnosis.decision.state is NetworkState.CONFIRMED_CLEAR
    assert network.status_calls == [NODE, OTHER]


async def test_mapping_that_cannot_be_repaired_stays_unresolved(db: Database) -> None:
    await seed_reference(db)
    network = ScriptedNetwork({})  # the line test cannot be run either

    diagnosis = await agent(db, network).run(subject("A-AMBIG", "S2"), audit=auditor(db))

    assert diagnosis.mapping.status is MappingStatus.AMBIGUOUS
    assert diagnosis.decision.reason is DecisionReason.MAPPING_UNRESOLVED
    assert (diagnosis.line_test, network.status_calls) == (None, [])


async def test_recent_result_is_reused_within_a_contact_unless_a_fresh_one_is_asked_for(
    db: Database, contacts: ContactRepository
) -> None:
    await seed_reference(db)
    network = ScriptedNetwork({NODE: operational(NODE)}, line_answer(NODE, "PASS"))
    diagnostics = agent(db, network)
    mine, another = await a_contact(contacts), await a_contact(contacts)
    earlier = line_result("S1", "FAIL", minutes_ago=1)
    await LineTestRepository(db).record(earlier, contact_id=mine)

    reused = await diagnostics.run(subject(contact_id=mine), audit=auditor(db))
    fresh = await diagnostics.run(subject(contact_id=mine), audit=auditor(db), fresh_line_test=True)
    new_complaint = await diagnostics.run(subject(contact_id=another), audit=auditor(db))

    assert reused.line_test == earlier
    assert fresh.line_test is not None
    assert fresh.line_test.result == "PASS"
    assert new_complaint.line_test is not None
    assert new_complaint.line_test.result == "PASS"  # tested for itself, not handed the old FAIL
    assert network.line_calls == ["S1", "S1"]  # one reuse, two real tests


async def test_stale_word_of_an_outage_does_not_spare_the_line_its_test(db: Database) -> None:
    """The feed once knew of an outage but has gone quiet; the live status says clear.

    The decision does not trust the stale feed, so the line's own state is still worth knowing.
    """
    await seed_reference(db)
    model = ReadModelRepository(db)
    async with db.transaction() as conn:
        await model.apply(conn, outage_event(1))
        await model.record_poll(
            conn,
            applied_seq=1,
            source_last_seq=1,
            heartbeat_at=NOW - timedelta(minutes=30),
            polled_at=NOW - timedelta(minutes=30),
        )
    network = ScriptedNetwork({NODE: operational(NODE)}, line_answer(NODE, "PASS"))

    diagnosis = await agent(db, network).run(subject(), audit=auditor(db))

    assert diagnosis.decision.state is NetworkState.CONFIRMED_CLEAR
    assert diagnosis.line_test is not None
    assert network.line_calls == ["S1"]


async def test_confident_mapping_the_line_contradicts_is_corrected(db: Database) -> None:
    """The mapping names a healthy node; the line is really served by one that is in outage."""
    await seed_reference(db)
    network = ScriptedNetwork(
        {NODE: operational(NODE), OTHER: in_outage(OTHER)}, line_answer(OTHER, "FAIL")
    )

    diagnosis = await agent(db, network).run(subject(), audit=auditor(db))

    assert (diagnosis.mapping.status, diagnosis.mapping.node_id) == (MappingStatus.REPAIRED, OTHER)
    assert diagnosis.decision.state is NetworkState.ACTIVE
    assert diagnosis.decision.node_id == OTHER
    assert network.status_calls == [NODE, OTHER]
    assert network.line_calls == ["S1"]


async def test_node_the_reference_data_does_not_know_repairs_nothing(db: Database) -> None:
    await seed_reference(db)
    retired = ScriptedNetwork({}, line_answer("BLR-OLT-0009"))  # known, but decommissioned
    unknown = ScriptedNetwork({NODE: operational(NODE)}, line_answer("ZZZ-OLT-9999"))

    unresolved = await agent(db, retired).run(subject("A-NOWHERE"), audit=auditor(db))
    kept = await agent(db, unknown).run(subject(), audit=auditor(db))

    assert unresolved.mapping.status is MappingStatus.MISSING
    assert unresolved.decision.reason is DecisionReason.MAPPING_UNRESOLVED
    assert (kept.mapping.status, kept.mapping.node_id) == (MappingStatus.RESOLVED, NODE)


async def test_failed_line_test_call_leaves_the_node_evidence_standing(db: Database) -> None:
    await seed_reference(db)
    network = ScriptedNetwork({NODE: operational(NODE)})

    diagnosis = await agent(db, network).run(subject(), audit=auditor(db))

    assert diagnosis.line_test is None
    assert diagnosis.decision.state is NetworkState.CONFIRMED_CLEAR  # the node evidence stands
    assert network.line_calls == ["S1"]


async def test_enough_lines_failing_on_the_node_overrules_a_live_all_clear(db: Database) -> None:
    await seed_reference(db)
    tests = LineTestRepository(db)
    for service_id in ("S7", "S8"):
        await tests.record(line_result(service_id, "FAIL", minutes_ago=4), contact_id=None)
    network = ScriptedNetwork({NODE: operational(NODE)}, line_answer(NODE, "FAIL"))

    diagnosis = await agent(db, network).run(subject(), audit=auditor(db))

    assert diagnosis.failing_lines_on_node == 3
    assert diagnosis.decision.state is NetworkState.UNKNOWN
    assert diagnosis.decision.reason is DecisionReason.SUSPECTED_NODE_FAULT


async def test_a_few_lines_failing_among_many_that_pass_leave_the_live_all_clear_standing(
    db: Database, contacts: ContactRepository
) -> None:
    """The wave after an outage: the platform has just tested every waiting line on the node,
    and three customers have faults of their own. That is not evidence against the node."""
    await seed_reference(db)
    tests = LineTestRepository(db)
    for service_id in ("S7", "S8"):
        await tests.record(line_result(service_id, "FAIL", minutes_ago=4), contact_id=None)
    for number in range(20):
        await tests.record(line_result(f"S-OK-{number}", "PASS", minutes_ago=3), contact_id=None)
    network = ScriptedNetwork({NODE: operational(NODE)}, line_answer(NODE, "FAIL"))
    contact_id = await a_contact(contacts)
    audit = Auditor(AuditRepository(db), contact_id=contact_id, correlation_id=uuid4().hex)

    diagnosis = await agent(db, network).run(subject(contact_id=contact_id), audit=audit)

    assert (diagnosis.failing_lines_on_node, diagnosis.passing_lines_on_node) == (3, 20)
    assert diagnosis.decision.state is NetworkState.CONFIRMED_CLEAR
    assert diagnosis.decision.reason is DecisionReason.LIVE_CLEAR
    trail = await AuditRepository(db).for_contact(contact_id)
    (decided,) = [record for record in trail if record.step == "diagnostics.decide"]
    assert (decided.inputs["failing_lines_on_node"], decided.inputs["passing_lines_on_node"]) == (
        3,
        20,
    )
    assert (decided.inputs["live_alarm"], decided.inputs["feed_alarm"]) == (False, False)


# --- sharing a status read, and when not to ------------------------------------------------------


class HeldNetwork(ScriptedNetwork):
    """A status API whose answers wait until the test lets them go."""

    def __init__(self, statuses: dict[str, Result[NodeStatusDto]]) -> None:
        super().__init__(statuses, line_answer(NODE))
        self.go = asyncio.Event()

    async def node_status(self, node_id: str, *, correlation_id: str) -> Result[NodeStatusDto]:
        self.status_calls.append(node_id)
        answer = self._statuses[node_id]  # what the node was when the read was sent
        await self.go.wait()
        return answer


def sharing_agent(db: Database, network: ScriptedNetwork) -> DiagnosticsAgent:
    return DiagnosticsAgent(
        network,  # type: ignore[arg-type]
        ReferenceRepository(db),
        ReadModelRepository(db),
        LineTestRepository(db),
        LIMITS,
        POLICY,
        lambda: NOW,
        shared=SharedNodeStatus(network),  # type: ignore[arg-type]
    )


async def test_diagnoses_of_one_node_at_the_same_moment_share_the_status_read(
    db: Database,
) -> None:
    await seed_reference(db)
    network = HeldNetwork({NODE: in_outage(NODE)})
    agent_ = sharing_agent(db, network)

    both = [
        asyncio.create_task(agent_.run(subject(service_id=f"S{n}"), audit=auditor(db)))
        for n in (1, 2)
    ]
    await asyncio.sleep(0.05)
    network.go.set()
    first, second = await asyncio.gather(*both)

    assert network.status_calls == [NODE]
    assert first.decision.state is second.decision.state is NetworkState.ACTIVE


async def test_turn_woken_by_the_feed_reads_the_status_for_itself(db: Database) -> None:
    """Found by review: a contact woken by "outage resolved" could join a read that was sent
    before the resolution, be told the outage was still on, and wait a quarter of an hour for
    the next look."""
    await seed_reference(db)
    network = HeldNetwork({NODE: in_outage(NODE)})
    agent_ = sharing_agent(db, network)

    earlier = asyncio.create_task(agent_.run(subject(service_id="S1"), audit=auditor(db)))
    await asyncio.sleep(0.05)  # its read is on its way, with the node still in outage
    network._statuses[NODE] = operational(NODE)  # resolved; the feed has said so
    woken = asyncio.create_task(
        agent_.run(subject(service_id="S2"), audit=auditor(db), own_status_read=True)
    )
    await asyncio.sleep(0.05)
    network.go.set()
    before = await earlier
    after = await woken

    assert network.status_calls == [NODE, NODE]
    assert before.decision.state is NetworkState.ACTIVE
    assert after.decision.state is not NetworkState.ACTIVE
