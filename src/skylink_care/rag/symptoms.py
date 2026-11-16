"""What kind of fault the customer describes, as far as plain words can tell.

A router guide has one section for each kind of fault a customer can work on: no service,
Wi-Fi, slow speed. Search by shared words cannot tell them apart when the customer's words are
not the guide's ("my broadband is down" shares nothing with a section called "No internet"), so
the kind of fault is read from the customer's words first and the search is told which section
to prefer. With no kind recognised, the section that works out whether the line or the router
is at fault is the place to start.

These are keyword rules, like the offline triage rules. They know only the words they were
given and they do not parse sentences: three principles stand in for that.

* What the customer rules out is not the fault ("not slow", "speed is fine", "isn't down").
* A word like "down", "dead" or "gone" says the service is gone only when it is said of the
  service: named just before it ("my broadband is down"), meant by "it", or left out at the
  start of the message. "My phone is dead" says nothing about the line.
* The service named as gone, or a red light on the router, outranks everything else in the
  message. After that, slow Wi-Fi is a question of speed, and Wi-Fi named as failing with
  nothing said of speed is a Wi-Fi fault.

They choose between sections of the customer's own router's guide, all of which are safe to
send; they decide nothing else. Where they are wrong the customer gets steps that do not fit,
says so, and a person takes over.
"""

from __future__ import annotations

import re

NO_INTERNET, WIFI, SLOW_SPEED = "no-internet", "wifi", "slow-speed"
# Where to start when the words name no kind of fault.
GENERAL_SECTION = NO_INTERNET

# Slowness as a state: "slow", "lagging". Not the Hindi "lagta hai" or "lag raha hai" (it
# seems). And the things that are slow or not: "speed", "ping" (not "I'll ping you").
_SLOW_STATES = r"slow\w*|buffer\w*|lag(?:s|gy|ging|ged)?(?!\s+rah[aie]\b)"
_SLOW_TOPICS = r"speeds?|latency|ping(?!\s+(?:you|me|him|her|them|us)\b)"
_SLOW = re.compile(
    rf"\b(?:{_SLOW_STATES}|{_SLOW_TOPICS})\b"
    r"|\b[mk]bps\b|\btakes? forever\b|\bdheere\b|\bdheema\b",
    re.IGNORECASE,
)
# Words that say something has failed without saying what.
_FAILS = (
    r"not working|isn'?t working|stopped|down|dead|offline|gone"
    r"|nahi chal|chal nahi|chal hi nahi|band ho|band hai"
)
# What the customer rules out, within one message: "not slow", "no more lag", "isn't down",
# "slow nahi hai", "speed is fine", "speed is not the problem". Only a state is ruled out by
# "no" or "not": "no speed at all" is a fault, not a fault ruled out.
_RULED_OUT = re.compile(
    r"\b(?:not|no|never|isn'?t|wasn'?t|aren'?t)[ \t]+(?:more[ \t]+|longer[ \t]+)?"
    r"(?:very[ \t]+|too[ \t]+|that[ \t]+|any[ \t]+|really[ \t]+|so[ \t]+)?"
    rf"(?:{_SLOW_STATES}|stopped|down|dead|offline|gone|red light)\b"
    r"|\b(?:slow|lag|buffering)[ \t]+(?:nahi|nahin|nhi)\b"
    rf"|\b(?:{_SLOW_TOPICS})[ \t]+(?:is[ \t]+|are[ \t]+|was[ \t]+|seems[ \t]+|looks[ \t]+)?"
    r"(?:fine|good|ok(?:ay)?|normal|great|alright|theek|thik|sahi"
    r"|not[ \t]+(?:the[ \t]+|a[ \t]+)?(?:problem|issue))\b"
    r"|\bno (?:problem|issue|complaint)s? with (?:the |my )?speeds?\b",
    re.IGNORECASE,
)
_WIFI = re.compile(r"\bwi[\s-]?fi\b|\bwireless\b|\bssid\b", re.IGNORECASE)
# Wi-Fi named as the thing that works: the fault is behind it, not in it. "Wifi connected
# nahi ho raha" and "not connected to the wifi" say the opposite.
_WIFI_WORKS = re.compile(
    r"\bwi[\s-]?fi (?:is |are |shows? )?(?:connected|working|works|fine|ok(?:ay)?)\b"
    r"(?!\s+(?:nahi|nahin|nhi)\b)"
    r"|(?<!not )(?<!n't )(?<!nt )\bconnected to (?:the |my )?wi[\s-]?fi\b",
    re.IGNORECASE,
)
# The service said to be absent in so many words, or shown as absent by the router.
_NO_SERVICE = re.compile(
    r"\bno (?:internet|net|connection|connectivity|network|signal)\b"
    r"|\bnot loading\b|\bnothing (?:loads|opens|works)\b"
    r"|\bred light\b|\blight is red\b|\blos\b",
    re.IGNORECASE,
)
# Said of nothing in particular: it is the line, unless Wi-Fi is what cannot be joined.
_CANNOT_CONNECT = re.compile(r"\bcan(?:'?t|not) connect\b", re.IGNORECASE)
# Something that has failed, said of the service: the service named, or "it", and then nothing
# but linking words ("is", "has been", "still", "bilkul") before the word; or the word opening
# the message ("not working since morning"). "The router is in the dead zone" is not that,
# and neither is "I restarted the router. My phone is dead".
_SERVICE = (
    r"internet|net|broadband|connection|fib(?:re|er)|network|line|router|modem"
    r"|it(?:'?s)?|everything"
)
_LINKING = (
    r"is|are|was|were|has|have|had|been|got|went|goes|keeps|seems|still|just|now|again"
    r"|completely|totally|fully|suddenly|but|bilkul|abhi|bhi|to"
)
_SERVICE_FAILS = re.compile(
    rf"(?:^\W*|\b(?:{_SERVICE})(?:[ \t]+(?:{_LINKING}))*[ \t]+)(?:{_FAILS})\b"
    r"(?!\s+(?:zone|spot)s?\b)",  # a dead zone is a place without Wi-Fi, not a dead line
    re.IGNORECASE,
)


def without_what_is_ruled_out(text: str) -> str:
    """The customer's words without the faults they rule out. The search is given these too:
    "not slow" names the section on slow speed, and means the opposite."""
    return _RULED_OUT.sub(" ", text.replace("\u2019", "'"))


def section_hint(text: str) -> str | None:
    """The section one message points to, or None when its words name no kind of fault."""
    said = without_what_is_ruled_out(text)
    slow = bool(_SLOW.search(said))
    wifi_fails = bool(_WIFI.search(said)) and not _WIFI_WORKS.search(said)
    cannot_connect = bool(_CANNOT_CONNECT.search(said)) and not wifi_fails
    if _NO_SERVICE.search(said) or _SERVICE_FAILS.search(said) or cannot_connect:
        return NO_INTERNET
    if wifi_fails and not slow:
        return WIFI
    return SLOW_SPEED if slow else None


def latest_hint(messages: str) -> str | None:
    """The kind of fault named in the most recent of the customer's messages (one a line)
    that names one. What they describe now counts before what they described earlier."""
    for message in reversed(messages.splitlines()):
        hint = section_hint(message)
        if hint is not None:
            return hint
    return None
