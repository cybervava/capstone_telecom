"""The kind of fault read from a customer's words, which tells the guide search what to prefer."""

from __future__ import annotations

import pytest

from skylink_care.rag.symptoms import (
    GENERAL_SECTION,
    NO_INTERNET,
    SLOW_SPEED,
    WIFI,
    latest_hint,
    section_hint,
    without_what_is_ruled_out,
)


@pytest.mark.parametrize(
    ("text", "section"),
    [
        ("my broadband is down", NO_INTERNET),
        ("net nahi chal raha", NO_INTERNET),
        ("internet band ho gaya hai", NO_INTERNET),
        ("there is a red light on the router", NO_INTERNET),
        ("pages are not loading at all", NO_INTERNET),
        ("the internet is very slow today", SLOW_SPEED),
        ("speed is very slow, videos keep buffering", SLOW_SPEED),
        ("net bahut slow hai", SLOW_SPEED),
        ("getting 2 Mbps on a 100 Mbps plan", SLOW_SPEED),
        ("wifi is not showing up on my phone", WIFI),
        ("wi-fi chal nahi raha", WIFI),
        ("the Wi Fi password is not accepted", WIFI),
    ],
)
def test_words_that_name_a_kind_of_fault_point_to_its_section(text: str, section: str) -> None:
    assert section_hint(text) == section


def test_wifi_that_works_is_not_a_wifi_fault() -> None:
    """The customer names Wi-Fi as the part that is fine: the fault is behind it."""
    assert section_hint("wifi is connected but there is no internet") == NO_INTERNET
    assert section_hint("I am connected to the wifi but nothing opens") == NO_INTERNET


def test_slow_wifi_is_a_question_of_speed() -> None:
    assert section_hint("the wifi is very slow upstairs") == SLOW_SPEED


@pytest.mark.parametrize(
    "text",
    ["", "hello", "please check", "the router lights are blinking orange", "it was fine yesterday"],
)
def test_words_that_name_no_kind_of_fault_give_no_hint(text: str) -> None:
    assert section_hint(text) is None
    assert GENERAL_SECTION == NO_INTERNET  # where the search is then nudged to start


@pytest.mark.parametrize(
    ("text", "section"),
    [
        ("internet is down, no lag", NO_INTERNET),
        ("internet is not slow but down", NO_INTERNET),
        ("it isn't very slow, it is dead", NO_INTERNET),
        ("internet is not down, just slow", SLOW_SPEED),
        ("speed is fine, wifi keeps dropping", WIFI),
        ("no problem with speed, wifi drops", WIFI),
        ("speed is not the problem, wifi keeps dropping", WIFI),
        ("internet isn\u2019t down, just slow", SLOW_SPEED),
        ("it is not slow", None),
        ("no more lag", None),
        ("internet slow nahi hai", None),
        ("no red light on the router", None),
    ],
)
def test_what_the_customer_rules_out_is_not_the_fault(text: str, section: str | None) -> None:
    """Found by review: "no lag" once sent the slow-speed steps to a customer with no service,
    and "not down, just slow" the no-service steps to a customer with a slow line."""
    assert section_hint(text) == section


@pytest.mark.parametrize(
    "text",
    [
        "wifi shows no internet",
        "I have no internet on wifi",
        "wifi is not slow, internet is down",
        "wifi is good. internet is down",
        "wifi works but internet down",
        "internet gone after wifi reset",
        "my internet is not working, it was slow yesterday",
    ],
)
def test_the_service_named_as_gone_outranks_everything_else_in_the_message(text: str) -> None:
    """Found by review: a mention of Wi-Fi once outranked "no internet" said in so many words."""
    assert section_hint(text) == NO_INTERNET


def test_only_a_state_is_ruled_out_by_no_or_not() -> None:
    """ "No lag" rules lag out. "No speed at all" is the fault itself."""
    assert section_hint("I get no speed at all") == SLOW_SPEED
    assert "speed" in without_what_is_ruled_out("I get no speed at all")
    assert section_hint("the speed is not good") == SLOW_SPEED


@pytest.mark.parametrize(
    ("text", "section"),
    [
        ("I can't connect to the wifi", WIFI),
        ("cannot connect my phone to wifi", WIFI),
        ("can\u2019t connect to the wifi", WIFI),
        ("can't connect to anything at home", NO_INTERNET),
        ("wifi is connected but I can't connect to any site", NO_INTERNET),
        ("wifi working nahi kar raha", WIFI),
        ("wifi connected nahi ho raha", WIFI),
        ("my phone is not connected to the wifi", WIFI),
    ],
)
def test_what_cannot_be_joined_decides_between_wifi_and_the_line(text: str, section: str) -> None:
    """Found by review: "I can't connect to the wifi" was once read as a line that is down,
    and "wifi connected nahi ho raha" as Wi-Fi that works."""
    assert section_hint(text) == section


@pytest.mark.parametrize(
    ("text", "section"),
    [
        ("my fibre connection is dead", NO_INTERNET),
        ("network is completely gone", NO_INTERNET),
        ("now it is not working at all", NO_INTERNET),
        ("not working since morning", NO_INTERNET),
        ("wifi not working, router is fine", WIFI),
        ("my laptop is not working on wifi", WIFI),
        ("it isn\u2019t working", NO_INTERNET),
        ("its still down", NO_INTERNET),
        ("net to chal hi nahi raha", NO_INTERNET),
        ("my phone is dead so I can't test", None),
        ("he has gone out, call after 6", None),
        ("I'll be offline till evening", None),
        ("the light is gone since morning", None),
        # Named, but the word is said of something else.
        ("I restarted the router. My phone is dead", None),
        ("router ok, laptop dead", None),
        ("this phone is dead", None),
        ("the router is in the dead zone", None),
        ("dead zone in my bedroom", None),
        ("internet speed goes down every evening", SLOW_SPEED),
    ],
)
def test_a_word_like_dead_or_gone_counts_only_when_said_of_the_service(
    text: str, section: str | None
) -> None:
    assert section_hint(text) == section


@pytest.mark.parametrize(
    ("text", "section"),
    [
        ("the video is lagging", SLOW_SPEED),
        ("game me bahut lag ho raha hai", SLOW_SPEED),
        ("router kharab lagta hai", None),
        ("wifi kharab lag raha hai", WIFI),
        ("I'll ping you tomorrow", None),
        ("ping is very high while gaming", SLOW_SPEED),
    ],
)
def test_words_that_only_look_like_slowness_are_not(text: str, section: str | None) -> None:
    """ "Lagta hai" and "lag raha hai" mean "it seems": the independent descriptions of the
    retrieval evaluation showed one of them read as lag. "I'll ping you" is not about ping."""
    assert section_hint(text) == section


def test_what_is_ruled_out_is_taken_from_the_words_given_to_the_search() -> None:
    kept = without_what_is_ruled_out("pages time out, it is not slow and the speed is fine")

    assert "slow" not in kept
    assert "speed" not in kept
    assert "pages time out" in kept
    assert without_what_is_ruled_out("my internet is slow") == "my internet is slow"


def test_an_answer_on_one_line_does_not_rule_out_the_next_message() -> None:
    """Each line is a message of its own: "no" answers a question, it does not negate the
    "down" that opens the customer's next message."""
    conversation = "is the light red?\nno\ndown since morning"

    assert "down since morning" in without_what_is_ruled_out(conversation)
    assert latest_hint(conversation) == NO_INTERNET


def test_what_the_customer_describes_now_counts_before_what_they_described_earlier() -> None:
    history = "my internet is very slow today\nthank you\nnow it is not working at all"

    assert latest_hint(history) == NO_INTERNET
    assert latest_hint("wifi password is not accepted\nplease help") == WIFI
    assert latest_hint("hello\nplease check") is None


def test_later_message_about_something_else_does_not_replace_the_fault() -> None:
    """Found by review: "my phone is dead so I can't test" once turned a Wi-Fi fault into a
    line that is down."""
    assert latest_hint("wifi keeps dropping\n1\nmy phone is dead so I can't test") == WIFI
    assert latest_hint("the wifi is not showing up\nok\nthat did not help") == WIFI
