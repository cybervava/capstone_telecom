"""Build the guide index from the knowledge pack.

Only the ``router_guides`` folder is read. Policies and regulatory documents are not device
guidance, and the ``quarantine`` folder holds material that must never reach an answer: neither
is opened here, and the guide parser refuses any document that is not a device guide.
"""

from __future__ import annotations

from pathlib import Path

from skylink_care.db.database import Database
from skylink_care.rag.documents import load_guides
from skylink_care.rag.embedding import Embedder, HashingEmbedder
from skylink_care.rag.index import GuideIndex, IndexInfo

GUIDES_FOLDER = "router_guides"


class KnowledgePackError(ValueError):
    """The knowledge pack has no guides to index."""


async def build_guide_index(
    database: Database, knowledge_dir: Path, *, embedder: Embedder | None = None
) -> IndexInfo:
    folder = knowledge_dir / GUIDES_FOLDER
    if not folder.is_dir():
        raise KnowledgePackError(f"{folder} does not exist")
    guides = load_guides(folder)
    if not guides:
        raise KnowledgePackError(f"{folder} contains no guides")
    return await GuideIndex(database, embedder or HashingEmbedder()).rebuild(guides)
