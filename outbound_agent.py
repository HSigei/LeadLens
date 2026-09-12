from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Annotated, Any, Literal

import boto3
import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel, Field

from core import ConditionalWriteError, audit, calls_table, utc_now
from tenant_policy import tenant_by_id

router = APIRouter(tags=["outbound-agent"])


class GhlOutboundTrigger(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=120)
    contact_id: str = Field(default="", max_length=200)
    phone_number: str = Field(pattern=r"^\+[1-9]\d{6,14}$")
    name: str | None = Field(default=None, max_length=200)
    campaign_id: str | None = Field(default=None, max_length=200)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=30)


class GenericOutboundTrigger(BaseModel):
    provider: Literal["ghl", "generic"]
    tenant_id: str = Field(min_length=1, max_length=120)
    phone_number: str = Field(pattern=r"^\+[1-9]\d{6,14}$")
    name: str | None = Field(default=None, max_length=200)
    campaign_id: str | None = Field(default=None, max_length=200)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=30)


class VapiCallEvent(BaseModel):
    event_id: str = Field(min_length=1, max_length=200)
    call_id: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=120)
    direction: Literal["inbound", "outbound"] = "inbound"
    status: str = Field(min_length=1, max_length=80)
    caller_number: str | None = Field(default=None, max_length=40)
    recording_url: str | None = Field(default=None, max_length=2048)
    transcript: str | None = Field(default=None, max_length=200000)
    ended_at: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict, max_length=30)


def required_secret(name: str) -> str:
    secret = os.getenv(name)
    if not secret or len(secret) < 32:
        raise HTTPException(503, f"{name} must be configured with at least 32 characters.")
    return secret


def verify_hmac(body: bytes, signature: str | None, secret_name: str, provider: str) -> None:
    if not signature:
        raise HTTPException(401, f"{provider} webhook signature required.")
    expected = hmac.new(required_secret(secret_name).encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.removeprefix("sha256="), expected):
        raise HTTPException(403, f"Invalid {provider} webhook signature.")


def verify_shared_secret(value: str | None) -> None:
    if not value or not hmac.compare_digest(value, required_secret("OUTBOUND_TRIGGER_SECRET")):
        raise HTTPException(403, "Invalid generic outbound trigger secret.")


def vapi_headers() -> dict[str, str]:
    key = os.getenv("VAPI_API_KEY")
    if not key:
        raise HTTPException(503, "VAPI_API_KEY is not configured.")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


async def start_vapi_call(trigger: GhlOutboundTrigger, actor: str) -> dict[str, str]:
    if not tenant_by_id(trigger.tenant_id):
        raise HTTPException(404, "Unknown tenant.")
    assistant_id = os.getenv("VAPI_ASSISTANT_ID")
    phone_number_id = os.getenv("VAPI_PHONE_NUMBER_ID")
    if not assistant_id or not phone_number_id:
        raise HTTPException(503, "VAPI_ASSISTANT_ID and VAPI_PHONE_NUMBER_ID must be configured.")
    payload: dict[str, Any] = {
        "assistantId": assistant_id,
        "phoneNumberId": phone_number_id,
        "customer": {"number": trigger.phone_number},
        "metadata": {"tenant_id": trigger.tenant_id, "ghl_contact_id": trigger.contact_id, "campaign_id": trigger.campaign_id or "", **trigger.metadata},
    }
    if trigger.name:
        payload["customer"]["name"] = trigger.name
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post("https://api.vapi.ai/call", headers=vapi_headers(), json=payload)
    response.raise_for_status()
    vapi_call_id = response.json().get("id")
    if not isinstance(vapi_call_id, str) or not vapi_call_id:
        raise HTTPException(502, "Vapi did not return a call ID.")
    try:
        calls_table().put_item(
            Item={
                "call_sid": vapi_call_id,
                "tenant_id": trigger.tenant_id,
                "status": "outbound_started",
                "direction": "outbound",
                "provider": "vapi",
                "from_number": "",
                "to_number": trigger.phone_number,
                "ghl_contact_id": trigger.contact_id,
                "campaign_id": trigger.campaign_id or "",
                "metadata": trigger.metadata,
                "created_at": utc_now(),
            },
            ConditionExpression="attribute_not_exists(call_sid)",
        )
    except ConditionalWriteError:
        pass
    audit(trigger.tenant_id, actor, "outbound_call_started", vapi_call_id, {"contact_id": trigger.contact_id, "campaign_id": trigger.campaign_id or ""})
    return {"status": "started", "call_id": vapi_call_id}


@router.post("/webhooks/ghl/outbound")
async def start_ghl_outbound_call(
    request: Request,
    x_ghl_signature: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    body = await request.body()
    verify_hmac(body, x_ghl_signature, "GHL_OUTBOUND_WEBHOOK_SECRET", "GHL")
    try:
        trigger = GhlOutboundTrigger.model_validate_json(body)
    except ValueError as error:
        raise HTTPException(400, "Invalid GHL outbound trigger payload.") from error
    if not trigger.contact_id:
        raise HTTPException(400, "GHL outbound trigger requires contact_id.")
    return await start_vapi_call(trigger, "ghl")


@router.post("/webhooks/outbound/trigger")
async def start_outbound_call(
    request: Request,
    x_ghl_signature: Annotated[str | None, Header()] = None,
    x_outbound_trigger_secret: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    body = await request.body()
    try:
        generic_trigger = GenericOutboundTrigger.model_validate_json(body)
    except ValueError as error:
        raise HTTPException(400, "Invalid outbound trigger payload.") from error
    if generic_trigger.provider == "ghl":
        verify_hmac(body, x_ghl_signature, "GHL_OUTBOUND_WEBHOOK_SECRET", "GHL")
        try:
            trigger = GhlOutboundTrigger.model_validate_json(body)
        except ValueError as error:
            raise HTTPException(400, "GHL outbound trigger requires contact_id.") from error
        if not trigger.contact_id:
            raise HTTPException(400, "GHL outbound trigger requires contact_id.")
        return await start_vapi_call(trigger, "ghl")
    verify_shared_secret(x_outbound_trigger_secret)
    trigger = GhlOutboundTrigger(contact_id="", **generic_trigger.model_dump(exclude={"provider"}))
    return await start_vapi_call(trigger, "generic")


@router.post("/webhooks/vapi/call")
async def receive_vapi_call_event(
    request: Request,
    background_tasks: BackgroundTasks,
    x_vapi_signature: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    body = await request.body()
    verify_hmac(body, x_vapi_signature, "VAPI_WEBHOOK_SECRET", "Vapi")
    try:
        event = VapiCallEvent.model_validate_json(body)
    except ValueError as error:
        raise HTTPException(400, "Invalid Vapi call event payload.") from error
    call = calls_table().get_item(Key={"call_sid": event.call_id}).get("Item")
    if call and (call.get("tenant_id") != event.tenant_id or call.get("direction") != event.direction):
        raise HTTPException(404, "Unknown Vapi call.")
    if not call:
        if not tenant_by_id(event.tenant_id):
            raise HTTPException(404, "Unknown tenant.")
        try:
            calls_table().put_item(Item={"call_sid": event.call_id, "tenant_id": event.tenant_id, "status": event.status, "direction": event.direction, "provider": "vapi", "from_number": event.caller_number or "", "to_number": "", "recording_url": event.recording_url or "", "provider_transcript": event.transcript or "", "ended_at": event.ended_at or "", "metadata": event.metadata, "created_at": utc_now()}, ConditionExpression="attribute_not_exists(call_sid)")
        except ConditionalWriteError:
            pass
    calls_table().update_item(
        Key={"call_sid": event.call_id},
        UpdateExpression="SET #status=:status, recording_url=:recording_url, provider_transcript=:transcript, ended_at=:ended_at, updated_at=:updated_at",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":status": event.status, ":recording_url": event.recording_url or "", ":transcript": event.transcript or "", ":ended_at": event.ended_at or "", ":updated_at": utc_now()},
    )
    if event.recording_url:
        if os.getenv("QUEUE_MODE", "sqs") == "inline":
            from worker import process

            background_tasks.add_task(process, event.call_id)
        else:
            boto3.client("sqs", region_name=os.getenv("AWS_REGION", "us-east-1")).send_message(
                QueueUrl=os.environ["PROCESSING_QUEUE_URL"],
                MessageBody=json.dumps({"call_sid": event.call_id, "tenant_id": event.tenant_id}),
                MessageDeduplicationId=event.event_id,
                MessageGroupId=event.tenant_id,
            )
    audit(event.tenant_id, "vapi", f"{event.direction}_call_event_received", event.call_id, {"event_id": event.event_id, "status": event.status})
    return {"status": "accepted", "call_id": event.call_id}
