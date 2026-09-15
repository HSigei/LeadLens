import asyncio
import gc
import logging
import threading

from fastapi.testclient import TestClient

import app
import llm_server


def vapi_request(messages, call_id="call-1", assistant_id="assistant-1"):
    return {
        "model": "gpt-4",
        "messages": messages,
        "stream": True,
        "call": {"id": call_id},
        "assistant": {"id": assistant_id},
        "assistantOverrides": {"variableValues": {}},
        "metadata": {},
    }


def stream_client(lines, requests):
    class Response:
        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            for line in lines:
                yield line

    class Stream:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *args):
            return None

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, method, url, headers, json):
            requests.append(json)
            return Stream()

    return Client


def capture_scheduled_audits(monkeypatch):
    audits = []
    monkeypatch.setattr(llm_server, "schedule_audit", lambda *args, **kwargs: audits.append(args))
    return audits


def test_custom_llm_streams_groq_response_and_audits_call(monkeypatch):
    requests = []
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "test-secret")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setattr(llm_server, "tenant_by_vapi_assistant_id", lambda assistant_id: {"tenant_id": "tenant-a"})
    audits = capture_scheduled_audits(monkeypatch)
    monkeypatch.setattr(llm_server, "claim_call_start", lambda *args: True)
    monkeypatch.setattr(llm_server, "get_facts", lambda tenant_id: [])
    monkeypatch.setattr(llm_server, "retrieve_context", lambda tenant_id, message: "")
    monkeypatch.setattr(llm_server.httpx, "AsyncClient", lambda **kwargs: stream_client(['data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}', 'data: {"choices":[{"delta":{"content":" there"},"finish_reason":"stop"}]}', "data: [DONE]"], requests)())
    llm_server.STARTED_CALL_CACHE.clear()

    response = TestClient(app.app).post("/custom-llm/test-secret/chat/completions", json=vapi_request([{"role": "system", "content": "Vapi default"}, {"role": "user", "content": "I need help"}]))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"object":"chat.completion.chunk"' in response.text
    assert '"content":"Hello"' in response.text
    assert response.text.endswith("data: [DONE]\n\n")
    assert requests[0]["stream"] is True
    assert requests[0]["messages"][0]["role"] == "system"
    assert [message["role"] for message in requests[0]["messages"]].count("system") == 1
    assert [event[2] for event in audits] == ["call_started", "turn_processed"]


def test_custom_llm_rejects_unmatched_assistant_without_calling_groq(monkeypatch):
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "test-secret")
    monkeypatch.setattr(llm_server, "tenant_by_vapi_assistant_id", lambda assistant_id: None)
    monkeypatch.setattr(llm_server.httpx, "AsyncClient", lambda **kwargs: (_ for _ in ()).throw(AssertionError("Groq must not be called")))

    response = TestClient(app.app).post("/custom-llm/test-secret/chat/completions", json=vapi_request([]))

    assert response.status_code == 404
    assert response.json()["detail"] == "No tenant is configured for this Vapi assistant."


def test_custom_llm_reports_unreadable_tenant_policy_distinctly(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "test-secret")
    monkeypatch.setattr(
        llm_server,
        "tenant_by_vapi_assistant_id",
        lambda assistant_id: (_ for _ in ()).throw(HTTPException(503, "Tenant policy file could not be loaded: unreadable")),
    )
    monkeypatch.setattr(llm_server.httpx, "AsyncClient", lambda **kwargs: (_ for _ in ()).throw(AssertionError("Groq must not be called")))

    response = TestClient(app.app).post("/custom-llm/test-secret/chat/completions", json=vapi_request([]))

    assert response.status_code == 503
    assert response.json()["detail"] == "Tenant policy file could not be loaded: unreadable"


def test_custom_llm_escalation_streams_reply_and_audits(monkeypatch):
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "test-secret")
    monkeypatch.setattr(llm_server, "tenant_by_vapi_assistant_id", lambda assistant_id: {"tenant_id": "tenant-a"})
    audits = capture_scheduled_audits(monkeypatch)
    monkeypatch.setattr(llm_server, "claim_call_start", lambda *args: True)
    llm_server.STARTED_CALL_CACHE.clear()

    response = TestClient(app.app).post("/custom-llm/test-secret/chat/completions", json=vapi_request([{"role": "user", "content": "I need a human representative"}]))

    assert response.status_code == 200
    assert "I will connect you with a team member now." in response.text
    assert [event[2] for event in audits] == ["call_started", "turn_processed", "escalation_detected"]


def test_custom_llm_uses_each_same_call_request_independently(monkeypatch):
    requests = []
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "test-secret")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setattr(llm_server, "tenant_by_vapi_assistant_id", lambda assistant_id: {"tenant_id": "tenant-a"})
    capture_scheduled_audits(monkeypatch)
    monkeypatch.setattr(llm_server, "claim_call_start", lambda *args: False)
    monkeypatch.setattr(llm_server, "get_facts", lambda tenant_id: [])
    monkeypatch.setattr(llm_server, "retrieve_context", lambda tenant_id, message: "")
    monkeypatch.setattr(llm_server.httpx, "AsyncClient", lambda **kwargs: stream_client(['data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}', "data: [DONE]"], requests)())
    llm_server.STARTED_CALL_CACHE.clear()
    client = TestClient(app.app)

    first = client.post("/custom-llm/test-secret/chat/completions", json=vapi_request([{"role": "user", "content": "first"}]))
    second = client.post("/custom-llm/test-secret/chat/completions", json=vapi_request([{"role": "user", "content": "second"}], call_id="call-1"))

    assert first.status_code == second.status_code == 200
    assert [request["messages"][-1]["content"] for request in requests] == ["first", "second"]


def test_claim_call_start_uses_durable_conditional_audit_marker(monkeypatch):
    stored = []
    monkeypatch.setattr(llm_server, "audit_table", lambda: type("Table", (), {"put_item": lambda self, **kwargs: stored.append(kwargs)})())
    monkeypatch.setattr(llm_server, "utc_now", lambda: "now")
    monkeypatch.setattr(llm_server, "retention_expiry", lambda days: 123)

    assert llm_server.claim_call_start("tenant-a", "call-1", "assistant-1") is True
    assert stored[0]["Item"]["event_id"] == "custom_llm_started#call-1"
    assert stored[0]["Item"]["expires_at"] == 123
    assert stored[0]["ConditionExpression"] == "attribute_not_exists(event_id)"


def test_cached_call_skips_durable_claim(monkeypatch):
    requests = []
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "test-secret")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setattr(llm_server, "tenant_by_vapi_assistant_id", lambda assistant_id: {"tenant_id": "tenant-a"})
    monkeypatch.setattr(llm_server, "claim_call_start", lambda *args: (_ for _ in ()).throw(AssertionError("cached call must not claim again")))
    monkeypatch.setattr(llm_server, "get_facts", lambda tenant_id: [])
    monkeypatch.setattr(llm_server, "retrieve_context", lambda tenant_id, message: "")
    monkeypatch.setattr(llm_server.httpx, "AsyncClient", lambda **kwargs: stream_client(['data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}', "data: [DONE]"], requests)())
    capture_scheduled_audits(monkeypatch)
    llm_server.STARTED_CALL_CACHE.clear()
    llm_server.cache_started_call("call-1")

    response = TestClient(app.app).post("/custom-llm/test-secret/chat/completions", json=vapi_request([{"role": "user", "content": "again"}]))

    assert response.status_code == 200


def test_started_call_cache_evicts_oldest_entry(monkeypatch):
    monkeypatch.setattr(llm_server, "STARTED_CALL_CACHE_MAX_SIZE", 2)
    llm_server.STARTED_CALL_CACHE.clear()

    llm_server.cache_started_call("oldest")
    llm_server.cache_started_call("middle")
    llm_server.cache_started_call("newest")

    assert list(llm_server.STARTED_CALL_CACHE) == ["middle", "newest"]


def test_schedule_audit_returns_without_waiting_for_write(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow_audit(*args, **kwargs):
        started.set()
        release.wait(timeout=1)

    monkeypatch.setattr(llm_server, "audit", slow_audit)

    async def verify():
        task = llm_server.schedule_audit("tenant-a", "custom_llm", "turn_processed", "call-1")
        await asyncio.sleep(0)
        assert started.wait(timeout=0.1)
        assert not task.done()
        release.set()
        await task

    asyncio.run(verify())


def test_background_audit_task_survives_garbage_collection(monkeypatch):
    called = threading.Event()
    monkeypatch.setattr(llm_server, "audit", lambda *args, **kwargs: called.set())
    llm_server.BACKGROUND_TASKS.clear()

    async def verify():
        task = llm_server.schedule_audit("tenant-a", "custom_llm", "turn_processed", "call-1")
        assert task in llm_server.BACKGROUND_TASKS
        del task
        gc.collect()
        await asyncio.sleep(0.05)
        assert called.is_set()
        assert not llm_server.BACKGROUND_TASKS

    asyncio.run(verify())


def test_background_audit_failure_is_logged(monkeypatch):
    logged = []
    monkeypatch.setattr(llm_server, "audit", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("database unavailable")))
    monkeypatch.setattr(llm_server, "log_event", lambda *args, **kwargs: logged.append((args, kwargs)))
    llm_server.BACKGROUND_TASKS.clear()

    async def verify():
        llm_server.schedule_audit("tenant-a", "custom_llm", "turn_processed", "call-1")
        await asyncio.sleep(0.05)

    asyncio.run(verify())

    assert logged == [(("custom_llm.audit_failed", logging.ERROR), {"error_type": "RuntimeError", "error": "database unavailable"})]


def test_build_context_block_returns_empty_for_unconfigured_tenant(monkeypatch):
    monkeypatch.setattr(llm_server, "get_facts", lambda tenant_id: [])
    monkeypatch.setattr(llm_server, "retrieve_context", lambda tenant_id, message: "")

    assert llm_server.build_context_block("tenant-a", "What are your hours?") == ""


def test_build_context_block_prioritizes_structured_facts_first(monkeypatch):
    monkeypatch.setattr(llm_server, "get_facts", lambda tenant_id: [{"fact_type": "hours", "fact_key": "monday_hours", "fact_value": "9am-5pm"}])
    monkeypatch.setattr(llm_server, "retrieve_context", lambda tenant_id, message: "Unstructured policy text.")

    context = llm_server.build_context_block("tenant-a", "What are your hours?")

    assert context.startswith("hours.monday_hours: 9am-5pm")
    assert "Unstructured policy text." in context


def test_build_context_block_truncates_unstructured_when_combined_exceeds_limit(monkeypatch):
    structured_fact = {"fact_type": "hours", "fact_key": "monday_hours", "fact_value": "x" * 100}
    monkeypatch.setattr(llm_server, "get_facts", lambda tenant_id: [structured_fact])
    monkeypatch.setattr(llm_server, "retrieve_context", lambda tenant_id, message: "y" * 5000)

    context = llm_server.build_context_block("tenant-a", "question")

    assert len(context) <= llm_server.MAX_CONTEXT_BLOCK_CHARS
    assert context.startswith("hours.monday_hours:")


def test_build_context_block_uses_only_structured_when_no_unstructured_match(monkeypatch):
    monkeypatch.setattr(llm_server, "get_facts", lambda tenant_id: [{"fact_type": "hours", "fact_key": "monday_hours", "fact_value": "9am-5pm"}])
    monkeypatch.setattr(llm_server, "retrieve_context", lambda tenant_id, message: "")

    context = llm_server.build_context_block("tenant-a", "What are your hours?")

    assert context == "hours.monday_hours: 9am-5pm"
