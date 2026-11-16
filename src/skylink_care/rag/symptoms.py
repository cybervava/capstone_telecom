"""What kind of fault the customer describes, as far as plain words can tell.

A router guide has one section for each kind of fault a customer can work on: no service,
Wi-Fi, slow speed. Search by shared words cannot tell them apart when the customer's words are
not the guide's ("my broadband is down" shares nothing with a section called "No internet"), so
the kind of fault is read from the customer's words first and the search is told which section
to prefer. With no kind recognised, the section that works out whether the line or the router
is at fault is the place to start.

These are keyword rules, like the offline triage rules, and they know only the words they were
given. They choose between sections of the customer's own router's guide, all of which are safe
to send; they decide nothing else. Where they are wrong the customer gets steps that do not fit,
says so, and a person takes over.
"""

from __future__ import annotations

import re

NO_INTERNET, WIFI, SLOW_SPEED = "no-internet", "wifi", "slow-speed"
# Where to start when the words name no kind of fault.
GENERAL_SECTION = NO_INTERNET

_SLOW_WORDS = r"slow\w*|buffer\w*|lag\w*|speeds?|latency|ping"
_SLOW = re.compile(
    rf"\b(?:{_SLOW_WORDS})\b|\b[mk]bps\b|\btakes? forever\b|\bdheere\b|\bdheema\b",
    re.IGNORECASE,
)
# "not slow", "no lag": the customer rules it out, so it is not what they describe.
_NOT_SLOW = re.compile(
    rf"\b(?:not|no|isn'?t|wasn'?t|never)\s+(?:very\s+|too\s+|that\s+|any\s+)?(?:{_SLOW_WORDS})\b",
    re.IGNORECASE,
)
_WIFI = re.compile(r"\bwi[\s-]?fi\b|\bwireless\b|\bssid\b", re.IGNORECASE)
# Wi-Fi named as the thing that works: the fault is behind it, not in it.
_WIFI_WORKS = re.compile(
    r"\bwi[\s-]?fi (?:is |are |shows? )?(?:connected|working|works|fine|ok(?:ay)?)\b"
    r"|\bconnected to (?:the |my )?wi[\s-]?fi\b",
    re.IGNORECASE,
)
_NO_SERVICE = re.compile(
    r"\bno (?:internet|net|connection|connectivity|network|signal)\b"
    r"|\bnot working\b|\bisn'?t working\b|\bstopped\b|\bdown\b|\bdead\b|\boffline\b|\bgone\b"
    r"|\bnot loading\b|\bnothing (?:loads|opens|works)\b|\bcan(?:'?t|not) connect\b"
    r"|\bred light\b|\blight is red\b|\blos\b"
    r"|\bnahi chal\b|\bchal nahi\b|\bchal hi nahi\b|\bband ho\b|\bband hai\b",
    re.IGNORECASE,
)


def section_hint(text: str) -> str | None:
    """The section the customer's words point to, or None when they name no kind of fault.

    No service outranks slow: "not working, it was slow yesterday" is a customer without
    service. Wi-Fi counts when it is the thing that fails and nothing says the service itself
    is gone.
    """
    wifi_fault = bool(_WIFI.search(text)) and not _WIFI_WORKS.search(text)
    no_service = bool(_NO_SERVICE.search(text))
    slow = bool(_SLOW.search(_NOT_SLOW.sub(" ", text)))
    if wifi_fault and not slow:
        return WIFI
    if no_service:
        return NO_INTERNET
    if slow:
        return SLOW_SPEED
    return None


def latest_hint(messages: str) -> str | None:
    """The kind of fault named in the most recent of the customer's messages (one a line)
    that names one. What they describe now counts before what they described earlier."""
    for message in reversed(messages.splitlines()):
        hint = section_hint(message)
        if hint is not None:
            return hint
    return None
