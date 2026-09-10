import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import call_center_integration as integration


def test_preflight_ready_for_post_call_webhook():
    profile = integration.IntegrationProfile(mode="post_call_webhook", provider="existing-pbx", base_url="https://agent.example.test")
    result = integration.preflight(profile)
    assert result["ready"] is True
    assert result["blockers"] == []


def test_preflight_blocks_live_sip_until_media_adapter_exists():
    profile = integration.IntegrationProfile(mode="sip_media_stream", provider="existing-pbx", base_url="https://agent.example.test")
    result = integration.preflight(profile)
    assert result["ready"] is False
    assert "live_audio_adapter_configured" in result["blockers"]


def test_verify_signature_requires_exact_hmac(monkeypatch):
    secret = "s" * 32
    body = b'{"event_id":"evt-12345678"}'
    monkeypatch.setenv("CALL_CENTER_WEBHOOK_SECRET", secret)
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    integration.verify_signature(body, f"sha256={signature}")
    with pytest.raises(HTTPException, match="Invalid"):
        integration.verify_signature(body, "bad")


def test_signed_event_is_accepted_only_for_known_tenant(monkeypatch):
    secret = "s" * 32
    body = json.dumps({"event_id": "evt-12345678", "event_type": "call.completed", "call_id": "CA1", "tenant_id": "tenant-a"}).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    monkeypatch.setenv("CALL_CENTER_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(integration, "organizations_table", lambda: SimpleNamespace(get_item=lambda **kwargs: {"Item": {"tenant_id": "tenant-a"}}))
    monkeypatch.setattr(integration, "calls_table", lambda: SimpleNamespace(put_item=lambda **kwargs: None))
    monkeypatch.setattr(integration, "audit", lambda *args, **kwargs: None)
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request({"type": "http", "method": "POST", "path": "/api/call-center/events", "headers": [], "client": ("test", 1), "server": ("test", 80), "scheme": "http", "query_string": b""}, receive=receive)
    result = asyncio.run(integration.receive_event(request, f"sha256={signature}"))
    assert result == {"status": "accepted", "call_id": "CA1"}
