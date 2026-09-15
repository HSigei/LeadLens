from __future__ import annotations

import os
import re
import uuid
from typing import Any

from core import insert_document_chunk, search_document_chunks


CHUNK_SIZE_WORDS = 500
CHUNK_OVERLAP_WORDS = 50
MAX_CONTEXT_CHARS = 2000


def knowledge_backend_enabled() -> bool:
    return os.getenv("KNOWLEDGE_BACKEND", "disabled") == "postgres"


def _split_sentences(paragraph: str) -> list[str]:
    return [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", paragraph) if sentence.strip()]


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE_WORDS, overlap: int = CHUNK_OVERLAP_WORDS) -> list[str]:
    units: list[str] = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if paragraph:
            units.extend(_split_sentences(paragraph))
    if not units:
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for unit in units:
        unit_word_count = len(unit.split())
        if current and current_words + unit_word_count > chunk_size:
            chunks.append(" ".join(current))
            current = " ".join(current).split()[-overlap:] if overlap else []
            current_words = len(current)
        current.append(unit)
        current_words += unit_word_count
    if current:
        chunks.append(" ".join(current))
    return chunks


def upload_document(tenant_id: str, filename: str, text_content: str) -> dict[str, Any]:
    if not knowledge_backend_enabled():
        raise RuntimeError("Knowledge backend is disabled.")
    document_id = str(uuid.uuid4())
    chunks = chunk_text(text_content)
    for index, chunk in enumerate(chunks):
        insert_document_chunk(tenant_id, document_id, index, chunk)
    return {"document_id": document_id, "filename": filename, "chunk_count": len(chunks)}


def retrieve_context(tenant_id: str, question: str, max_results: int = 4) -> str:
    if not knowledge_backend_enabled() or not question.strip():
        return ""
    matches = search_document_chunks(tenant_id, question, limit=max_results)
    context = "\n\n".join(match["chunk_text"] for match in matches)
    return context[:MAX_CONTEXT_CHARS]
