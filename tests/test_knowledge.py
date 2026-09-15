import pytest

import knowledge


def test_knowledge_backend_enabled_defaults_to_false(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_BACKEND", raising=False)
    assert knowledge.knowledge_backend_enabled() is False


def test_knowledge_backend_enabled_requires_exact_postgres_value(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "chroma")
    assert knowledge.knowledge_backend_enabled() is False
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "postgres")
    assert knowledge.knowledge_backend_enabled() is True


def test_chunk_text_splits_on_sentence_boundaries_with_overlap():
    text = "First sentence here. Second sentence here. Third sentence here.\n\nSecond paragraph sentence."
    chunks = knowledge.chunk_text(text, chunk_size=6, overlap=2)
    assert len(chunks) > 1
    assert all(chunk.strip() for chunk in chunks)


def test_chunk_text_returns_empty_list_for_blank_text():
    assert knowledge.chunk_text("   \n\n  ") == []


def test_upload_document_raises_when_backend_disabled(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_BACKEND", raising=False)
    with pytest.raises(RuntimeError, match="disabled"):
        knowledge.upload_document("tenant-a", "faq.txt", "Some content.")


def test_upload_document_inserts_each_chunk(monkeypatch):
    inserted = []
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "postgres")
    monkeypatch.setattr(knowledge, "insert_document_chunk", lambda tenant_id, document_id, index, chunk: inserted.append((tenant_id, document_id, index, chunk)))
    monkeypatch.setattr(knowledge, "chunk_text", lambda text, chunk_size=500, overlap=50: ["chunk one", "chunk two"])

    result = knowledge.upload_document("tenant-a", "faq.txt", "irrelevant")

    assert result["filename"] == "faq.txt"
    assert result["chunk_count"] == 2
    assert [entry[2] for entry in inserted] == [0, 1]
    assert all(entry[0] == "tenant-a" for entry in inserted)
    assert all(entry[1] == result["document_id"] for entry in inserted)


def test_retrieve_context_returns_empty_when_backend_disabled(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_BACKEND", raising=False)
    assert knowledge.retrieve_context("tenant-a", "What are your hours?") == ""


def test_retrieve_context_returns_empty_for_blank_question(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "postgres")
    assert knowledge.retrieve_context("tenant-a", "   ") == ""


def test_retrieve_context_passes_tenant_id_and_truncates_result(monkeypatch):
    captured = {}
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "postgres")
    monkeypatch.setattr(
        knowledge,
        "search_document_chunks",
        lambda tenant_id, query, limit: captured.update(tenant_id=tenant_id, query=query, limit=limit) or [{"chunk_text": "x" * 3000}],
    )

    context = knowledge.retrieve_context("tenant-a", "What are your hours?", max_results=4)

    assert captured == {"tenant_id": "tenant-a", "query": "What are your hours?", "limit": 4}
    assert len(context) == knowledge.MAX_CONTEXT_CHARS
