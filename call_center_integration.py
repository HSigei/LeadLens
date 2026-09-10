from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Annotated, Literal

import boto3
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from core import ConditionalWriteError, audit, calls_table, organizations_table, utc_now
from dashboard import principal

router = APIRouter(prefix="/api/call-center", tags=["call-center integration"])

IntegrationMode = Literal["post_call_webhook", "twilio_routing", "sip_media_stream"]


class IntegrationProfile(BaseModel):
    mode: IntegrationMode
    provider: str = Field(min_length=2, max_length=80)
    base_url: str = Field(min_length=8, max_length=2048)
    call_id_field: str = Field(default="call_id", pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    caller_number_field: str = Field(default="caller_number", pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    recording_url_field: str | None = Field(default="recording_url", pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    event_types: list[str] = Field(default_factory=lambda: ["call.completed"], min_length=1, max_length=20)
    expected_signature_header: str = Field(default="X-Call-Center-Signature", pattern=r"^[A-Za-z][A-Za-z0-9-]{0,63}$")


class CallCenterEvent(BaseModel):
    event_id: str = Field(min_length=8, max_length=200)
    event_type: Literal["call.completed", "call.recording.ready"]
    call_id: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=120)
    caller_number: str | None = Field(default=None, max_length=40)
    recording_url: str | None = Field(default=None, max_length=2048)
    started_at: str | None = None
    ended_at: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict, max_length=30)


def admin(user: dict[str, str] = Depends(principal)) -> dict[str, str]:
    if user["role"] != "admin":
        raise HTTPException(403, "Organization administrator access is required.")
    return user


def integration_secret() -> str:
    secret = os.getenv("CALL_CENTER_WEBHOOK_SECRET")
    if not secret or len(secret) < 32:
        raise HTTPException(503, "Call-center webhook secret is not configured.")
    return secret


def verify_signature(body: bytes, signature: str | None) -> None:
    if not signature:
        raise HTTPException(401, "Call-center webhook signature required.")
    expected = hmac.new(integration_secret().encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.removeprefix("sha256="), expected):
        raise HTTPException(403, "Invalid call-center webhook signature.")


def preflight(profile: IntegrationProfile) -> dict[str, object]:
    checks = {
        "https_endpoint": profile.base_url.startswith("https://"),
        "signed_events": True,
        "stable_call_id": bool(profile.call_id_field),
        "recording_available": profile.mode != "post_call_webhook" or bool(profile.recording_url_field),
        "consent_contract_required": True,
        "tenant_mapping_required": True,
        "replay_protection_required": True,
        "live_audio_adapter_configured": profile.mode != "sip_media_stream",
    }
    blockers = [name for name, passed in checks.items() if not passed]
    return {"ready": not blockers, "checks": checks, "blockers": blockers}


@router.post("/preflight")
def save_preflight(profile: IntegrationProfile, user: dict[str, str] = Depends(admin)) -> dict[str, object]:
    result = preflight(profile)
    organizations_table().update_item(
        Key={"tenant_id": user["tenant_id"]},
        UpdateExpression="SET call_center_integration=:integration, updated_at=:updated",
        ExpressionAttributeValues={":integration": {**profile.model_dump(), "preflight": result}, ":updated": utc_now()},
    )
    audit(user["tenant_id"], user["sub"], "call_center_integration_preflighted", detail={"provider": profile.provider, "mode": profile.mode, "ready": result["ready"], "blockers": result["blockers"]})
    return {"tenant_id": user["tenant_id"], "profile": profile.model_dump(), **result}


@router.post("/events")
async def receive_event(
    request: Request,
    x_call_center_signature: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    body = await request.body()
    verify_signature(body, x_call_center_signature)
    try:
        event = CallCenterEvent.model_validate_json(body)
    except ValueError as error:
        raise HTTPException(400, "Invalid call-center event payload.") from error
    if event.event_type == "call.recording.ready" and not event.recording_url:
        raise HTTPException(422, "recording_url is required for recording-ready events.")
    if not organizations_table().get_item(Key={"tenant_id": event.tenant_id}).get("Item"):
        raise HTTPException(404, "Unknown tenant.")
    try:
        calls_table().put_item(
            Item={
                "call_sid": event.call_id,
                "tenant_id": event.tenant_id,
                "external_event_id": event.event_id,
                "status": "queued" if event.recording_url else "received",
                "from_number": event.caller_number or "",
                "recording_url": event.recording_url or "",
                "started_at": event.started_at or "",
                "ended_at": event.ended_at or "",
                "metadata": event.metadata,
                "created_at": utc_now(),
            },
            ConditionExpression="attribute_not_exists(call_sid)",
        )
    except ConditionalWriteError:
        return {"status": "duplicate", "call_id": event.call_id}
    except Exception as error:
        conditional_error = getattr(calls_table().meta.client.exceptions, "ConditionalCheckFailedException", None)
        if conditional_error and isinstance(error, conditional_error):
            return {"status": "duplicate", "call_id": event.call_id}
        raise
    if event.recording_url:
        boto3.client("sqs", region_name=os.getenv("AWS_REGION", "us-east-1")).send_message(
            QueueUrl=os.environ["PROCESSING_QUEUE_URL"],
            MessageBody=json.dumps({"call_sid": event.call_id, "tenant_id": event.tenant_id}),
            MessageDeduplicationId=event.event_id,
            MessageGroupId=event.tenant_id,
        )
    audit(event.tenant_id, "call_center", "call_center_event_received", event.call_id, {"event_id": event.event_id, "event_type": event.event_type})
    return {"status": "accepted", "call_id": event.call_id}
