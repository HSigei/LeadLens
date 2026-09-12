import json

import pytest

from tenant_policy import tenant_by_id, validate_registry


def policy(**overrides):
    tenant = {
        "tenant_id": "tenant-a",
        "escalation_number": "+15550001111",
        "jurisdiction": "US-CA",
        "processing_region": "us-east-1",
        "privacy_notice_version": "v1",
        "lawful_basis": "consent",
        "cross_border_safeguard": "contractual_safeguards",
        "dpia_approved": "true",
        "approved_by": "privacy officer",
        "approved_at": "2026-09-07",
        "data_protection_contact": "privacy@example.com",
    }
    tenant.update(overrides)
    return {"version": 1, "tenants": {"+15551234567": tenant}}


def test_valid_policy_registry_is_accepted():
    validate_registry(policy())


def test_high_risk_jurisdiction_requires_dpia():
    with pytest.raises(Exception):
        validate_registry(policy(jurisdiction="EU", dpia_approved="false"))


def test_policy_requires_documented_approval():
    invalid = policy()
    del invalid["tenants"]["+15551234567"]["approved_by"]
    with pytest.raises(ValueError, match="documented approvals"):
        validate_registry(invalid)


def test_analysis_fields_accepts_valid_and_rejects_invalid_entries():
    validate_registry(policy(analysis_fields=[{"name": "kyc_verified", "type": "boolean"}]))
    with pytest.raises(ValueError, match="analysis_fields"):
        validate_registry(policy(analysis_fields=[{"type": "boolean"}]))
    with pytest.raises(ValueError, match="analysis_fields"):
        validate_registry(policy(analysis_fields=[{"name": "x", "type": "number"}]))


def test_tenant_by_id_finds_and_misses(monkeypatch):
    monkeypatch.setenv("TENANT_ROUTING_JSON", json.dumps(policy()))
    monkeypatch.delenv("TENANT_POLICY_FILE", raising=False)
    assert tenant_by_id("tenant-a")["tenant_id"] == "tenant-a"
    assert tenant_by_id("unknown-tenant") is None