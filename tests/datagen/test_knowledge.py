"""Knowledge pack: router models, guides and the quarantined hostile document."""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

SECTION_IDS = ["overview", "leds", "power-cycle", "no-internet", "wifi", "slow-speed",
               "factory-reset", "escalation"]  # fmt: skip
FACT_KEYS = {"admin_url", "default_ssid_pattern", "power_led", "internet_led_name",
             "internet_led_ok", "internet_led_fault", "los_led_name", "reset_hold_seconds",
             "reboot_wait_seconds", "wps_button", "firmware"}  # fmt: skip
REAL_BRANDS = (
    "tp-link",
    "tplink",
    "netgear",
    "huawei",
    "nokia",
    "zte",
    "d-link",
    "asus",
    "linksys",
    "cisco",
    "tenda",
    "jio",
    "airtel",
    "xiaomi",
    "mercusys",
    "syrotech",
    "digisol",
    "alcatel",
    "sagemcom",
    "technicolor",
    "fiberhome",
    "ubiquiti",
)
REPO_KNOWLEDGE = Path(__file__).resolve().parents[2] / "data" / "knowledge"
STEP_SECTIONS = SECTION_IDS[2:]


def _models(root: Path) -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = json.loads((root / "router_models.json").read_text("utf-8"))
    return loaded


def _split(text: str) -> tuple[dict[str, Any], str]:
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "missing front matter"
    return yaml.safe_load(match.group(1)), text[match.end() :]


def _sections(body: str) -> dict[str, str]:
    parts = re.split(r"^## .+ \{#([a-z-]+)\}$", body, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def _guide(root: Path, model_id: str) -> tuple[dict[str, Any], str]:
    return _split((root / "router_guides" / f"{model_id}.md").read_text("utf-8"))


def test_router_models_structure(knowledge_dir: Path) -> None:
    models = _models(knowledge_dir)
    assert len(models) == 12
    assert len({m["model_id"] for m in models}) == 12
    assert len({m["vendor"] for m in models}) >= 5
    assert {m["type"] for m in models} == {"ont_router", "router"}
    for model in models:
        assert set(model) == {"model_id", "vendor", "name", "type", "facts"}
        facts = model["facts"]
        assert set(facts) == FACT_KEYS
        assert (facts["los_led_name"] is None) == (model["type"] == "router")
        assert isinstance(facts["reset_hold_seconds"], int)
        assert isinstance(facts["reboot_wait_seconds"], int)
        assert isinstance(facts["wps_button"], bool)
        blob = json.dumps(model).lower()
        assert not any(brand in blob for brand in REAL_BRANDS)


def test_any_two_models_differ_on_five_facts(knowledge_dir: Path) -> None:
    for a, b in itertools.combinations(_models(knowledge_dir), 2):
        diff = sum(a["facts"][k] != b["facts"][k] for k in FACT_KEYS)
        assert diff >= 5, (a["model_id"], b["model_id"])


def test_one_guide_per_model_with_front_matter(knowledge_dir: Path) -> None:
    models = _models(knowledge_dir)
    files = sorted(p.stem for p in (knowledge_dir / "router_guides").glob("*.md"))
    assert files == sorted(m["model_id"] for m in models)
    doc_ids = set()
    for model in models:
        meta, _ = _guide(knowledge_dir, model["model_id"])
        assert meta["doc_type"] == "device_guide"
        assert meta["device_model"] == model["model_id"]
        assert meta["vendor"] == model["vendor"]
        assert meta["doc_id"] == f"guide-{model['model_id'].lower()}"
        assert model["name"] in meta["title"]
        assert isinstance(meta["version"], str)
        assert meta["access_roles"] == ["customer", "agent", "supervisor"]
        assert meta["effective_from"]
        doc_ids.add(meta["doc_id"])
    assert len(doc_ids) == 12


def test_guides_have_sections_in_order_with_numbered_steps(knowledge_dir: Path) -> None:
    for model in _models(knowledge_dir):
        _, body = _guide(knowledge_dir, model["model_id"])
        sections = _sections(body)
        assert list(sections) == SECTION_IDS
        for section_id in STEP_SECTIONS:
            numbers = re.findall(r"^(\d+)\. \S", sections[section_id], flags=re.MULTILINE)
            assert numbers, (model["model_id"], section_id)
            assert numbers[0] == "1"


def _expected_statements(model: dict[str, Any]) -> dict[str, list[str]]:
    f = model["facts"]
    los = f["los_led_name"]
    leds = [f["power_led"], f["internet_led_name"], f["internet_led_ok"], f["internet_led_fault"]]
    leds.append(f"{los} light" if los else "no LOS light")
    wps = "WPS button" if f["wps_button"] else "no WPS button"
    return {
        "overview": [f["firmware"], f["admin_url"], model["name"]],
        "leds": leds,
        "power-cycle": [f"{f['reboot_wait_seconds']} seconds"],
        "no-internet": [f["internet_led_fault"], *([los] if los else [])],
        "wifi": [f["default_ssid_pattern"], wps],
        "slow-speed": [f["admin_url"]],
        "factory-reset": [f"{f['reset_hold_seconds']} seconds"],
    }


def test_each_guide_states_its_facts_in_matching_section(knowledge_dir: Path) -> None:
    for model in _models(knowledge_dir):
        _, body = _guide(knowledge_dir, model["model_id"])
        sections = _sections(body)
        for section_id, statements in _expected_statements(model).items():
            for statement in statements:
                assert statement in sections[section_id], (model["model_id"], section_id, statement)


def test_guides_do_not_leak_other_models_identifying_facts(knowledge_dir: Path) -> None:
    models = _models(knowledge_dir)
    for model in models:
        _, body = _guide(knowledge_dir, model["model_id"])
        for other in models:
            if other["model_id"] != model["model_id"]:
                assert other["facts"]["firmware"] not in body
                assert other["facts"]["default_ssid_pattern"] not in body


def test_guides_carry_safety_and_escalation_content(knowledge_dir: Path) -> None:
    for model in _models(knowledge_dir):
        _, body = _guide(knowledge_dir, model["model_id"])
        sections = _sections(body)
        lower = body.lower()
        assert "casing" in lower
        assert "look into" in lower
        assert "erase" in sections["factory-reset"].lower()
        assert "technician" in sections["escalation"].lower()
        assert "stop" in sections["no-internet"].lower()
        assert len(body.split()) >= 450


def test_hostile_document(knowledge_dir: Path) -> None:
    text = (knowledge_dir / "quarantine" / "untrusted_forum_post.md").read_text("utf-8")
    meta, body = _split(text)
    assert meta["doc_type"] == "untrusted"
    assert meta["doc_id"] == "untrusted-forum-post-001"
    lower = body.lower()
    for marker in ("ignore all previous instructions", "system prompt", "credit", "apply_credit"):
        assert marker in lower, marker
    assert "look into the fibre" in lower


def test_knowledge_command_writes_nothing_else(knowledge_dir: Path) -> None:
    top = sorted(p.name for p in knowledge_dir.iterdir())
    assert top == ["quarantine", "router_guides", "router_models.json"]
    assert [p.name for p in (knowledge_dir / "quarantine").iterdir()] == ["untrusted_forum_post.md"]


@pytest.mark.skipif(not (REPO_KNOWLEDGE / "router_models.json").exists(), reason="not generated")
def test_committed_knowledge_matches_generator(knowledge_dir: Path) -> None:
    for path in sorted(p for p in knowledge_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(knowledge_dir)
        assert (REPO_KNOWLEDGE / rel).read_bytes() == path.read_bytes(), rel
