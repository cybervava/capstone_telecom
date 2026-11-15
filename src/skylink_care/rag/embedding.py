"""Text embeddings behind one small interface.

The default embedder needs no model, no download and no network: it hashes words and word pairs
into a fixed number of buckets. It is deterministic, which makes the index reproducible in tests
and CI, and it is honest about what it is: a lexical signal in vector form, not a language model.
A learned embedding model can replace it by implementing the same two attributes and one method;
the index records which embedder built it and refuses a query from a different one.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from itertools import pairwise
from typing import Protocol

DIMENSIONS = 256
_WORD = re.compile(r"[a-z0-9]+")
# Words too common to tell one guide section from another.
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "so",
        "that",
        "the",
        "their",
        "then",
        "there",
        "this",
        "to",
        "was",
        "will",
        "with",
        "you",
        "your",
        "i",
        "me",
        "we",
        "our",
        "have",
        "has",
        "do",
        "does",
        "did",
        "can",
    ]
)


# Customers and guides spell the same thing differently. Both sides of a search are folded to
# one spelling before they are compared; the text shown to a customer is never changed.
_VARIANTS = (
    (re.compile(r"\bwi[\s-]?fi\b"), "wifi"),
    (re.compile(r"\bre-?start(?:ed|ing|s)?\b|\bre-?boot(?:ed|ing|s)?\b"), "restart power cycle"),
    (re.compile(r"\bpower[\s-]cycl(?:e|ed|ing)\b"), "restart power cycle"),
    (re.compile(r"\b(?:drop(?:s|ped|ping)?|disconnect(?:s|ed|ing)?)\b"), "disconnect connect"),
    (re.compile(r"\b(?:slow(?:ly|er)?|buffer(?:s|ing)?|lag(?:s|ging)?)\b"), "slow speed"),
)


def searchable(text: str) -> str:
    """The text as it is searched: lower case, with spelling variants folded together."""
    folded = text.lower()
    for pattern, replacement in _VARIANTS:
        folded = pattern.sub(replacement, folded)
    return folded


class Embedder(Protocol):
    name: str
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Feature hashing of words and adjacent word pairs, L2-normalised.

    The name is part of the index's identity. Change it whenever the hashing or the folding of
    spelling variants changes, so that an index built the old way is rebuilt, not half-matched.
    """

    name = "hashing-v1"
    dimensions = DIMENSIONS

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        words = [word for word in _WORD.findall(searchable(text)) if word not in _STOP]
        features = words + [f"{first} {second}" for first, second in pairwise(words)]
        vector = [0.0] * self.dimensions
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector


def to_literal(vector: Sequence[float]) -> str:
    """A vector in the text form PostgreSQL's ``vector`` type reads."""
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"
