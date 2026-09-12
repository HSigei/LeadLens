import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace

import outbound_agent
from fastapi import BackgroundTasks
from starlette.requests import Request


def request_for(body: bytes) -> Request:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/webhooks/test", "headers": [], "client": ("test", 1), "server": ("test", 80), "scheme": "http", "query_string": b""}, receive=receive)


def signature(secret: str, body: bytes) -> str:
    return f"sha256={hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}"


def test_signed_ghl_trigger_starts_vapi_call_and_records_correlation(monkeypatch):
    secret = "g" * 32
    body = json.dumps({"tenant_id": "tenant-a", "contact_id": "ghl-1", "phone_number": "+15550001111", "name": "Jane", "campaign_id": "renewals"}).encode()
    stored = []
    monkeypatch.setenv("GHL_OUTBOUND_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("VAPI_API_KEY", "vapi-key")
    monkeypatch.setenv("VAPI_ASSISTANT_ID", "assistant-1")
    monkeypatch.setenv("VAPI_PHONE_NUMBER_ID", "number-1")
    monkeypatch.setattr(outbound_agent, "calls_table", lambda: SimpleNamespace(put_item=lambda **kwargs: stored.append(kwargs)))
    monkeypatch.setattr(outbound_agent, "audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(outbound_agent, "tenant_by_id", lambda tenant_id: {"tenant_id": tenant_id})

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers, json):
            assert url == "https://api.vapi.ai/call"
            assert json["assistantId"] == "assistant-1"
            assert json["phoneNumberId"] == "number-1"
            assert json["customer"] == {"number": "+15550001111", "name": "Jane"}
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"id": "vapi-call-1"})

    monkeypatch.setattr(outbound_agent.httpx, "AsyncClient", lambda **kwargs: Client())
    result = asyncio.run(outbound_agent.start_ghl_outbound_call(request_for(body), signature(secret, body)))
    assert result == {"status": "started", "call_id": "vapi-call-1"}
    assert stored[0]["Item"]["ghl_contact_id"] == "ghl-1"
    assert stored[0]["Item"]["direction"] == "outbound"


def test_signed_vapi_callback_updates_outbound_call_and_queues_recording(monkeypatch):
    secret = "v" * 32
    body = json.dumps({"event_id": "event-1", "call_id": "vapi-call-1", "tenant_id": "tenant-a", "direction": "outbound", "status": "ended", "recording_url": "https://recording.example.test/call.mp3"}).encode()
    updates = []
    messages = []
    table = SimpleNamespace(
        get_item=lambda **kwargs: {"Item": {"call_sid": "vapi-call-1", "tenant_id": "tenant-a", "direction": "outbound"}},
        update_item=lambda **kwargs: updates.append(kwargs),
    )
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("PROCESSING_QUEUE_URL", "https://queue.example.test")
    monkeypatch.setattr(outbound_agent, "calls_table", lambda: table)
    monkeypatch.setattr(outbound_agent, "audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(outbound_agent.boto3, "client", lambda *args, **kwargs: SimpleNamespace(send_message=lambda **kwargs: messages.append(kwargs)))

    result = asyncio.run(outbound_agent.receive_vapi_call_event(request_for(body), BackgroundTasks(), signature(secret, body)))
    assert result == {"status": "accepted", "call_id": "vapi-call-1"}
    assert updates[0]["ExpressionAttributeValues"][":status"] == "ended"
    assert json.loads(messages[0]["MessageBody"]) == {"call_sid": "vapi-call-1", "tenant_id": "tenant-a"}


def test_signed_inbound_vapi_callback_creates_call_record(monkeypatch):
    secret = "v" * 32
    body = json.dumps({"event_id": "event-inbound-1", "call_id": "vapi-inbound-1", "tenant_id": "tenant-a", "direction": "inbound", "status": "ended", "caller_number": "+15550001111"}).encode()
    stored = []
    updates = []
    table = SimpleNamespace(get_item=lambda **kwargs: {}, put_item=lambda **kwargs: stored.append(kwargs), update_item=lambda **kwargs: updates.append(kwargs))
    monkeypatch.setenv("VAPI_WEBHOOK_SECRET", secret)
    monkeypatch.setattr(outbound_agent, "calls_table", lambda: table)
    monkeypatch.setattr(outbound_agent, "tenant_by_id", lambda tenant_id: {"tenant_id": tenant_id})
    monkeypatch.setattr(outbound_agent, "audit", lambda *args, **kwargs: None)

    result = asyncio.run(outbound_agent.receive_vapi_call_event(request_for(body), BackgroundTasks(), signature(secret, body)))
    assert result == {"status": "accepted", "call_id": "vapi-inbound-1"}
    assert stored[0]["Item"]["direction"] == "inbound"
    assert stored[0]["Item"]["provider"] == "vapi"
    assert updates[0]["ExpressionAttributeValues"][":status"] == "ended"


def test_generic_trigger_starts_vapi_call_with_shared_secret(monkeypatch):
    body = json.dumps({"provider": "generic", "tenant_id": "tenant-a", "phone_number": "+15550001111", "name": "Jane", "metadata": {"form": "website"}}).encode()
    stored = []
    monkeypatch.setenv("OUTBOUND_TRIGGER_SECRET", "o" * 32)
    monkeypatch.setenv("VAPI_API_KEY", "vapi-key")
    monkeypatch.setenv("VAPI_ASSISTANT_ID", "assistant-1")
    monkeypatch.setenv("VAPI_PHONE_NUMBER_ID", "number-1")
    monkeypatch.setattr(outbound_agent, "tenant_by_id", lambda tenant_id: {"tenant_id": tenant_id})
    monkeypatch.setattr(outbound_agent, "calls_table", lambda: SimpleNamespace(put_item=lambda **kwargs: stored.append(kwargs)))
    monkeypatch.setattr(outbound_agent, "audit", lambda *args, **kwargs: None)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"id": "vapi-generic-1"})

    monkeypatch.setattr(outbound_agent.httpx, "AsyncClient", lambda **kwargs: Client())
    result = asyncio.run(outbound_agent.start_outbound_call(request_for(body), x_outbound_trigger_secret="o" * 32))
    assert result == {"status": "started", "call_id": "vapi-generic-1"}
    assert stored[0]["Item"] == {"call_sid": "vapi-generic-1", "tenant_id": "tenant-a", "status": "outbound_started", "direction": "outbound", "provider": "vapi", "from_number": "", "to_number": "+15550001111", "ghl_contact_id": "", "campaign_id": "", "metadata": {"form": "website"}, "created_at": stored[0]["Item"]["created_at"]}
