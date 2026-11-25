"""The MCP server: the platform's read-only tools, asked for the way an MCP client asks.

A client of the Model Context Protocol (a staff assistant, an IDE, another agent) is given
four things to ask and two documents to read. None of them writes to an enterprise system or
reads a customer's data, and each answers by the same rule the platform's own agents go by.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import Client, MCPError
from mcp.server import MCPServer

from mock_oss.clock import ManualClock
from skylink_care.adapters.gateway import OssGateway
from skylink_care.api.ratelimit import RateLimiter
from skylink_care.config import OssKeys, Settings
from skylink_care.db.database import Database
from skylink_care.db.reference import ReferenceRepository
from skylink_care.domain.credit import CreditInput, evaluate_credit, load_policy
from skylink_care.domain.models import ServiceType
from skylink_care.mcp_server.server import build_server
from skylink_care.mcp_server.tools import CareTools, build_tools
from skylink_care.pipelines.reference_loader import load_reference
from tests.support.stack import MOCK_URL, TEST_POLICY, Simulator

TOOLS = {"check_network_status", "search_router_guide", "quote_service_credit", "platform_status"}


@pytest.fixture
async def tools(
    db: Database,
    datapack: Path,
    gateway: OssGateway,
    care_settings: Settings,
    stack_clock: ManualClock,
) -> CareTools:
    await load_reference(db, datapack)
    return build_tools(care_settings, db, gateway, now=lambda: stack_clock.now().astimezone(UTC))


class Connected:
    """A client of the server, in memory. Every question opens a session of its own and closes
    it again, as a client that keeps no session does over HTTP."""

    def __init__(self, server: MCPServer) -> None:
        self._server = server

    async def list_tools(self) -> Any:
        async with Client(self._server) as client:
            return await client.list_tools()

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> Any:
        async with Client(self._server) as client:
            return await client.call_tool(tool, arguments)

    async def list_resources(self) -> Any:
        async with Client(self._server) as client:
            return await client.list_resources()

    async def read_resource(self, uri: str) -> Any:
        async with Client(self._server) as client:
            return await client.read_resource(uri)

    async def refused_to_read(self, uri: str) -> str:
        """What the client is told when a document is not given to it."""
        async with Client(self._server) as client:
            try:
                await client.read_resource(uri)
            except MCPError as refusal:
                return str(refusal)
        raise AssertionError(f"{uri} was read")

    async def instructions(self) -> str:
        async with Client(self._server) as client:
            return client.instructions or ""


@pytest.fixture
def client(tools: CareTools) -> Connected:
    return Connected(build_server(tools))


async def a_node(simulator: Simulator) -> str:
    customer = await simulator.find_customer(
        service_type="broadband", mapping_defect="none", status="active"
    )
    return str(customer["true_node_id"])


async def asked(client: Connected, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return dict(result.structured_content)


async def trail_so_far(db: Database) -> int:
    """The audit trail is never emptied: a test reads what was added after this point."""
    async with db.connection() as conn:
        cursor = await conn.execute("SELECT coalesce(max(audit_id), 0) AS last FROM audit_log")
        row = await cursor.fetchone()
    return int(row["last"]) if row else 0


async def calls_since(db: Database, last: int) -> list[dict[str, Any]]:
    async with db.connection() as conn:
        cursor = await conn.execute(
            "SELECT actor, step, outcome, inputs, outputs FROM audit_log"
            " WHERE audit_id > %s AND step LIKE 'mcp.%%' ORDER BY audit_id",
            (last,),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def refused(client: Connected, tool: str, **arguments: Any) -> str:
    result = await client.call_tool(tool, arguments)
    assert result.is_error, result.structured_content
    return " ".join(getattr(part, "text", "") for part in result.content)


# --- what a client is offered ----------------------------------------------------------------


async def test_a_client_is_offered_four_tools_and_each_only_reads(client: Connected) -> None:
    listed = (await client.list_tools()).tools

    assert {tool.name for tool in listed} == TOOLS
    for tool in listed:
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.destructive_hint is False, tool.name
        assert tool.description, tool.name


async def test_the_inputs_of_a_tool_are_typed_and_bounded_in_its_schema(client: Connected) -> None:
    by_name = {tool.name: tool.input_schema for tool in (await client.list_tools()).tools}

    quote = by_name["quote_service_credit"]["properties"]
    assert quote["service_type"]["enum"] == ["broadband", "mobile"]
    assert quote["outage_minutes"]["minimum"] == 0
    assert quote["outage_minutes"]["maximum"] == 60 * 24 * 92
    search = by_name["search_router_guide"]["properties"]
    assert search["problem"]["maxLength"] == 500
    assert "pattern" in by_name["check_network_status"]["properties"]["node_id"]


async def test_the_server_says_what_its_answers_may_be_used_for(client: Connected) -> None:
    said = await client.instructions()

    assert "never an all-clear" in said
    assert "word for word" in said
    assert "writes" in said


# --- the state of a node -----------------------------------------------------------------------


async def test_a_node_with_nothing_wrong_is_confirmed_clear_on_a_status_read_now(
    client: Connected, simulator: Simulator
) -> None:
    node = await a_node(simulator)

    status = await asked(client, "check_network_status", node_id=node)

    assert (status["node_id"], status["state"]) == (node, "CONFIRMED_CLEAR")
    assert status["source"] == "live_api"
    assert status["outage"] is None
    assert status["evidence_at"] is not None
    assert "one customer's own line" in status["meaning"]


async def test_a_declared_outage_is_told_with_its_cause_and_estimate(
    client: Connected, simulator: Simulator
) -> None:
    node = await a_node(simulator)
    outage = await simulator.start_outage(node, cause="FIBRE_CUT", eta_minutes=120)

    status = await asked(client, "check_network_status", node_id=node)

    assert status["state"] == "ACTIVE"
    assert status["outage"]["outage_id"] == outage["outage_id"]
    assert status["outage"]["cause"] == "FIBRE_CUT"
    assert status["outage"]["eta"] is not None
    assert "would not help" in status["meaning"]


async def test_a_status_that_cannot_be_read_is_unknown_and_said_not_to_be_an_all_clear(
    client: Connected, simulator: Simulator
) -> None:
    node = await a_node(simulator)
    await simulator.fault("network", "down")

    status = await asked(client, "check_network_status", node_id=node)

    assert status["state"] == "UNKNOWN"
    assert status["outage"] is None
    assert "not an all-clear" in status["meaning"]


async def test_a_node_the_platform_does_not_know_is_refused(client: Connected) -> None:
    said = await refused(client, "check_network_status", node_id="OLT-NOWHERE-999")

    assert "not a node the platform knows" in said


@pytest.mark.parametrize("node_id", ["", "OLT 1; DROP TABLE nodes", "x" * 80, "../admin"])
async def test_a_node_id_of_another_shape_never_reaches_the_tool(
    client: Connected, node_id: str
) -> None:
    said = await refused(client, "check_network_status", node_id=node_id)

    assert "node_id" in said


# --- the guide of a router -----------------------------------------------------------------------


async def test_the_sections_of_a_routers_guide_are_found_with_their_steps_and_source(
    client: Connected,
) -> None:
    found = await asked(
        client, "search_router_guide", router_model="AL-AX18", problem="wifi keeps dropping"
    )

    best = found["sections"][0]
    assert found["router_model"] == "AL-AX18"
    assert found["index_version"]
    assert best["citation"].startswith("guide-al-ax18 §wifi v")
    assert best["section"] == "Wi-Fi problems"
    assert "Passwords are case-sensitive." in " ".join(best["steps"])
    assert [section["score"] for section in found["sections"]] == sorted(
        (section["score"] for section in found["sections"]), reverse=True
    )


async def test_only_what_a_customer_may_follow_is_ever_found(client: Connected) -> None:
    found = await asked(
        client,
        "search_router_guide",
        router_model="AL-AX18",
        problem="how do I factory reset the router with the reset button and a paperclip",
    )

    said = str(found).lower()
    assert "paperclip" not in said
    assert all("factory-reset" not in section["citation"] for section in found["sections"])
    assert all(section["citation"].startswith("guide-al-ax18 ") for section in found["sections"])


async def test_a_router_without_a_guide_is_refused(client: Connected) -> None:
    said = await refused(
        client, "search_router_guide", router_model="ZZ-9000", problem="wifi keeps dropping"
    )

    assert "no guide" in said


# --- a credit under the policy ------------------------------------------------------------------


async def test_a_credit_is_quoted_by_the_policy_engine_with_its_working(
    client: Connected, care_settings: Settings
) -> None:
    quote = await asked(
        client,
        "quote_service_credit",
        service_type="broadband",
        monthly_fee_usd="999.00",
        outage_minutes=300,
    )

    now = datetime(2026, 11, 20, tzinfo=UTC)
    engine = evaluate_credit(
        load_policy(care_settings.credit_policy_path),
        CreditInput(
            service_type=ServiceType.BROADBAND,
            monthly_fee_usd=Decimal("999.00"),
            service_active=True,
            affected=True,
            planned=False,
            started_at=now - timedelta(minutes=300),
            resolved_at=now,
            prior_outage_credits_in_cycle_usd=Decimal(0),
            already_credited_for_outage=False,
        ),
    )
    assert quote["eligible"] is True
    assert quote["amount_usd"] == str(engine.amount_usd) == "50.00"
    assert quote["currency"] == "USD"
    assert quote["policy_version"] == "v0-placeholder"
    assert quote["working"]["billable_hours"] == 5
    assert quote["working"]["computed_usd"] == "6.94"
    assert quote["working"]["floor_applied"] is True
    assert "Nothing is applied" in quote["note"]
    # What the quotation takes for granted is said with it: the platform's own credit is
    # worked out from Billing's record, which may say otherwise.
    assert "assumes an active service" in quote["note"]
    assert "no earlier credit" in quote["note"]


async def test_a_credit_above_the_limit_is_quoted_as_a_supervisors_to_approve(
    client: Connected,
) -> None:
    quote = await asked(
        client,
        "quote_service_credit",
        service_type="broadband",
        monthly_fee_usd="4999",
        outage_minutes=3000,
    )

    assert (quote["amount_usd"], quote["approval"]) == ("520.73", "supervisor")


@pytest.mark.parametrize(
    ("asked_for", "reason"),
    [
        ({"outage_minutes": 60}, "BELOW_MINIMUM_DURATION"),
        ({"outage_minutes": 600, "planned": True}, "PLANNED_WORK"),
        ({"outage_minutes": 600, "credited_this_cycle_usd": "999.00"}, "CAP_REACHED"),
    ],
)
async def test_an_outage_the_policy_does_not_cover_is_quoted_as_nothing_with_the_reason(
    client: Connected, asked_for: dict[str, Any], reason: str
) -> None:
    quote = await asked(
        client,
        "quote_service_credit",
        service_type="broadband",
        monthly_fee_usd="999.00",
        **asked_for,
    )

    assert (quote["eligible"], quote["amount_usd"], quote["reason"]) == (False, "0.00", reason)
    assert quote["approval"] == "none"


@pytest.mark.parametrize(
    "bad",
    [
        {"monthly_fee_usd": "-5", "outage_minutes": 300},
        {"monthly_fee_usd": "999", "outage_minutes": -1},
        {"monthly_fee_usd": "999", "outage_minutes": 10**9},
        {"monthly_fee_usd": "a lot", "outage_minutes": 300},
        {"monthly_fee_usd": "999", "outage_minutes": 300, "service_type": "satellite"},
    ],
)
async def test_an_amount_or_a_duration_out_of_bounds_never_reaches_the_engine(
    client: Connected, bad: dict[str, Any]
) -> None:
    arguments = {"service_type": "broadband", **bad}

    result = await client.call_tool("quote_service_credit", arguments)

    assert result.is_error


# --- how the platform is doing --------------------------------------------------------------------


async def test_the_state_of_the_platform_is_told_without_anything_of_a_customer(
    client: Connected,
) -> None:
    status = await asked(client, "platform_status")

    assert status["guide_index"]["documents"] == 12
    assert status["guide_index"]["embedder"] == "hashing-v1"
    assert status["feed"]["caught_up"] is True
    assert status["feed"]["intact"] is True
    assert status["backlog"]["contacts_waiting"] == 0
    assert status["open_outages"] == 0


# --- documents a client may read -----------------------------------------------------------------


async def test_the_credit_policy_and_the_router_models_can_be_read(client: Connected) -> None:
    listed = {str(resource.uri) for resource in (await client.list_resources()).resources}

    assert listed == {"skylink://credit-policy", "skylink://router-models"}
    policy = (await client.read_resource("skylink://credit-policy")).contents[0]
    models = (await client.read_resource("skylink://router-models")).contents[0]
    assert "version: v0-placeholder" in policy.text  # type: ignore[union-attr]
    assert "currency: USD" in policy.text  # type: ignore[union-attr]
    assert '"AL-AX18"' in models.text  # type: ignore[union-attr]
    assert models.text.count('"device_model"') == 12  # type: ignore[union-attr]


async def test_the_policy_a_client_reads_is_the_one_the_engine_was_started_with(
    db: Database,
    gateway: OssGateway,
    care_settings: Settings,
    stack_clock: ManualClock,
    tmp_path: Path,
) -> None:
    """The engine reads the policy once, when the process starts. A file changed since then
    is not what it prices by, so it is not what a client is shown."""
    policy_file = tmp_path / "credit_policy.yaml"
    policy_file.write_text(care_settings.credit_policy_path.read_text(encoding="utf-8"))
    settings = care_settings.model_copy(update={"credit_policy_path": policy_file})
    started = build_tools(settings, db, gateway, now=lambda: stack_clock.now().astimezone(UTC))
    policy_file.write_text("version: changed-after-the-start\n", encoding="utf-8")

    read = await started.credit_policy_text()

    assert "version: v0-placeholder" in read
    assert "changed-after-the-start" not in read


# --- what is kept of a call, and how many may be made -------------------------------------------


async def test_every_call_is_in_the_audit_trail_without_the_words_that_were_asked(
    client: Connected, db: Database
) -> None:
    before = await trail_so_far(db)
    await asked(
        client,
        "search_router_guide",
        router_model="AL-AX18",
        problem="this is Asha Menon, 98450 12345, my wifi keeps dropping",
    )
    await refused(client, "check_network_status", node_id="OLT-NOWHERE-999")

    rows = await calls_since(db, before)
    assert [(row["actor"], row["step"], row["outcome"]) for row in rows] == [
        ("mcp:client", "mcp.tool:search_router_guide", "OK"),
        ("mcp:client", "mcp.tool:check_network_status", "REFUSED"),
    ]
    kept = str(rows[0]["inputs"]) + str(rows[0]["outputs"])
    assert "Asha" not in kept
    assert "98450" not in kept
    assert rows[0]["inputs"]["problem_chars"] > 0
    assert rows[0]["inputs"]["router_model"] == "AL-AX18"


async def test_calls_beyond_the_limit_are_refused_and_nothing_is_asked_for_them(
    db: Database,
    datapack: Path,
    gateway: OssGateway,
    care_settings: Settings,
    stack_clock: ManualClock,
) -> None:
    await load_reference(db, datapack)
    limited = build_tools(
        care_settings,
        db,
        gateway,
        now=lambda: stack_clock.now().astimezone(UTC),
        limiter=RateLimiter(capacity=2, refill_per_second=0.0001),
    )
    client = Connected(build_server(limited))
    before = await trail_so_far(db)
    await asked(client, "platform_status")
    await asked(client, "platform_status")
    said = await refused(client, "platform_status")

    assert "too many calls" in said
    outcomes = [row["outcome"] for row in await calls_since(db, before)]
    assert outcomes == ["OK", "OK", "RATE_LIMITED"]


async def test_a_client_that_keeps_asking_past_the_limit_cannot_fill_the_trail(
    db: Database,
    datapack: Path,
    gateway: OssGateway,
    care_settings: Settings,
    stack_clock: ManualClock,
) -> None:
    """Each call that is served is on record. Of the calls refused for the limit, the first of
    a minute is: a refusal must not cost a write every time it is given."""
    await load_reference(db, datapack)
    limited = build_tools(
        care_settings,
        db,
        gateway,
        now=lambda: stack_clock.now().astimezone(UTC),
        limiter=RateLimiter(capacity=1, refill_per_second=0.0001),
    )
    client = Connected(build_server(limited))
    before = await trail_so_far(db)
    await asked(client, "platform_status")
    for _ in range(5):
        assert "too many calls" in await refused(client, "platform_status")

    outcomes = [row["outcome"] for row in await calls_since(db, before)]
    assert outcomes == ["OK", "RATE_LIMITED"]


async def test_reading_a_document_is_counted_and_recorded_like_a_call(
    db: Database,
    datapack: Path,
    gateway: OssGateway,
    care_settings: Settings,
    stack_clock: ManualClock,
) -> None:
    await load_reference(db, datapack)
    limited = build_tools(
        care_settings,
        db,
        gateway,
        now=lambda: stack_clock.now().astimezone(UTC),
        limiter=RateLimiter(capacity=2, refill_per_second=0.0001),
    )
    client = Connected(build_server(limited))
    before = await trail_so_far(db)
    await client.read_resource("skylink://credit-policy")
    await client.read_resource("skylink://router-models")
    said = await client.refused_to_read("skylink://router-models")

    assert "too many calls" in said
    rows = await calls_since(db, before)
    assert [(row["actor"], row["step"], row["outcome"]) for row in rows] == [
        ("mcp:client", "mcp.resource:credit-policy", "OK"),
        ("mcp:client", "mcp.resource:router-models", "OK"),
        ("mcp:client", "mcp.resource:router-models", "RATE_LIMITED"),
    ]


async def test_a_refusal_is_recorded_under_its_reason(client: Connected, db: Database) -> None:
    before = await trail_so_far(db)
    await refused(
        client, "search_router_guide", router_model="ZZ-9000", problem="wifi keeps dropping"
    )

    rows = await calls_since(db, before)
    assert [(row["step"], row["outcome"]) for row in rows] == [
        ("mcp.tool:search_router_guide", "NO_GUIDE_FOR_MODEL")
    ]


async def test_a_fault_of_the_platforms_own_is_recorded_and_tells_the_client_nothing(
    client: Connected, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(self: ReferenceRepository, node_id: str) -> None:
        raise RuntimeError("password=hunter2 at db-internal.example")

    monkeypatch.setattr(ReferenceRepository, "get_node", broken)
    before = await trail_so_far(db)
    said = await refused(client, "check_network_status", node_id="OLT-0001")

    assert "hunter2" not in said
    assert "db-internal" not in said
    rows = await calls_since(db, before)
    assert [(row["step"], row["outcome"], row["outputs"]) for row in rows] == [
        ("mcp.tool:check_network_status", "ERROR", {"error": "RuntimeError"})
    ]


async def test_a_call_that_is_cancelled_is_still_on_record(
    tools: CareTools, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A client that goes away in the middle of a call has still made it."""
    reading = asyncio.Event()

    async def never_answers(self: ReferenceRepository, node_id: str) -> None:
        reading.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ReferenceRepository, "get_node", never_answers)
    before = await trail_so_far(db)
    call = asyncio.create_task(tools.check_network_status("OLT-0001"))
    await reading.wait()
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call

    rows = await calls_since(db, before)
    assert [(row["step"], row["outcome"]) for row in rows] == [
        ("mcp.tool:check_network_status", "CANCELLED")
    ]


async def test_without_a_key_for_network_monitoring_the_other_tools_still_answer(
    db: Database,
    datapack: Path,
    oss_transport: httpx.ASGITransport,
    care_settings: Settings,
    stack_clock: ManualClock,
    simulator: Simulator,
) -> None:
    await load_reference(db, datapack)
    keyless = OssGateway(MOCK_URL, OssKeys(mcp=None), transport=oss_transport, policy=TEST_POLICY)
    try:
        client = Connected(
            build_server(
                build_tools(
                    care_settings, db, keyless, now=lambda: stack_clock.now().astimezone(UTC)
                )
            )
        )
        before = await trail_so_far(db)
        said = await refused(client, "check_network_status", node_id=await a_node(simulator))
        quote = await asked(
            client,
            "quote_service_credit",
            service_type="broadband",
            monthly_fee_usd="999.00",
            outage_minutes=300,
        )
    finally:
        await keyless.aclose()

    assert "OSS_KEY_MCP is not set" in said
    assert quote["amount_usd"] == "50.00"
    outcomes = [row["outcome"] for row in await calls_since(db, before)]
    assert outcomes == ["NOT_CONFIGURED", "OK"]
