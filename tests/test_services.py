import io
import json
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from openpyxl import load_workbook

import aggregator
import billing
import dashboard
import integrations
import knowledge
import saas
from app import app
from tenant_policy import policy_registry, read_policy_file, tenant_for_number


USER = {"tenant_id": "tenant-a", "sub": "user-1", "role": "admin"}


class FakeTable:
    def __init__(self, item=None, items=None):
        self.item = item
        self.items = items or []
        self.calls = []

    def get_item(self, **kwargs):
        self.calls.append(("get_item", kwargs))
        return {"Item": self.item} if self.item is not None else {}

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        return {"Items": self.items, "LastEvaluatedKey": {"call_sid": "next"}} if self.items else {"Items": []}

    def update_item(self, **kwargs):
        self.calls.append(("update_item", kwargs))

    def delete_item(self, **kwargs):
        self.calls.append(("delete_item", kwargs))


class FakeStorage:
    def __init__(self):
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(("put_object", kwargs))

    def delete_object(self, **kwargs):
        self.calls.append(("delete_object", kwargs))

    def generate_presigned_url(self, *args, **kwargs):
        return "https://example.test/report"


def test_master_workbook_contains_headers_and_decimal_values():
    content = aggregator.master_workbook([{"call_sid": "CA1", "status": "completed", "updated_at": "today", "analysis": {"sentiment": "positive", "agent_performance_score": 95, "issue_resolved": True, "revenue_opportunity": "high", "keywords": ["renewal"], "objections": [], "missed_opportunities": [], "training_recommendations": []}}])
    workbook = load_workbook(io.BytesIO(content))
    assert workbook.active.title == "Call Intelligence"
    assert aggregator.excel_value(aggregator.Decimal("2.5")) == 2.5


def test_tenant_policy_file_and_registry(monkeypatch, tmp_path):
    path = tmp_path / "policy.json"
    path.write_text('{"version": 1, "tenants": {}}', encoding="utf-8")
    assert read_policy_file(str(path))["version"] == 1
    monkeypatch.setenv("TENANT_POLICY_FILE", str(path))
    assert policy_registry()["tenants"] == {}
    with pytest.raises(HTTPException, match="Tenant policy is invalid"):
        tenant_for_number("+15550000000")


def test_tenant_policy_reports_invalid_file(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="Cannot read"):
        read_policy_file(str(path))


def test_dashboard_routes_cover_metrics_report_and_erasure(monkeypatch):
    calls = FakeTable(items=[{"status": "completed", "analysis": {"issue_resolved": True, "agent_performance_score": 80}}])
    monkeypatch.setattr(dashboard, "calls_table", lambda: calls)
    monkeypatch.setattr(dashboard, "audit", lambda *args, **kwargs: None)
    assert dashboard.list_calls(USER, limit=500)["items"]
    assert dashboard.metrics(USER) == {"calls_processed": 1, "resolution_rate": 100.0, "average_performance": 80.0}

    calls.item = {"tenant_id": "tenant-a", "report_key": "reports/CA1.xlsx", "audio_key": "a", "transcript_key": "t"}
    storage = FakeStorage()
    monkeypatch.setattr(dashboard.boto3, "client", lambda *args, **kwargs: storage)
    monkeypatch.setattr(dashboard, "env", lambda name: "value")
    assert dashboard.report_url("CA1", USER)["expires_in_seconds"] == "900"
    assert dashboard.erase_call("CA1", USER, "true") == {"status": "erased"}
    assert len([call for call in storage.calls if call[0] == "delete_object"]) == 3


def test_dashboard_rejects_non_admin_and_legal_hold(monkeypatch):
    with pytest.raises(HTTPException, match="administrator"):
        dashboard.erase_call("CA1", {**USER, "role": "supervisor"}, "true")
    calls = FakeTable(item={"tenant_id": "tenant-a", "legal_hold": True})
    monkeypatch.setattr(dashboard, "calls_table", lambda: calls)
    with pytest.raises(HTTPException, match="legal hold"):
        dashboard.erase_call("CA1", USER, "true")


def test_billing_checkout_and_webhook(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_test")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setattr(billing, "audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(billing.stripe.checkout.Session, "create", lambda **kwargs: SimpleNamespace(url="https://checkout.test"))
    assert billing.checkout(USER)["checkout_url"] == "https://checkout.test"

    event = {"type": "checkout.session.completed", "data": {"object": {"client_reference_id": "tenant-a", "status": "active"}}}
    monkeypatch.setattr(billing.stripe.Webhook, "construct_event", lambda *args: event)
    organizations = FakeTable()
    monkeypatch.setattr(billing, "organizations_table", lambda: organizations)
    monkeypatch.setattr(billing, "audit", lambda *args, **kwargs: None)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    response = TestClient(app).post("/api/billing/webhook", content=b"{}", headers={"Stripe-Signature": "sig"})
    assert response.status_code == 200
    assert organizations.calls


def test_billing_rejects_unconfigured_and_invalid_webhook(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_ID", raising=False)
    with pytest.raises(HTTPException, match="not configured"):
        billing.checkout(USER)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "secret")
    monkeypatch.setattr(billing.stripe.Webhook, "construct_event", lambda *args: (_ for _ in ()).throw(ValueError()))
    response = TestClient(app).post("/api/billing/webhook", content=b"bad", headers={"Stripe-Signature": "bad"})
    assert response.status_code == 400


def test_knowledge_upload_and_retrieval(monkeypatch):
    storage = FakeStorage()
    bedrock = SimpleNamespace(start_ingestion_job=lambda **kwargs: {"jobId": "job"})
    monkeypatch.setattr(knowledge.boto3, "client", lambda service, **kwargs: bedrock if service == "bedrock-agent" else storage)
    monkeypatch.setattr(knowledge, "env", lambda name: {"AWS_REGION": "us-east-1", "CALL_DATA_BUCKET": "bucket", "CALL_DATA_KMS_KEY_ID": "key", "KNOWLEDGE_BASE_ID": "kb", "KNOWLEDGE_DATA_SOURCE_ID": "ds"}[name])
    monkeypatch.setattr(knowledge, "audit", lambda *args, **kwargs: None)
    assert knowledge.safe_filename("../../hello world.txt") == "hello_world.txt"
    assert knowledge.retrieve_context("tenant-a", "price") == ""
    response = TestClient(app).post("/api/knowledge/documents", files={"document": ("../../facts.txt", b"approved", "text/plain")})
    assert response.status_code in {401, 403}


def test_oauth_authorize_branches(monkeypatch):
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-with-at-least-thirty-two-bytes")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setattr(integrations, "oauth_setting", lambda provider, name: "client")
    for provider in ("hubspot", "salesforce", "zoho"):
        result = integrations.authorize(provider, USER)
        assert result["authorization_url"].startswith("https://")
    with pytest.raises(HTTPException, match="Unsupported"):
        integrations.authorize("unknown", USER)


def test_onboarding_routes(monkeypatch):
    organizations = FakeTable(item={"name": "Acme", "privacy_contact": "privacy@example.com", "privacy_approved_at": "today", "connections": {"twilio": {}, "hubspot": {}, "stripe": {}}})
    monkeypatch.setattr(saas, "organizations_table", lambda: organizations)
    monkeypatch.setattr(saas, "audit", lambda *args, **kwargs: None)
    assert saas.status(USER)["ready"] is True
    profile = saas.OrganizationProfile(name="Acme", jurisdiction="US-CA", privacy_contact="privacy@example.com")
    assert saas.save_organization(profile, USER)["name"] == "Acme"
    assert saas.approve_privacy(USER)["status"] == "recorded"
    request = saas.ConnectionRequest(provider="twilio", connection_id="conn-1")
    assert saas.register_connection(request, USER)["provider"] == "twilio"