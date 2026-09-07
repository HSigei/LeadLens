from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import jwt
from fastapi import HTTPException


PII_PATTERNS = (
    (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "[EMAIL_REDACTED]"),
    (re.compile(r"\b(?:\+254|254|0)7\d{8}\b"), "[PHONE_REDACTED]"),
    (re.compile(r"\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}\b"), "[PHONE_REDACTED]"),
    (re.compile(r"\b\d{7,8}\b"), "[NATIONAL_ID_REDACTED]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN_REDACTED]"),
)
ROLES = {"supervisor", "manager", "admin"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} must be configured")
    return value


def jwt_signing_secret() -> str:
    secret = env("DASHBOARD_JWT_SECRET")
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError("DASHBOARD_JWT_SECRET must be at least 32 bytes.")
    return secret


def redact_pii(text: str) -> str:
    for pattern, replacement in PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def retention_expiry(days: int | None = None) -> int:
    return int((datetime.now(UTC) + timedelta(days=days or int(os.getenv("RETENTION_DAYS", "365")))).timestamp())


def calls_table():
    return boto3.resource("dynamodb", region_name=env("AWS_REGION")).Table(env("CALLS_TABLE"))


def audit_table():
    return boto3.resource("dynamodb", region_name=env("AWS_REGION")).Table(env("AUDIT_TABLE"))


def organizations_table():
    return boto3.resource("dynamodb", region_name=env("AWS_REGION")).Table(env("ORGANIZATIONS_TABLE"))


def audit(tenant_id: str, actor: str, action: str, call_sid: str | None = None, detail: dict[str, Any] | None = None) -> None:
    audit_table().put_item(Item={"event_id": str(uuid.uuid4()), "tenant_id": tenant_id, "created_at": utc_now(), "actor": actor, "action": action, "call_sid": call_sid or "", "detail": detail or {}, "expires_at": retention_expiry(int(os.getenv("AUDIT_RETENTION_DAYS", "2555")))})


def event_key(payload: dict[str, str]) -> str:
    stable = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable.encode()).hexdigest()


def accept_event(event_id: str, tenant_id: str) -> bool:
    try:
        calls_table().put_item(Item={"call_sid": f"event#{event_id}", "tenant_id": tenant_id, "event_id": event_id, "status": "received", "expires_at": retention_expiry(7)}, ConditionExpression="attribute_not_exists(call_sid)")
        return True
    except calls_table().meta.client.exceptions.ConditionalCheckFailedException:
        return False


def decode_access_token(token: str) -> dict[str, str]:
    try:
        issuer = os.getenv("OIDC_ISSUER")
        if issuer:
            jwks = jwt.PyJWKClient(f"{issuer.rstrip('/')}/.well-known/jwks.json")
            key = jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=env("OIDC_AUDIENCE"), issuer=issuer)
        else:
            claims = jwt.decode(token, jwt_signing_secret(), algorithms=["HS256"], audience="callsignal-dashboard")
    except jwt.PyJWTError as error:
        raise HTTPException(401, "Invalid or expired access token.") from error
    namespace = os.getenv("OIDC_CLAIMS_NAMESPACE", "")
    role = claims.get(f"{namespace}role", claims.get("role"))
    tenant_id = claims.get(f"{namespace}tenant_id", claims.get("tenant_id"))
    if role not in ROLES or not tenant_id or not claims.get("sub"):
        raise HTTPException(403, "Token is missing required access claims.")
    return {"tenant_id": tenant_id, "role": role, "sub": claims["sub"]}


def validate_analysis(value: Any) -> dict[str, Any]:
    required = {"keywords": list, "objections": list, "sentiment": str, "sentiment_score": int, "agent_performance_score": int, "issue_resolved": bool, "missed_opportunities": list, "training_recommendations": list, "revenue_opportunity": str, "customer_experience_notes": str}
    if not isinstance(value, dict) or any(not isinstance(value.get(key), kind) for key, kind in required.items()):
        raise ValueError("Model returned an invalid analysis schema.")
    if value["sentiment"] not in {"positive", "mixed", "negative"} or value["revenue_opportunity"] not in {"low", "medium", "high"}:
        raise ValueError("Model returned unsupported analysis values.")
    for score in (value["sentiment_score"], value["agent_performance_score"]):
        if not 0 <= score <= 100:
            raise ValueError("Model returned an out-of-range score.")
    return value


def validate_tenant_compliance(tenant: dict[str, str]) -> None:
    required = ("tenant_id", "escalation_number", "privacy_notice_version", "lawful_basis", "jurisdiction", "processing_region", "cross_border_safeguard")
    missing = [field for field in required if not tenant.get(field)]
    if missing:
        raise HTTPException(503, f"Tenant is not approved for live calls: missing {', '.join(missing)}.")
    if tenant["lawful_basis"] not in {"consent", "contract", "legal_obligation", "legitimate_interest"}:
        raise HTTPException(503, "Tenant has an unsupported lawful basis.")
    if tenant["cross_border_safeguard"] not in {"none", "consent", "adequacy", "contractual_safeguards", "binding_corporate_rules"}:
        raise HTTPException(503, "Tenant is not approved for cross-border AI processing.")
    high_risk_jurisdictions = {"KE", "EU", "UK", "BR", "CA-QC"}
    if tenant["jurisdiction"] in high_risk_jurisdictions and tenant.get("dpia_approved") != "true":
        raise HTTPException(503, "Tenant requires an approved impact assessment before live calls.")


def requires_recording_consent(tenant: dict[str, str]) -> bool:
    return tenant.get("recording_consent_required", "true").lower() == "true"