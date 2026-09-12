from __future__ import annotations

import hashlib
import os
import re
from pathlib import PurePosixPath

import boto3
import chromadb
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from core import audit, env
from dashboard import principal
from storage import storage_client

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 100

_client: chromadb.ClientAPI | None = None


def _collection():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=os.getenv("CHROMA_PERSIST_DIR", "./data/chroma"), settings=chromadb.Settings(anonymized_telemetry=False))
    return _client.get_or_create_collection("tenant_knowledge")


def admin(user: dict[str, str] = Depends(principal)) -> dict[str, str]:
    if user["role"] != "admin":
        raise HTTPException(403, "Organization administrator access is required.")
    return user


def safe_filename(filename: str) -> str:
    name = PurePosixPath(filename).name
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = text.strip()
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap
    return chunks


@router.post("/documents")
async def upload_document(document: UploadFile = File(...), user: dict[str, str] = Depends(admin)) -> dict[str, str]:
    content = await document.read(MAX_DOCUMENT_BYTES + 1)
    if not content or len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(400, "Document must be between 1 byte and 10 MB.")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(400, "Only UTF-8 text documents are supported.") from error
    name = safe_filename(document.filename or "knowledge.txt")
    chunks = chunk_text(text)
    if not chunks:
        raise HTTPException(400, "Document has no extractable text.")
    doc_id = hashlib.sha256(f"{user['tenant_id']}:{name}".encode()).hexdigest()[:16]
    ids = [f"{doc_id}:{index}" for index in range(len(chunks))]
    _collection().upsert(ids=ids, documents=chunks, metadatas=[{"tenant_id": user["tenant_id"], "source": name} for _ in chunks])
    if os.getenv("CALL_DATA_BUCKET"):
        options = {"Bucket": env("CALL_DATA_BUCKET"), "Key": f"knowledge/{user['tenant_id']}/{name}", "Body": content, "ContentType": document.content_type or "text/plain"}
        if kms_key_id := os.getenv("CALL_DATA_KMS_KEY_ID"):
            options.update({"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": kms_key_id})
        storage_client().put_object(**options)
    audit(user["tenant_id"], user["sub"], "knowledge_document_uploaded", detail={"source": name, "chunks": len(chunks)})
    return {"status": "indexed", "chunks": str(len(chunks))}


def retrieve_context(tenant_id: str, question: str) -> str:
    collection = _collection()
    count = collection.count()
    if count == 0:
        return ""
    results = collection.query(query_texts=[question], n_results=min(4, count), where={"tenant_id": tenant_id})
    excerpts = results.get("documents") or [[]]
    return "\n\n".join(excerpt for excerpt in excerpts[0] if excerpt)[:12000]