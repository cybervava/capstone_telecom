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
        ("my internet is not working, it was slow yesterday", NO_INTERNET),
        ("wifi works but internet down", NO_INTERNET),
        ("it isn't very slow, it is dead", NO_INTERNET),
    ],
)
def test_fault_the_customer_rules_out_or_only_mentions_is_not_the_fault(
    text: str, section: str
) -> None:
    """Found by review: "no lag" and "it was slow yesterday" once sent the slow-speed steps
    to a customer with no service at all."""
    assert section_hint(text) == section


def test_what_the_customer_describes_now_counts_before_what_they_described_earlier() -> None:
    history = "my internet is very slow today\nthank you\nnow it is not working at all"

    assert latest_hint(history) == NO_INTERNET
    assert latest_hint("wifi password is not accepted\nplease help") == WIFI
    assert latest_hint("hello\nplease check") is None
