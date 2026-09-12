import asyncio
import json
import os
from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.datastructures import Headers

os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("CALLS_TABLE", "calls")
os.environ.setdefault("CALL_DATA_BUCKET", "bucket")
os.environ.setdefault("CALL_DATA_KMS_KEY_ID", "kms")
os.environ.setdefault("REPORT_RECIPIENTS", "a@example.com")
os.environ.setdefault("REPORT_SENDER", "s@example.com")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import aggregator
import app
import core
import knowledge
import observability
import worker
from security import apply_security_headers
from tenant_policy import tenant_for_number, validate_registry


def test_core_configuration_and_validation_branches(monkeypatch):
    monkeypatch.delenv("MISSING_VALUE", raising=False)
    with pytest.raises(RuntimeError, match="MISSING_VALUE"):
        core.env("MISSING_VALUE")
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "short")
    with pytest.raises(RuntimeError, match="32 bytes"):
        core.jwt_signing_secret()
    assert core.retention_expiry(1) > 0
    with pytest.raises(ValueError, match="invalid analysis"):
        core.validate_analysis({})
    valid = {"keywords": [], "objections": [], "sentiment": "mixed", "sentiment_score": 50, "agent_performance_score": 50, "issue_resolved": False, "missed_opportunities": [], "training_recommendations": [], "revenue_opportunity": "medium", "customer_experience_notes": ""}
    with pytest.raises(ValueError, match="unsupported"):
        core.validate_analysis({**valid, "sentiment": "unknown"})
    assert core.validate_analysis(valid)["custom"] == {}
    custom_fields = [{"name": "kyc_verified", "type": "boolean"}]
    with pytest.raises(ValueError, match="custom field"):
        core.validate_analysis(valid, custom_fields)
    with_custom = core.validate_analysis({**valid, "custom": {"kyc_verified": True, "extra": "dropped"}}, custom_fields)
    assert with_custom["custom"] == {"kyc_verified": True}
    with pytest.raises(HTTPException, match="missing"):
        core.validate_tenant_compliance({})
    complete = {"tenant_id": "t", "escalation_number": "+1", "privacy_notice_version": "v1", "lawful_basis": "wrong", "jurisdiction": "US", "processing_region": "us", "cross_border_safeguard": "none"}
    with pytest.raises(HTTPException, match="lawful"):
        core.validate_tenant_compliance(complete)
    with pytest.raises(HTTPException, match="cross-border"):
        core.validate_tenant_compliance({**complete, "lawful_basis": "consent", "cross_border_safeguard": "wrong"})
    with pytest.raises(HTTPException, match="impact"):
        core.validate_tenant_compliance({**complete, "lawful_basis": "consent", "cross_border_safeguard": "none", "jurisdiction": "EU"})


def test_core_storage_audit_and_event_branches(monkeypatch):
    table = SimpleNamespace(put_item=lambda **kwargs: None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setattr(core, "PostgresTable", lambda name: table)
    assert core.calls_table() is table
    assert core.audit_table() is table
    assert core.organizations_table() is table
    core.audit("t", "actor", "action")
    assert core.event_key({"b": "2", "a": "1"}) == core.event_key({"a": "1", "b": "2"})

    failing = SimpleNamespace(put_item=lambda **kwargs: (_ for _ in ()).throw(core.ConditionalWriteError()))
    monkeypatch.setattr(core, "calls_table", lambda: failing)
    assert core.accept_event("event", "tenant") is False
    monkeypatch.setattr(core, "calls_table", lambda: table)
    assert core.accept_event("event", "tenant") is True


def test_core_token_branches(monkeypatch):
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-with-at-least-thirty-two-bytes")
    with pytest.raises(HTTPException, match="Invalid"):
        core.decode_access_token("bad")
    token = jwt.encode({"sub": "u", "tenant_id": "t", "role": "unknown", "aud": "callsignal-dashboard"}, os.environ["DASHBOARD_JWT_SECRET"], algorithm="HS256")
    with pytest.raises(HTTPException, match="required"):
        core.decode_access_token(token)


def test_app_security_rejects_large_requests(monkeypatch):
    response = TestClient(app.app).get("/healthz", headers={"content-length": "99999999"})
    assert response.status_code == 413


def test_security_headers_https_and_api():
    response = TestClient(app.app).get("/healthz")
    assert response.headers["x-frame-options"] == "DENY"
    response = TestClient(app.app).get("/api/metrics")
    assert response.headers["cache-control"] == "no-store"


def test_observability_branches(monkeypatch, caplog):
    assert observability.request_id_from_header("bad value") != "bad value"
    assert observability._safe_value({"token": "secret", "nested": ["jane@example.com"]})["token"] == "[REDACTED]"
    record = __import__("logging").LogRecord("x", 20, "", 1, "event", (), None)
    record.fields = {"message": "jane@example.com"}
    assert "REDACTED" in observability.JsonFormatter().format(record)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    observability.initialize_error_tracking()
    observability.capture_exception(RuntimeError("test"))


def test_knowledge_retrieval_and_upload(monkeypatch):
    class FakeCollection:
        def __init__(self):
            self.rows = []

        def upsert(self, ids, documents, metadatas):
            self.rows.extend(zip(ids, documents, metadatas))

        def count(self):
            return len(self.rows)

        def query(self, query_texts, n_results, where):
            matched = [document for _, document, metadata in self.rows if metadata.get("tenant_id") == where.get("tenant_id")]
            return {"documents": [matched[:n_results]]}

    collection = FakeCollection()
    collection.upsert(ids=["a:0", "a:1"], documents=["one", "two"], metadatas=[{"tenant_id": "tenant"}, {"tenant_id": "tenant"}])
    monkeypatch.setattr(knowledge, "_collection", lambda: collection)
    assert knowledge.retrieve_context("tenant", "question") == "one\n\ntwo"
    class Upload:
        filename = "../facts.txt"
        content_type = "text/plain"
        async def read(self, limit):
            return b"approved facts about the product"
    monkeypatch.delenv("CALL_DATA_BUCKET", raising=False)
    monkeypatch.setattr(knowledge, "audit", lambda *args, **kwargs: None)
    assert asyncio.run(knowledge.upload_document(Upload(), {"tenant_id": "t", "sub": "u", "role": "admin"}))["status"] == "indexed"


def test_policy_registry_shapes_and_number_not_found(monkeypatch):
    monkeypatch.delenv("TENANT_POLICY_FILE", raising=False)
    monkeypatch.setenv("TENANT_ROUTING_JSON", json.dumps({"+1": {}}))
    with pytest.raises(ValueError, match="version"):
        validate_registry({"version": 2, "tenants": {}})
    with pytest.raises(ValueError, match="E.164"):
        validate_registry({"version": 1, "tenants": {"123": {}}})
    monkeypatch.setenv("TENANT_ROUTING_JSON", json.dumps({"version": 1, "tenants": {"+1": {"tenant_id": "t"}}}))
    with pytest.raises(HTTPException, match="missing"):
        tenant_for_number("+1")


def test_aggregator_run_paginates_and_sends(monkeypatch):
    os.environ.update({"CALL_DATA_BUCKET": "bucket", "CALL_DATA_KMS_KEY_ID": "kms", "REPORT_RECIPIENTS": "a@example.com", "REPORT_SENDER": "s@example.com"})
    class Table:
        count = 0
        def scan(self, **kwargs):
            self.count += 1
            if self.count == 1:
                return {"Items": [{"call_sid": "1"}], "LastEvaluatedKey": {"x": 1}}
            return {"Items": [{"call_sid": "2"}]}
    storage = SimpleNamespace(put_object=lambda **kwargs: None, generate_presigned_url=lambda *args, **kwargs: "url")
    ses = SimpleNamespace(send_email=lambda **kwargs: None)
    table = Table()
    monkeypatch.setattr(aggregator, "calls_table", lambda: table)
    monkeypatch.setattr(aggregator.boto3, "client", lambda service, **kwargs: ses if service == "sesv2" else storage)
    aggregator.run()


def test_worker_optional_crm_and_queue_error(monkeypatch):
    monkeypatch.delenv("CRM_WEBHOOK_URL", raising=False)
    worker.sync_crm({"tenant_id": "t", "call_sid": "c"}, {})
    calls = []
    class Queue:
        def receive_message(self, **kwargs):
            calls.append(1)
            raise RuntimeError("queue")
    monkeypatch.setattr(worker, "sqs", Queue())
    monkeypatch.setattr(worker, "env", lambda name: "queue")
    monkeypatch.setattr(worker.time, "sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        worker.run()