"""What a model may add to an answer from a guide: only words that are already in a source.

A model that is shown a customer's words beside the guide sections found for them answers
with the id of a section and with two passages it points to: the customer's own description of
the fault, and the sentence of the section that says what it is for. Nothing it writes reaches
the customer as its own words. Each passage is looked up in the text it claims to come from, and
what is used is that text's own clause or sentence, whole, in its own characters. One that is
not there, or that has no place in a message of the assistant's, is left out, and the answer
goes without it: the steps themselves are quoted by the platform from the index, whatever the
model answered.

So an answer is grounded by construction, and this module is where that is enforced:

* the model's words must stand in one line of the source, word for word and at word
  boundaries. A paraphrase, words stitched from two lines, and words the source does not have
  are all "not in source";
* the model only points. What is quoted is the whole clause of the customer's message, or the
  whole sentence of the guide, that its words stand in. It cannot carve "working fine" out of
  "not working fine", or an instruction out of the sentence that forbids it;
* what is quoted is put between quotation marks in the message, so it may carry none of its
  own. A customer's clause is said back only if it is made of plain characters: a quotation
  mark would close the quotation early and leave the rest standing as the assistant's words,
  and a letter of another alphabet can look like one of these and be read as none of them;
* a passage that names personal data (a masking token), speaks of money, of a promise or of
  what SkyLink will do, sends the customer somewhere, names something unsafe or speaks to an
  assistant is not repeated, although it is in its source: a customer can write such a
  sentence in order to have it said back;
* nothing quoted may say anything about the state of the network. That is said by the
  message's own template, on the evidence of the turn (``guardrails.claims``).

The checks on what is said back are lists of what has no place in an answer, not a proof that
a clause is harmless: what gets through them is still shown as the customer's own words, in
quotation marks, to the customer who wrote them.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from skylink_care.guardrails.claims import claims_in_words
from skylink_care.guardrails.pii import normalise
from skylink_care.llm.offline import second_look
from skylink_care.rag.documents import names_a_hazard, not_a_step

# A description of a fault is a few words; the sentence that says what a section is for is one
# sentence. Longer passages are not what was asked for.
MAX_CUSTOMER_CHARS = 120
MAX_GUIDE_CHARS = 240
MIN_CHARS = 8
MIN_WORDS = 2

NOT_IN_SOURCE = "NOT_IN_SOURCE"
TOO_LONG = "TOO_LONG"
TOO_SHORT = "TOO_SHORT"
PERSONAL_DATA = "PERSONAL_DATA"
NOT_FOR_AN_ANSWER = "NOT_FOR_AN_ANSWER"
NOT_A_FAULT_DESCRIPTION = "NOT_A_FAULT_DESCRIPTION"
INJECTION = "INJECTION"
ABUSE = "ABUSE"

# Where a clause of a customer's message ends, and where a sentence of a guide does. A mark
# counts only before a space or at the end of the line: "192.168.1.1" and "2.4 GHz" are whole.
_CLAUSE_END = re.compile(r"[.!?;,](?=\s|$)")
_SENTENCE_END = re.compile(r"[.!?:](?=\s|$)")
# The number or bullet a line of a guide may open with: not part of its sentence.
_LIST_MARKER = re.compile(r"^\s*(?:\d+[.)]|[-*\u2022])\s+")
# What a model may put around a passage it copies, and what closes a sentence in the source.
_WRAPPING = " \t\r\n\"'\u2018\u2019\u201c\u201d`"
_CLOSING = " .:;,"
# A token the masking put in place of personal data: <NAME>, <PHONE_1>.
_MASK_TOKEN = re.compile(r"<[A-Z][A-Z_]*(?:_\d+)?>")
# What a customer's clause may be made of to be said back: the letters and digits of plain
# English or of Hindi written in Latin letters, and the few marks a description of a fault
# needs. No quotation mark, no markup, no colon to name a speaker, no look-alike letter.
_PLAIN = re.compile(r"[A-Za-z0-9 '\u2019()/%+&-]+")
# No passage carries a quotation mark of its own into the quotation it is put in.
_QUOTATION_MARK = re.compile(r'["\u201c\u201d\u201e\u201f\u2033\u2036\u00ab\u00bb\uff02]')
# Words of a request, of a promise or of a claim, not of a fault in the home. A customer's
# description of their fault has none of them; a clause that does is not said back.
_NOT_A_SYMPTOM = re.compile(
    r"\b(?:outages?|maintenance|technicians?|engineers?|visits?|appointments?"
    r"|restor\w*|resolved|supervisors?|managers?|complaints?|legal|lawyers?"
    r"|skylink|guarantee\w*|promis\w*|waiv\w*|free|discounts?|replac\w*|entitled"
    r"|approv\w*|confirm\w*|bills?|billing|accounts?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Quoted:
    """A passage a model pointed to, as its source has it, or why it is not used.

    Both are None when the model offered nothing: that is neither a quotation nor a refusal.
    """

    text: str | None = None
    refused: str | None = None


def quote_from_customer(masked_text: str, offered: str) -> Quoted:
    """The customer's own description of the fault: the clause of their message that the
    model's words stand in, as they wrote it.

    ``masked_text`` is what the model was shown: one message a line, personal data replaced
    by tokens. A clause with a token in it is not used, so nothing personal is said back.
    """
    found = _located(masked_text, offered, MAX_CUSTOMER_CHARS, _CLAUSE_END)
    if found.text is None:
        return found
    if _PLAIN.fullmatch(found.text) is None:
        return Quoted(refused=NOT_FOR_AN_ANSWER)
    words = second_look(found.text)
    if words.injection:
        return Quoted(refused=INJECTION)
    if words.abuse:
        return Quoted(refused=ABUSE)
    if _NOT_A_SYMPTOM.search(found.text):
        return Quoted(refused=NOT_A_FAULT_DESCRIPTION)
    # A guide may name something unsafe in order to forbid it. A customer's clause that names
    # it is not said back at all, whatever it says of it.
    if names_a_hazard(found.text):
        return Quoted(refused=NOT_FOR_AN_ANSWER)
    return _fit_for_an_answer(found)


def quote_from_guide(body: str, offered: str) -> Quoted:
    """The sentence of the chosen section that the model's words stand in, as the guide has it.

    The steps and headings of a guide are checked when it is indexed; the text around them is
    not, so a sentence of it is checked here, where it would leave.
    """
    found = _located(body, offered, MAX_GUIDE_CHARS, _SENTENCE_END)
    if found.text is None:
        return found
    if (
        normalise(found.text) != found.text
        or not _in_latin_letters(found.text)
        or _QUOTATION_MARK.search(found.text)
    ):
        # A character that is not what it looks like, or one that cannot be seen, is how a
        # word is hidden from the checks below; a quotation mark would end the quotation.
        return Quoted(refused=NOT_FOR_AN_ANSWER)
    return _fit_for_an_answer(found)


def _in_latin_letters(text: str) -> bool:
    """Whether every letter of the text is a Latin one. The guides are in English: a letter
    of another alphabet in one of their sentences is there to look like a Latin letter."""
    return all("LATIN" in unicodedata.name(char, "") for char in text if char.isalpha())


def _located(source: str, offered: str, max_chars: int, ends: re.Pattern[str]) -> Quoted:
    """The whole clause or sentence of the source that the model's words stand in, when they
    are there and it is what was asked for: not too long, more than a word, nothing personal."""
    wanted = offered.strip(_WRAPPING)
    if not wanted:
        return Quoted()
    if len(wanted) > max_chars:
        return Quoted(refused=TOO_LONG)
    pointed_at = wanted.rstrip(_CLOSING)
    if len(pointed_at) < MIN_CHARS or len(pointed_at.split()) < MIN_WORDS:
        # One word points at nothing in particular: it stands in many clauses.
        return Quoted(refused=TOO_SHORT)
    passage = _whole_unit_around(source, pointed_at, ends)
    if passage is None:
        return Quoted(refused=NOT_IN_SOURCE)
    if len(passage) > max_chars:
        return Quoted(refused=TOO_LONG)
    if _MASK_TOKEN.search(passage):
        return Quoted(refused=PERSONAL_DATA)
    return Quoted(text=passage)


def _fit_for_an_answer(found: Quoted) -> Quoted:
    """Words that are in their source and still have no place in a message of the
    assistant's: money, an address or a number to go to, something unsafe, words for an
    assistant, or a claim about the state of the network."""
    if found.text is None:
        return found
    if not_a_step(found.text) is not None or claims_in_words(found.text):
        return Quoted(refused=NOT_FOR_AN_ANSWER)
    return found


def _whole_unit_around(source: str, wanted: str, ends: re.Pattern[str]) -> str | None:
    """The clause or sentence of one line of the source in which these words stand, in the
    source's own characters with its spaces closed up and without the mark that closes it.

    Capitals and spacing may differ between the model's words and the source; words may not.
    The model's words must lie inside one unit: what straddles the end of a sentence is two
    passages, not one.
    """
    pattern = re.compile(
        r"(?<!\w)" + r"[ \t]+".join(re.escape(word) for word in wanted.split()) + r"(?!\w)",
        re.IGNORECASE,
    )
    for line in source.splitlines():
        text = _LIST_MARKER.sub("", line)
        match = pattern.search(text)
        if match is None:
            continue
        marks = [mark.start() for mark in ends.finditer(text)]
        if any(match.start() <= mark < match.end() for mark in marks):
            return None
        begins = max((mark + 1 for mark in marks if mark < match.start()), default=0)
        stops = min((mark for mark in marks if mark >= match.end()), default=len(text))
        return " ".join(text[begins:stops].split()).strip(_CLOSING)
    return None
