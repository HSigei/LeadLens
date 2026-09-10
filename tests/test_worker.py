import json
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("CALLS_TABLE", "calls")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("CALL_DATA_BUCKET", "bucket")
os.environ.setdefault("CALL_DATA_KMS_KEY_ID", "kms")
os.environ.setdefault("REPORT_RECIPIENTS", "recipient@example.com")
os.environ.setdefault("REPORT_SENDER", "sender@example.com")
os.environ.setdefault("TWILIO_ACCOUNT_SID", "AC123")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "token")

import worker


class Response:
    def __init__(self, payload=None, content=b"audio"):
        self.payload = payload or {}
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_worker_openai_and_analysis_adapters(monkeypatch):
    assert worker.openai_headers()["Authorization"] == "Bearer test-key"
    monkeypatch.setattr(worker.httpx, "post", lambda *args, **kwargs: Response({"text": " hello "}))
    assert worker.transcribe(b"audio", "call.mp3") == "hello"
    analysis = {"keywords": [], "objections": [], "sentiment": "positive", "sentiment_score": 90, "agent_performance_score": 80, "issue_resolved": True, "missed_opportunities": [], "training_recommendations": [], "revenue_opportunity": "high", "customer_experience_notes": "Good"}
    monkeypatch.setattr(worker.httpx, "post", lambda *args, **kwargs: Response({"choices": [{"message": {"content": json.dumps(analysis)}}]}))
    assert worker.analyze("Transcript")["sentiment"] == "positive"


def test_worker_storage_crm_and_workbook(monkeypatch):
    storage = SimpleNamespace(put_object=lambda **kwargs: None)
    monkeypatch.setattr(worker, "s3", storage)
    monkeypatch.setattr(worker, "env", lambda name: {"CALL_DATA_BUCKET": "bucket", "CALL_DATA_KMS_KEY_ID": "kms", "CRM_WEBHOOK_URL": "https://crm.test", "CRM_WEBHOOK_TOKEN": "crm-token"}.get(name, os.environ.get(name, "value")))
    worker.put_encrypted("key", b"data", "text/plain")
    assert len(worker.workbook_bytes({"call_sid": "CA1"}, {"sentiment": "mixed", "agent_performance_score": 50, "issue_resolved": False, "revenue_opportunity": "low", "keywords": [], "objections": [], "missed_opportunities": [], "training_recommendations": [], "customer_experience_notes": ""})) > 100
    monkeypatch.setattr(worker.httpx, "post", lambda *args, **kwargs: Response())
    worker.sync_crm({"tenant_id": "tenant-a", "call_sid": "CA1"}, {"issue_resolved": True, "revenue_opportunity": "high", "sentiment": "positive", "agent_performance_score": 90})


def test_worker_process_completes_call(monkeypatch):
    call = {"call_sid": "CA1", "tenant_id": "tenant-a", "recording_url": "https://recording", "status": "queued"}
    updates = []
    table = SimpleNamespace(get_item=lambda **kwargs: {"Item": call}, update_item=lambda **kwargs: updates.append(kwargs))
    monkeypatch.setattr(worker, "calls_table", lambda: table)
    monkeypatch.setattr(worker.httpx, "get", lambda *args, **kwargs: Response(content=b"audio"))
    monkeypatch.setattr(worker, "transcribe", lambda *args: "caller 0712345678")
    analysis = {"keywords": [], "objections": [], "sentiment": "mixed", "sentiment_score": 50, "agent_performance_score": 70, "issue_resolved": False, "missed_opportunities": [], "training_recommendations": [], "revenue_opportunity": "medium", "customer_experience_notes": "Neutral"}
    monkeypatch.setattr(worker, "analyze", lambda text: analysis)
    monkeypatch.setattr(worker, "put_encrypted", lambda *args: None)
    monkeypatch.setattr(worker, "sync_crm", lambda *args: None)
    monkeypatch.setattr(worker, "audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "env", lambda name: {"TWILIO_ACCOUNT_SID": "AC", "TWILIO_AUTH_TOKEN": "token", "CALL_DATA_BUCKET": "bucket", "REPORT_RECIPIENTS": "recipient@example.com", "REPORT_SENDER": "sender@example.com"}.get(name, "value"))
    monkeypatch.setattr(worker.s3, "generate_presigned_url", lambda *args, **kwargs: "https://report")
    monkeypatch.setattr(worker.ses, "send_email", lambda **kwargs: None)
    worker.process("CA1")
    assert updates


def test_worker_process_skips_completed(monkeypatch):
    monkeypatch.setattr(worker, "calls_table", lambda: SimpleNamespace(get_item=lambda **kwargs: {"Item": {"status": "completed"}}))
    worker.process("CA2")