from __future__ import annotations

import hmac
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/custom-llm", tags=["custom-llm diagnostic"])
CAPTURE_FILE = Path("logs/vapi_request_capture.jsonl")


def capture_request(headers: dict[str, str], raw_body: bytes) -> None:
    CAPTURE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "captured_at": datetime.now(UTC).isoformat(),
        "headers": headers,
        "body": json.loads(raw_body),
    }
    with CAPTURE_FILE.open("a", encoding="utf-8") as capture:
        capture.write(json.dumps(payload, separators=(",", ":")) + "\n")


def verify_secret_key(secret_key: str) -> None:
    expected = os.getenv("CUSTOM_LLM_API_KEY")
    if not expected or not hmac.compare_digest(secret_key, expected):
        raise HTTPException(401, "Invalid Custom LLM API key.")


@router.post("/{secret_key}/chat/completions")
async def capture_custom_llm_request(secret_key: str, request: Request) -> dict[str, object]:
    verify_secret_key(secret_key)
    raw_body = await request.body()
    capture_request(dict(request.headers), raw_body)
    return {
        "id": "leadlens-diagnostic-response",
        "object": "chat.completion",
        "created": int(datetime.now(UTC).timestamp()),
        "model": "leadlens-diagnostic",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello, this is a test response."}, "finish_reason": "stop"}],
    }