import os

import jwt
import pytest

from core import decode_access_token, redact_pii, validate_analysis


def analysis():
    return {"keywords": [], "objections": [], "sentiment": "mixed", "sentiment_score": 50, "agent_performance_score": 65, "issue_resolved": False, "missed_opportunities": [], "training_recommendations": [], "revenue_opportunity": "medium", "customer_experience_notes": "Neutral."}


def test_redact_pii_masks_common_contact_data():
    redacted = redact_pii("Reach jane@example.com or 212-555-0199.")
    assert "jane@example.com" not in redacted
    assert "212-555-0199" not in redacted


def test_analysis_schema_rejects_invalid_score():
    value = analysis()
    value["sentiment_score"] = 101
    with pytest.raises(ValueError):
        validate_analysis(value)


def test_jwt_requires_tenant_role_and_subject(monkeypatch):
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-secret-with-at-least-thirty-two-bytes")
    token = jwt.encode({"sub": "supervisor-1", "tenant_id": "tenant-a", "role": "supervisor", "aud": "callsignal-dashboard"}, os.environ["DASHBOARD_JWT_SECRET"], algorithm="HS256")
    assert decode_access_token(token)["tenant_id"] == "tenant-a"


def test_jwt_rejects_short_hmac_secret(monkeypatch):
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "too-short")
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        decode_access_token("invalid")