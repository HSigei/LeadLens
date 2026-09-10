import io
import json
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from openpyxl import load_workbook

import aggregator
import dashboard
import knowledge
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


def test_dashboard_audit_records_erasure_context(monkeypatch):
    calls = FakeTable(item={"tenant_id": "tenant-a", "audio_key": "a", "transcript_key": "t", "report_key": "r"})
    monkeypatch.setattr(dashboard, "calls_table", lambda: calls)
    captured = []
    monkeypatch.setattr(dashboard, "audit", lambda tenant_id, actor, action, call_sid=None, detail=None: captured.append({"tenant_id": tenant_id, "actor": actor, "action": action, "call_sid": call_sid, "detail": detail}))
    storage = FakeStorage()
    monkeypatch.setattr(dashboard.boto3, "client", lambda *args, **kwargs: storage)
    monkeypatch.setattr(dashboard, "env", lambda name: "value")

    assert dashboard.erase_call("CA1", USER, "true") == {"status": "erased"}
    assert captured[-1]["detail"]["erasure_type"] == "data_subject"
    assert captured[-1]["detail"]["retention_days"] == 365
    assert captured[-1]["detail"]["keys_deleted"] == ["a", "t", "r"]


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