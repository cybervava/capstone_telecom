"""No raw identifier of the customer reaches a language model, whatever they write.

Three hundred synthetic customers each write their own details into a message, in the
spellings people use: a number broken up with brackets and dashes, the last digits of it, a
name with an ending, a pincode with a space. The whole request each model provider would send
(Claude's and OpenAI's: the system prompt and the message) is then searched for exactly what
was written in, and for the digits of every number however they were set apart.

The check knows what it put in. It does not ask the masking's own rules what counts as a
leak, so a spelling the masking misses is a failure here and not a blind spot.

What cannot be caught is pinned in ``tests/unit/guardrails/test_pii.py``: digits as words, a
name in another script, a street the platform was never told.
"""

from __future__ import annotations

import json
import random
import re
from types import SimpleNamespace
from typing import Any

import pytest

from skylink_care.adapters.models import CustomerProfile
from skylink_care.agents.triage import personal_values
from skylink_care.llm.base import GuideSection, TriageProvider
from skylink_care.llm.claude import ClaudeProvider
from skylink_care.llm.gateway import LLMGateway
from skylink_care.llm.openai_provider import OpenAIProvider
from skylink_datagen.reference import customer_id, phone_for, service_id
from skylink_datagen.wordlists import AREAS, CITIES, FIRST_NAMES, LAST_NAMES, PIN_PREFIX

CUSTOMERS = 300
SEED = 20261116
ANSWER = {
    "intent": "connectivity",
    "confidence": "high",
    "wants_human": False,
    "insists_device_fault": False,
    "requests_technician": False,
    "requests_credit": False,
    "confirms_working": False,
    "abusive_or_off_topic": False,
    "summary": "Customer reports no internet.",
}
# What a model answers when it is asked which guide section fits (``answer_from_guide``).
GUIDE_ANSWER = {"section_id": "wifi", "customer_words": "", "guide_words": ""}
GUIDE_SECTIONS = (
    GuideSection(
        "no-internet", "No internet", "Work out whether the line or the router.\n\n1. Look."
    ),
    GuideSection(
        "wifi", "Wi-Fi problems", "If devices cannot connect over Wi-Fi:\n\n1. Move closer."
    ),
)
# Each is formatted with spellings of the customer's own details (``spellings`` below).
MESSAGES = (
    "Hi, this is {first} here, my internet has been down since morning",
    "{full} speaking. Wifi not working at {area}, {city} {pincode}",
    "{FIRST} {LAST} FROM {AREA}!! NET IS DOWN AGAIN",
    "my id is {customer_id} and my number is {phone_spaced}, no internet",
    "call me on {phone_plain} or mail {email}. service {service_id} is dead",
    "{customer_id} {last4}",
    "registered mobile ends in {last4}. {first}'s router shows a red light",
    "{last}, {first} ({pincode}) - broadband down, account {customer_id_lower}",
    "+91-{phone_dashed} is my line, {first}  {last}, {area}",
    "{first_ji} here. number is {phone_bracketed}, ending {last5}",
    "reach me at {phone_slashed} / {phone_wide}. {first_last_joined} is the name",
    "account {customer_id_spaced}, pin {pincode_spaced}, {area_hyphened}",
    "mail is {mail_name} at example dot com, last six {last6}",
    "{first_long} from {area_first_word} side, mobile XXXXXX{last4}",
    "{mail_name_spaced} / {customer_id_hyphened} / {last4_spaced}",
)


PROVIDERS = ("anthropic", "openai")


class Requests:
    """Stands in for a model's client: keeps every request it is asked to send, and answers
    in the shape of that provider."""

    def __init__(self, provider: str = "anthropic") -> None:
        self.provider = provider
        self.sent: list[dict[str, Any]] = []

    async def create(self, **request: Any) -> Any:
        self.sent.append(request)
        # Each question is answered in the shape it asks for.
        about_a_guide = "section_id" in json.dumps(request, default=str)
        answer = json.dumps(GUIDE_ANSWER if about_a_guide else ANSWER)
        if self.provider == "openai":
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=answer, refusal=None),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
                model="gpt-5.4-mini",
            )
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=answer)],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            model="claude-opus-5-5",
        )


def gateway_with(requests: Requests) -> LLMGateway:
    provider: TriageProvider
    if requests.provider == "openai":
        client = SimpleNamespace(chat=SimpleNamespace(completions=requests))
        provider = OpenAIProvider(client, model="gpt-5.4-mini")  # type: ignore[arg-type]
    else:
        client = SimpleNamespace(messages=requests, beta=SimpleNamespace(messages=requests))
        provider = ClaudeProvider(client, model="claude-opus-5-5")  # type: ignore[arg-type]
    return LLMGateway(provider)


def profile(index: int, rng: random.Random, **changed: Any) -> CustomerProfile:
    city = rng.choice(CITIES)
    address = {
        "address_id": f"A{index + 1:07d}",
        "area": rng.choice(AREAS[city]),
        "city": city,
        "pincode": f"{PIN_PREFIX[city]}{rng.randrange(1000):03d}",
    }
    first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
    record = {
        "customer_id": customer_id(index),
        "full_name": f"{first} {last}",
        "phone": phone_for(index),
        "email": f"{first.lower()}.{last.lower()}{index}@example.com",
        "segment": "consumer",
        "status": "active",
        "consent": {"outage_notifications": True, "channel": "sms"},
        "services": [
            {
                "service_id": service_id(index),
                "service_type": "broadband",
                "plan_name": "Fibre 100",
                "monthly_fee_usd": "599.00",
                "status": "active",
                "address": address,
                "router_model": "AL-AX18",
                "billing_cycle_day": 5,
            }
        ],
    }
    return CustomerProfile.model_validate({**record, **changed})


def spellings(person: CustomerProfile) -> dict[str, str]:
    """The customer's details as they might write them. Every value is theirs alone."""
    names = re.split(r"[\W_]+", person.full_name)
    first, last = names[0], names[-1]
    address = person.services[0].address
    digits = person.phone[-10:]
    mail_name = person.email.partition("@")[0]
    number = person.customer_id[1:]
    return {
        "first": first,
        "last": last,
        "full": person.full_name,
        "FIRST": first.upper(),
        "LAST": last.upper(),
        "first_ji": f"{first}ji",
        "first_long": f"{first}{first[-1] * 2}",
        "first_last_joined": f"{first}_{last}",
        "area": address.area,
        "AREA": address.area.upper(),
        "area_hyphened": address.area.replace(" ", "-"),
        "area_first_word": address.area.split()[0],
        "city": address.city,
        "pincode": address.pincode,
        "pincode_spaced": f"{address.pincode[:3]} {address.pincode[3:]}",
        "customer_id": person.customer_id,
        "customer_id_lower": person.customer_id.lower(),
        "customer_id_spaced": f"C {number}",
        "customer_id_hyphened": f"c-{number}",
        "service_id": person.services[0].service_id,
        "email": person.email,
        "mail_name": mail_name,
        "mail_name_spaced": mail_name.replace(".", " "),
        "phone_plain": digits,
        "phone_spaced": f"{digits[:5]} {digits[5:]}",
        "phone_dashed": f"{digits[:5]}-{digits[5:]}",
        "phone_bracketed": f"({digits[:5]}) {digits[5:]}",
        "phone_slashed": f"{digits[:5]}/{digits[5:]}",
        "phone_wide": f"{digits[:5]}  -  {digits[5:]}",
        "last4": digits[-4:],
        "last5": digits[-5:],
        "last6": digits[-6:],
        "last4_spaced": " ".join(digits[-4:]),
    }


# Too short a word to look for by itself: the "JP" of "JP Nagar" stands in many other places.
MIN_WORD = 3
_TOKEN = re.compile(r"<[A-Z][A-Z0-9_]*>")


def message_of(request: dict[str, Any]) -> str:
    """The one part of a request that is made from what the customer wrote."""
    (message,) = [message for message in request["messages"] if message["role"] == "user"]
    return str(message["content"])


def rest_of(request: dict[str, Any]) -> str:
    """Everything else that would leave: the system prompt, the model, every setting."""
    others = [message for message in request["messages"] if message["role"] != "user"]
    return json.dumps({**request, "messages": others}, default=str, sort_keys=True)


def left_in(message: str, person: CustomerProfile, written: dict[str, str]) -> list[str]:
    """What of the customer is still in the message: any spelling exactly as it was written
    (standing by itself, not as letters inside another word), and the digits of each of their
    numbers however they are set apart."""
    lowered = message.lower()
    found = [
        f"{name}: {value}"
        for name, value in written.items()
        if len(value) >= MIN_WORD
        and re.search(rf"(?<![a-z]){re.escape(value.lower())}(?![a-z])", lowered)
    ]
    digits = re.sub(r"\D", "", _TOKEN.sub(" ", message))
    address = person.services[0].address
    for name, number in (
        ("phone", person.phone[-10:]),
        ("last four", person.phone[-4:]),
        ("pincode", address.pincode),
        ("customer number", person.customer_id[1:]),
        ("service number", person.services[0].service_id[1:]),
    ):
        if number in digits:
            found.append(f"digits of the {name}: {number}")
    return found


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_no_identifier_of_the_customer_is_in_anything_sent_to_the_model(
    provider: str,
) -> None:
    rng = random.Random(SEED)
    requests = Requests(provider)
    gateway = gateway_with(requests)
    leaks: list[tuple[str, list[str]]] = []

    for index in range(CUSTOMERS):
        person = profile(index, rng)
        written = spellings(person)
        for template in MESSAGES:
            text = template.format(**written)
            used = {name: value for name, value in written.items() if f"{{{name}}}" in template}
            _, call = await gateway.read_contact(text, known_values=personal_values(person))
            assert not call.degraded, call.error
            found = left_in(message_of(requests.sent[-1]), person, used)
            if found:
                leaks.append((text, found))

    assert len(requests.sent) == CUSTOMERS * len(MESSAGES)
    assert leaks == [], f"{len(leaks)} requests carried an identifier, for example {leaks[:3]}"
    # Outside the message nothing differs from one customer to the next: the system prompt,
    # the model and the settings are the same request every time.
    assert {rest_of(request) for request in requests.sent} == {rest_of(requests.sent[0])}


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_no_identifier_of_the_customer_is_in_a_question_about_guide_sections(
    provider: str,
) -> None:
    """The second thing a model is asked: which section of the customer's router guide fits
    what they wrote. Their words go with the question, masked the same way."""
    rng = random.Random(SEED)
    requests = Requests(provider)
    gateway = gateway_with(requests)
    leaks: list[tuple[str, list[str]]] = []

    for index in range(CUSTOMERS):
        person = profile(index, rng)
        written = spellings(person)
        for template in MESSAGES:
            text = template.format(**written)
            used = {name: value for name, value in written.items() if f"{{{name}}}" in template}
            reading = await gateway.answer_from_guide(
                text, GUIDE_SECTIONS, known_values=personal_values(person)
            )
            assert reading.call is not None
            assert not reading.call.degraded, reading.call.error
            # In the request, and in what the platform keeps of what the model was shown.
            found = left_in(message_of(requests.sent[-1]), person, used)
            found += left_in(reading.shown, person, used)
            if found:
                leaks.append((text, found))

    assert len(requests.sent) == CUSTOMERS * len(MESSAGES)
    assert leaks == [], f"{len(leaks)} requests carried an identifier, for example {leaks[:3]}"
    assert {rest_of(request) for request in requests.sent} == {rest_of(requests.sent[0])}


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize(
    ("full_name", "written"),
    [
        ("Mary-Anne D'Souza", "This is Mary Anne, internet down"),
        ("Mary-Anne D'Souza", "Maryanne DSouza here, no wifi"),
        ("Mary-Anne D'Souza", "mrs d\u2019souza speaking, router dead"),
        ("Mohammed Abdul Rahman", "Abdul bhai here. Rahmanji's line is down"),
        ("Menon, Asha", "Asha here, net down"),
    ],
)
async def test_names_with_hyphens_apostrophes_and_more_parts_are_masked(
    full_name: str, written: str, provider: str
) -> None:
    person = profile(0, random.Random(SEED), full_name=full_name)
    requests = Requests(provider)

    await gateway_with(requests).read_contact(written, known_values=personal_values(person))

    sent = message_of(requests.sent[-1]).lower()
    for part in re.split(r"[\W_]+", full_name):
        if len(part) >= MIN_WORD:
            assert not re.search(rf"(?<![a-z]){part.lower()}", sent), (part, sent)


async def test_the_check_itself_finds_what_is_left_in() -> None:
    """With only the patterns, and nothing known about the customer, most of these spellings
    go through: the test above is not passing because it cannot see them."""
    person = profile(0, random.Random(SEED))
    written = spellings(person)
    requests = Requests()
    gateway = gateway_with(requests)
    seen: set[str] = set()

    for template in MESSAGES:
        await gateway.read_contact(template.format(**written))
        used = {name: value for name, value in written.items() if f"{{{name}}}" in template}
        left = left_in(message_of(requests.sent[-1]), person, used)
        seen |= {found.split(":")[0] for found in left}

    assert {
        "first",
        "last",
        "area",
        "first_ji",
        "pincode_spaced",
        "phone_bracketed",
        "phone_wide",
        "last4",
        "last5",
        "last6",
        "mail_name",
        "customer_id_spaced",
        "digits of the phone",
        "digits of the pincode",
    } <= seen


@pytest.mark.parametrize(
    ("full_name", "parts"),
    [
        ("Asha Menon", ["Asha", "Menon"]),
        ("Mohammed Abdul Rahman", ["Mohammed", "Abdul", "Rahman"]),
        ("Mary-Anne D'Souza", ["Mary", "Anne", "D", "Souza"]),
        ("Cher", ["Cher"]),
    ],
)
def test_a_name_is_known_whole_and_in_each_of_its_parts(full_name: str, parts: list[str]) -> None:
    person = profile(0, random.Random(SEED), full_name=full_name)

    values = personal_values(person)

    assert values["NAME"] == full_name
    assert [values[f"NAME_{number}"] for number in range(1, len(parts) + 1)] == parts
    assert f"NAME_{len(parts) + 1}" not in values


def test_what_is_known_covers_the_number_its_last_digits_and_every_address() -> None:
    person = profile(0, random.Random(SEED))
    second = person.services[0].model_copy(
        update={
            "service_id": "S9999999",
            "address": person.services[0].address.model_copy(
                update={"area": "Bandra West", "city": "MUM", "pincode": "400050"}
            ),
        }
    )
    both = person.model_copy(update={"services": (person.services[0], second)})

    values = personal_values(both)

    digits = person.phone[-10:]
    first = person.services[0].address
    assert values["PHONE"] == digits
    assert [values[f"PHONE_LAST_{kept}"] for kept in (9, 6, 5, 4)] == [
        digits[-9:],
        digits[-6:],
        digits[-5:],
        digits[-4:],
    ]
    assert values["EMAIL_NAME"] == person.email.split("@")[0]
    assert values["CUSTOMER_NUMBER"] == person.customer_id[1:]
    assert (values["SERVICE_NUMBER"], values["SERVICE_NUMBER_2"]) == (
        person.services[0].service_id[1:],
        "9999999",
    )
    assert (values["AREA"], values["CITY"], values["PINCODE"]) == (
        first.area,
        first.city,
        first.pincode,
    )
    assert (values["AREA_2"], values["CITY_2"], values["PINCODE_2"]) == (
        "Bandra West",
        "MUM",
        "400050",
    )
    assert {"Bandra", "West"} <= {
        value for label, value in values.items() if label.startswith("AREA_WORD")
    }
