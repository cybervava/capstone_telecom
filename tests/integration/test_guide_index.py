"""The guide index against real PostgreSQL with pgvector: filters first, ranking second."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

import pytest

from skylink_care.agents.troubleshooting import TroubleshootingAgent
from skylink_care.db.audit import AuditRepository
from skylink_care.db.database import Database
from skylink_care.observability.audit import Auditor
from skylink_care.pipelines.guide_index import KnowledgePackError, build_guide_index
from skylink_care.rag.documents import SELF_SERVICE_SECTIONS, GuideError, load_guides
from skylink_care.rag.embedding import DIMENSIONS, HashingEmbedder
from skylink_care.rag.index import GuideIndex, GuideIndexUnavailableError
from tests.support.postgres import KNOWLEDGE_DIR

SELF_SERVICE = ("no-internet", "wifi", "slow-speed", "power-cycle")


class OtherEmbedder:
    """Stands for any embedder other than the one the index was built with."""

    name = "other-v9"
    dimensions = DIMENSIONS

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return HashingEmbedder().embed(texts)


def index(db: Database) -> GuideIndex:
    return GuideIndex(db, HashingEmbedder())


async def test_index_holds_every_section_of_every_guide(db: Database) -> None:
    info = await index(db).info()

    assert info is not None
    assert (info.documents, info.chunks, info.embedder) == (12, 96, "hashing-v1")


ALL_MODELS = sorted(path.stem for path in (KNOWLEDGE_DIR / "router_guides").glob("*.md"))
# Customers do not write the way guides do. These are their words, not the guides'.
PROBLEMS = [
    ("my phone cannot connect to the wifi, wrong password", "wifi"),
    ("the wifi keeps dropping on my phone", "wifi"),
    ("wi-fi not showing on my laptop", "wifi"),
    ("WiFi disconnects every few minutes", "wifi"),
    ("the internet is very slow in the evening, speed test is bad", "slow-speed"),
    ("videos keep buffering, everything is so slow", "slow-speed"),
    ("no internet at all, the internet light is red", "no-internet"),
    ("internet not working since morning", "no-internet"),
    ("how do I restart the router", "power-cycle"),
    ("should I reboot it? how long do I wait", "power-cycle"),
]


@pytest.mark.parametrize("model", ALL_MODELS)
@pytest.mark.parametrize(("problem", "section"), PROBLEMS)
async def test_customers_words_find_the_matching_section_of_their_own_guide(
    db: Database, model: str, problem: str, section: str
) -> None:
    (best,) = await index(db).search(
        problem, device_model=model, role="customer", sections=SELF_SERVICE, limit=1
    )

    assert best.section_id == section
    assert best.doc_id == f"guide-{model.lower()}"
    assert best.steps
    assert best.citation == f"{best.doc_id} §{section} v{best.doc_version}"


async def test_router_model_decides_the_guide_whatever_the_customer_writes(db: Database) -> None:
    """Naming another router in the message must not bring that router's guide."""
    problem = "I have a Kavya HX6 KV-HX6 and the wifi does not connect"

    passages = await index(db).search(
        problem, device_model="AL-AX18", role="customer", sections=SELF_SERVICE, limit=5
    )
    other = await index(db).search(
        problem, device_model="KV-HX6", role="customer", sections=SELF_SERVICE, limit=1
    )

    assert passages
    assert {passage.device_model for passage in passages} == {"AL-AX18"}
    assert other[0].doc_id == "guide-kv-hx6"
    assert other[0].steps != passages[0].steps  # the models really differ in what they say


async def test_sections_outside_the_allowed_list_are_never_returned(db: Database) -> None:
    passages = await index(db).search(
        "factory reset the router, hold the reset button with a paperclip",
        device_model="AL-AX18",
        role="customer",
        sections=SELF_SERVICE,
        limit=8,
    )

    assert passages
    assert "factory-reset" not in {passage.section_id for passage in passages}


async def test_role_without_access_and_unknown_model_find_nothing(db: Database) -> None:
    guides = index(db)

    assert (
        await guides.search("wifi", device_model="AL-AX18", role="auditor", sections=SELF_SERVICE)
        == []
    )
    assert (
        await guides.search(
            "wifi", device_model="NO-SUCH-1", role="customer", sections=SELF_SERVICE
        )
        == []
    )


async def test_text_with_nothing_to_search_for_finds_no_section(db: Database) -> None:
    """There is no best section for an unreadable problem."""
    for unreadable in ("?? !!", "", "   ", "the and of to"):
        assert (
            await index(db).search(
                unreadable, device_model="AL-AX18", role="customer", sections=SELF_SERVICE
            )
            == []
        )


async def test_index_built_with_one_embedder_refuses_queries_from_another(db: Database) -> None:
    with pytest.raises(GuideIndexUnavailableError, match="built with hashing-v1"):
        await GuideIndex(db, OtherEmbedder()).search(
            "wifi", device_model="AL-AX18", role="customer", sections=SELF_SERVICE
        )


async def test_rebuild_replaces_the_index_and_a_bad_pack_leaves_it_alone(
    db: Database, tmp_path: Path
) -> None:
    guides = index(db)
    before = await guides.info()
    assert before is not None
    try:
        one = load_guides(KNOWLEDGE_DIR / "router_guides")[:1]
        smaller = await guides.rebuild(one)
        assert (smaller.documents, smaller.chunks) == (1, 8)
        assert smaller.index_version != before.index_version
        assert (
            await guides.search(
                "wifi", device_model="KV-HX6", role="customer", sections=SELF_SERVICE
            )
            == []
        )

        broken = tmp_path / "router_guides"
        broken.mkdir()
        (broken / "bad.md").write_text("no front matter here\n", encoding="utf-8")
        with pytest.raises(GuideError):
            await build_guide_index(db, tmp_path)
        with pytest.raises(KnowledgePackError):
            await build_guide_index(db, tmp_path / "nowhere")
        unchanged = await guides.info()
        assert unchanged is not None
        assert unchanged.index_version == smaller.index_version
    finally:
        restored = await build_guide_index(db, KNOWLEDGE_DIR)  # the index is shared by the session
    assert restored.index_version == before.index_version  # same content, same version


async def test_empty_index_is_reported_as_unavailable_not_as_no_result(db: Database) -> None:
    guides = index(db)
    try:
        await guides.rebuild([])
        with pytest.raises(GuideIndexUnavailableError, match="has not been built"):
            await guides.search(
                "wifi", device_model="AL-AX18", role="customer", sections=SELF_SERVICE
            )
    finally:
        await build_guide_index(db, KNOWLEDGE_DIR)


async def test_agent_gives_no_guidance_without_a_router_on_record_or_a_guide_for_it(
    db: Database,
) -> None:
    agent = TroubleshootingAgent(index(db))
    audit = Auditor(AuditRepository(db), contact_id=None, correlation_id=uuid4().hex)

    no_router = await agent.guide(router_model=None, problem="wifi is down", audit=audit)
    unknown = await agent.guide(router_model="NO-SUCH-1", problem="wifi is down", audit=audit)
    known = await agent.guide(router_model="AL-AX18", problem="wifi is down", audit=audit)

    assert (no_router, unknown) == (None, None)
    assert known is not None
    assert known.passage.doc_id == "guide-al-ax18"
    async with db.connection() as conn:
        cursor = await conn.execute(
            "SELECT outcome FROM audit_log WHERE correlation_id = %s ORDER BY audit_id",
            (audit.correlation_id,),
        )
        outcomes = [row["outcome"] for row in await cursor.fetchall()]
    assert outcomes == ["NO_ROUTER_MODEL", "NO_GUIDE_FOR_MODEL", "OK"]


async def test_section_the_caller_expects_wins_over_shared_words_and_only_within_the_guide(
    db: Database,
) -> None:
    """The kind of fault read from the customer's words is told to the search as a preference.
    It chooses between the sections of the customer's own router's guide and nothing else."""
    guides = GuideIndex(db, HashingEmbedder())
    asked = {"device_model": "TG-FG7", "role": "customer", "sections": SELF_SERVICE_SECTIONS}

    by_words = await guides.search("my broadband is down", limit=1, **asked)
    preferred = await guides.search(
        "my broadband is down", limit=1, prefer="no-internet", preference=3.0, **asked
    )
    not_on_offer = await guides.search(
        "my broadband is down", limit=4, prefer="factory-reset", preference=9.0, **asked
    )

    assert preferred[0].section_id == "no-internet"
    assert preferred[0].device_model == by_words[0].device_model == "TG-FG7"
    assert "factory-reset" not in {passage.section_id for passage in not_on_offer}


async def test_fault_the_customer_rules_out_does_not_find_its_section(db: Database) -> None:
    """ "Not slow" names the section on slow speed by its title, and the customer said the
    opposite. What they rule out is kept from the search as it is from the rules."""
    agent = TroubleshootingAgent(index(db))
    audit = Auditor(AuditRepository(db), contact_id=None, correlation_id=uuid4().hex)

    answer = await agent.guide(
        router_model="AL-AX18", problem="pages time out all the time, it is not slow", audit=audit
    )

    assert answer is not None
    assert answer.passage.section_id != "slow-speed"
