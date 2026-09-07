from __future__ import annotations

import json
import os
import re
from pathlib import PurePosixPath

import boto3
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from core import audit, env
from dashboard import principal

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024


def admin(user: dict[str, str] = Depends(principal)) -> dict[str, str]:
    if user["role"] != "admin":
        raise HTTPException(403, "Organization administrator access is required.")
    return user


def safe_filename(filename: str) -> str:
    name = PurePosixPath(filename).name
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


@router.post("/documents")
async def upload_document(document: UploadFile = File(...), user: dict[str, str] = Depends(admin)) -> dict[str, str]:
    content = await document.read(MAX_DOCUMENT_BYTES + 1)
    if not content or len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(400, "Document must be between 1 byte and 10 MB.")
    name = safe_filename(document.filename or "knowledge.txt")
    key = f"knowledge/{user['tenant_id']}/{name}"
    storage = boto3.client("s3", region_name=env("AWS_REGION"))
    storage.put_object(Bucket=env("CALL_DATA_BUCKET"), Key=key, Body=content, ServerSideEncryption="aws:kms", SSEKMSKeyId=env("CALL_DATA_KMS_KEY_ID"), ContentType=document.content_type or "application/octet-stream")
    storage.put_object(Bucket=env("CALL_DATA_BUCKET"), Key=f"{key}.metadata.json", Body=json.dumps({"metadataAttributes": {"tenant_id": user["tenant_id"]}}).encode(), ServerSideEncryption="aws:kms", SSEKMSKeyId=env("CALL_DATA_KMS_KEY_ID"), ContentType="application/json")
    boto3.client("bedrock-agent", region_name=env("AWS_REGION")).start_ingestion_job(knowledgeBaseId=env("KNOWLEDGE_BASE_ID"), dataSourceId=env("KNOWLEDGE_DATA_SOURCE_ID"))
    audit(user["tenant_id"], user["sub"], "knowledge_document_uploaded", detail={"key": key})
    return {"status": "ingestion_started", "document_key": key}


def retrieve_context(tenant_id: str, question: str) -> str:
    if not os.getenv("KNOWLEDGE_BASE_ID"):
        return ""
    response = boto3.client("bedrock-agent-runtime", region_name=env("AWS_REGION")).retrieve(knowledgeBaseId=env("KNOWLEDGE_BASE_ID"), retrievalQuery={"text": question}, retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 4, "filter": {"equals": {"key": "tenant_id", "value": tenant_id}}}})
    excerpts = [result.get("content", {}).get("text", "") for result in response.get("retrievalResults", [])]
    return "\n\n".join(excerpt for excerpt in excerpts if excerpt)[:12000]