"""The guide index: build it, and find the section that answers a customer's problem.

Retrieval is filtered before it is ranked. The device model is a hard filter, taken from the
customer's service record and never from what they wrote, so a customer can only ever be shown
the guide for the router they actually have. The role filter and the list of sections an
automated answer may use are applied in the same query. Ranking then combines a lexical score
with vector similarity over the few sections that are left.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from psycopg.types.json import Jsonb

from skylink_care.db.base import Repository
from skylink_care.db.database import Database
from skylink_care.hashing import canonical_hash
from skylink_care.rag.documents import DEVICE_GUIDE, Guide
from skylink_care.rag.embedding import Embedder, searchable, to_literal

# The score is a sum of three signals, strongest first: the customer's words name the section's
# topic; the customer's words appear in the section; the texts are close in the hashed space.
TITLE_WEIGHT = 1.0
LEXICAL_WEIGHT = 4.0
_QUERY_WORD = re.compile(r"[a-z]{3,}")
_MAX_QUERY_WORDS = 24


class GuideIndexUnavailableError(RuntimeError):
    """There is no index, or it was built by a different embedder than the one asking."""


@dataclass(frozen=True)
class Passage:
    doc_id: str
    doc_version: str
    device_model: str
    section_id: str
    title: str
    body: str
    steps: tuple[str, ...]
    score: float

    @property
    def citation(self) -> str:
        return f"{self.doc_id} §{self.section_id} v{self.doc_version}"


@dataclass(frozen=True)
class IndexInfo:
    index_version: str
    embedder: str
    documents: int
    chunks: int
    built_at: datetime


class GuideIndex(Repository):
    def __init__(self, database: Database, embedder: Embedder) -> None:
        super().__init__(database)
        self._embedder = embedder

    async def rebuild(self, guides: Sequence[Guide]) -> IndexInfo:
        """Replace the whole index with these guides, atomically."""
        chunks = [(guide, section) for guide in guides for section in guide.sections]
        vectors = self._embedder.embed(
            [f"{guide.title}: {section.title}\n{section.body}" for guide, section in chunks]
        )
        version = canonical_hash(
            {"embedder": self._embedder.name, "guides": sorted(g.content_hash for g in guides)}
        )[:16]
        async with self._db.transaction() as conn:
            await conn.execute("DELETE FROM guide_chunks")
            async with conn.cursor() as cursor:
                await cursor.executemany(
                    "INSERT INTO guide_chunks (chunk_id, doc_id, doc_type, device_model,"
                    " doc_version, section_id, title, body, search_title, search_body, steps,"
                    " access_roles, content_hash, embedding)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)",
                    [
                        (
                            f"{guide.doc_id}#{section.section_id}",
                            guide.doc_id,
                            guide.doc_type,
                            guide.device_model,
                            guide.version,
                            section.section_id,
                            f"{guide.title}: {section.title}",
                            section.body,
                            # Only the section's own title is searched: the guide title is
                            # the same for every section and would drown it out.
                            searchable(section.title),
                            searchable(section.body),
                            Jsonb(list(section.steps)),
                            list(guide.access_roles),
                            guide.content_hash,
                            to_literal(vector),
                        )
                        for (guide, section), vector in zip(chunks, vectors, strict=True)
                    ],
                )
            await conn.execute(
                "INSERT INTO guide_index (id, index_version, embedder, documents, chunks)"
                " VALUES (true, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET"
                " index_version = EXCLUDED.index_version, embedder = EXCLUDED.embedder,"
                " documents = EXCLUDED.documents, chunks = EXCLUDED.chunks,"
                " built_at = clock_timestamp()",
                (version, self._embedder.name, len(guides), len(chunks)),
            )
        info = await self.info()
        if info is None:  # pragma: no cover - written in the transaction above
            raise RuntimeError("guide index row is missing after a rebuild")
        return info

    async def info(self) -> IndexInfo | None:
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                "SELECT index_version, embedder, documents, chunks, built_at FROM guide_index"
            )
            row = await cursor.fetchone()
        return IndexInfo(**row) if row else None

    async def search(
        self,
        query: str,
        *,
        device_model: str,
        role: str,
        sections: Sequence[str],
        limit: int = 3,
        prefer: str | None = None,
        preference: float = 0.0,
    ) -> list[Passage]:
        """The best sections of this model's guide for the query, among the sections allowed.

        ``prefer`` names a section the caller has reason to expect (the kind of fault the
        customer described); ``preference`` is how much that counts against the words.
        """
        info = await self.info()
        if info is None or info.chunks == 0:
            raise GuideIndexUnavailableError("the guide index has not been built")
        if info.embedder != self._embedder.name:
            raise GuideIndexUnavailableError(
                f"the index was built with {info.embedder}, not {self._embedder.name}"
            )
        (vector,) = self._embedder.embed([query])
        if not any(vector):
            # Nothing in the text can be searched for (no words at all, or none we index).
            # There is no best section for an unreadable problem, and a zero vector has no
            # direction to compare.
            return []
        async with self._db.connection() as conn:
            cursor = await conn.execute(
                _SEARCH,
                (
                    _any_word(query),
                    TITLE_WEIGHT,
                    LEXICAL_WEIGHT,
                    to_literal(vector),
                    prefer,
                    preference if prefer else 0.0,
                    device_model,
                    DEVICE_GUIDE,
                    role,
                    list(sections),
                    limit,
                ),
            )
            rows = await cursor.fetchall()
        return [Passage(**{**row, "steps": tuple(row["steps"])}) for row in rows]


# A section scores by its title and its words against the customer's, by how close its
# embedding is, and by whether it is the section the caller expects.
_SEARCH = (
    "WITH asked AS (SELECT to_tsquery('english', NULLIF(%s, '')) AS words)"
    " SELECT doc_id, doc_version, device_model, section_id, title, body, steps,"
    " COALESCE((title_search @@ asked.words)::int, 0) * %s"
    "   + COALESCE(ts_rank(search, asked.words), 0) * %s"
    "   + (1 - (embedding <=> %s::vector))"
    "   + CASE WHEN section_id = %s THEN %s ELSE 0 END AS score"
    " FROM guide_chunks, asked"
    " WHERE device_model = %s AND doc_type = %s AND %s = ANY(access_roles)"
    "   AND section_id = ANY(%s)"
    " ORDER BY score DESC, section_id LIMIT %s"
)


def _any_word(query: str) -> str:
    """A text-search query matching any word of the customer's text. Built from letters only."""
    words = list(dict.fromkeys(_QUERY_WORD.findall(searchable(query))))[:_MAX_QUERY_WORDS]
    return " | ".join(words)
