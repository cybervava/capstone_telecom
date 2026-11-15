"""Guides as the index reads them, and the embedder that needs no model."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from skylink_care.rag.documents import MAX_GUIDE_BYTES, GuideError, load_guides, parse_guide
from skylink_care.rag.embedding import DIMENSIONS, HashingEmbedder, to_literal

KNOWLEDGE = Path(__file__).resolve().parents[3] / "data" / "knowledge"
GUIDE = """---
doc_id: guide-test-1
doc_type: device_guide
title: Test router guide
device_model: TR-1
version: "2.0"
access_roles: [customer, agent]
---

# Test router guide

Intro text that belongs to no section.

## Power cycle {#power-cycle}

Try this first.

1. Switch the router off.
2. Wait 30 seconds.
3. Switch it on again.

## Lights {#leds}

The power light is green when all is well.
"""


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def test_guide_is_split_into_cited_sections_with_their_steps() -> None:
    guide = parse_guide(GUIDE)

    assert (guide.doc_id, guide.device_model, guide.version) == ("guide-test-1", "TR-1", "2.0")
    assert guide.access_roles == ("customer", "agent")
    assert [section.section_id for section in guide.sections] == ["power-cycle", "leds"]
    power_cycle, lights = guide.sections
    assert power_cycle.steps == (
        "Switch the router off.",
        "Wait 30 seconds.",
        "Switch it on again.",
    )
    assert lights.steps == ()
    assert "Intro text" not in power_cycle.body


def test_content_hash_changes_with_the_text_and_with_nothing_else() -> None:
    original = parse_guide(GUIDE)

    assert parse_guide(GUIDE).content_hash == original.content_hash
    assert parse_guide(GUIDE.replace("30 seconds", "60 seconds")).content_hash != (
        original.content_hash
    )


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("# just a heading\n", "no front matter"),
        (GUIDE.replace("doc_type: device_guide", "doc_type: untrusted"), "is not indexed"),
        (GUIDE.replace("device_model: TR-1\n", ""), "lacks device_model"),
        (GUIDE.replace("{#power-cycle}", "").replace("{#leds}", ""), "no sections"),
        ("---\n- a\n- list\n---\nbody\n", "not a mapping"),
        ("---\n: : :\n---\nbody\n", "not valid YAML"),
    ],
)
def test_document_that_is_not_a_usable_device_guide_is_refused(text: str, why: str) -> None:
    with pytest.raises(GuideError, match=why):
        parse_guide(text, source="bad.md")


def test_every_guide_in_the_knowledge_pack_parses_with_the_same_sections() -> None:
    guides = load_guides(KNOWLEDGE / "router_guides")

    assert len(guides) == 12
    assert len({guide.device_model for guide in guides}) == 12
    for guide in guides:
        ids = [section.section_id for section in guide.sections]
        assert {"power-cycle", "no-internet", "wifi", "slow-speed", "factory-reset"} <= set(ids)
        assert all(
            section.steps for section in guide.sections if section.section_id == "power-cycle"
        )


def test_quarantined_material_is_refused_by_the_parser_itself() -> None:
    """Even if someone copied it into the guides folder."""
    hostile = (KNOWLEDGE / "quarantine" / "untrusted_forum_post.md").read_text(encoding="utf-8")

    with pytest.raises(GuideError):
        parse_guide(hostile, source="untrusted_forum_post.md")


def test_embedding_is_deterministic_normalised_and_of_fixed_size() -> None:
    embedder = HashingEmbedder()

    first, again = embedder.embed(["the wifi keeps dropping", "the wifi keeps dropping"])

    assert first == again
    assert len(first) == DIMENSIONS
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0, rel_tol=1e-9)
    assert embedder.embed([""]) == [[0.0] * DIMENSIONS]


def test_texts_about_the_same_thing_are_closer_than_texts_about_different_things() -> None:
    embedder = HashingEmbedder()
    wifi, wifi_again, speed = embedder.embed(
        [
            "devices cannot connect to the wifi network password",
            "my phone will not connect to wifi, password not accepted",
            "run a speed test on a laptop connected by ethernet cable",
        ]
    )

    assert cosine(wifi, wifi_again) > cosine(wifi, speed)


def test_vector_literal_is_what_postgres_reads() -> None:
    assert to_literal([0.5, -0.25, 0.0]) == "[0.500000,-0.250000,0.000000]"


HAZARDOUS_STEPS = [
    "Press and hold the reset button with a paperclip for 10 seconds.",
    "Do a factory reset from the admin page.",
    "Unplug the green fibre cable and look into the connector.",
    "Open the casing and reseat the board.",
    "If that fails, disconnect the fibre and plug it in again.",
]
SAFE_STEPS = [
    "Leave it unplugged for a short while. Do not unplug the fibre cable.",
    "Check that the fibre cable is not bent sharply or crushed. Do not disconnect it.",
    "Stop here: do not reset the router, and report the fault to SkyLink.",
    "Stop and contact SkyLink support rather than trying a factory reset yourself.",
    "Never look into the fibre connector.",
]


@pytest.mark.parametrize("step", HAZARDOUS_STEPS)
def test_guide_whose_quotable_steps_are_hazardous_is_refused(step: str) -> None:
    """The steps go to customers verbatim, so the guide is the control."""
    edited = GUIDE.replace("2. Wait 30 seconds.", f"2. {step}")

    with pytest.raises(GuideError, match="unsafe"):
        parse_guide(edited, source="edited.md")


@pytest.mark.parametrize("step", SAFE_STEPS)
def test_step_that_only_forbids_the_hazard_is_accepted(step: str) -> None:
    guide = parse_guide(GUIDE.replace("2. Wait 30 seconds.", f"2. {step}"))

    assert step in guide.sections[0].steps


def test_hazard_in_a_section_that_is_never_quoted_does_not_block_the_guide() -> None:
    with_reset = GUIDE + "\n## Factory reset {#factory-reset}\n\n1. Hold the reset button.\n"

    guide = parse_guide(with_reset)

    assert guide.sections[-1].section_id == "factory-reset"


def test_indented_lines_under_a_step_stay_part_of_it() -> None:
    wrapped = GUIDE.replace(
        "2. Wait 30 seconds.", "2. Wait 30 seconds.\n   Do not unplug the fibre cable."
    )

    guide = parse_guide(wrapped)

    assert guide.sections[0].steps[1] == "Wait 30 seconds. Do not unplug the fibre cable."
    assert len(guide.sections[0].steps) == 3


@pytest.mark.parametrize(
    ("old", "new", "field"),
    [
        ("doc_id: guide-test-1", 'doc_id: "guide 1) see http://example.test"', "doc_id"),
        ('version: "2.0"', 'version: "2.0 beta!"', "version"),
        ("device_model: TR-1", "device_model: tr 1", "device_model"),
    ],
)
def test_identifiers_shown_in_the_citation_must_have_their_fixed_shape(
    old: str, new: str, field: str
) -> None:
    with pytest.raises(GuideError, match=field):
        parse_guide(GUIDE.replace(old, new), source="odd.md")


def test_two_guides_for_one_model_and_oversized_files_are_refused(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(GUIDE, encoding="utf-8")
    (tmp_path / "b.md").write_text(
        GUIDE.replace("guide-test-1", "guide-test-old"), encoding="utf-8"
    )
    with pytest.raises(GuideError, match="more than one guide for model TR-1"):
        load_guides(tmp_path)

    (tmp_path / "b.md").write_text(GUIDE + "x" * MAX_GUIDE_BYTES, encoding="utf-8")
    with pytest.raises(GuideError, match="larger than"):
        load_guides(tmp_path)
