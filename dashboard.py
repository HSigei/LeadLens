from __future__ import annotations

import os
from typing import Annotated

import boto3
from boto3.dynamodb.conditions import Key
from fastapi import APIRouter, Depends, Header, HTTPException

from core import audit, calls_table, decode_access_token, env


router = APIRouter(prefix="/api", tags=["supervisor"])


def principal(authorization: Annotated[str | None, Header()] = None) -> dict[str, str]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer access token required.")
    return decode_access_token(authorization.removeprefix("Bearer "))


@router.get("/calls")
def list_calls(user: dict[str, str] = Depends(principal), limit: int = 50) -> dict:
    result = calls_table().query(IndexName="tenant-created-at-index", KeyConditionExpression=Key("tenant_id").eq(user["tenant_id"]), ScanIndexForward=False, Limit=max(1, min(limit, 100)))
    audit(user["tenant_id"], user["sub"], "dashboard_calls_read")
    return {"items": result.get("Items", []), "next_key": result.get("LastEvaluatedKey")}


@router.get("/calls/{call_sid}/report-url")
def report_url(call_sid: str, user: dict[str, str] = Depends(principal)) -> dict[str, str]:
    result = calls_table().get_item(Key={"call_sid": call_sid})
    call = result.get("Item")
    if not call or call.get("tenant_id") != user["tenant_id"] or not call.get("report_key"):
        raise HTTPException(404, "Report not found.")
    url = boto3.client("s3", region_name=env("AWS_REGION")).generate_presigned_url("get_object", Params={"Bucket": env("CALL_DATA_BUCKET"), "Key": call["report_key"]}, ExpiresIn=900)
    audit(user["tenant_id"], user["sub"], "report_download_requested", call_sid)
    return {"url": url, "expires_in_seconds": "900"}


@router.get("/metrics")
def metrics(user: dict[str, str] = Depends(principal)) -> dict:
    response = calls_table().query(IndexName="tenant-created-at-index", KeyConditionExpression=Key("tenant_id").eq(user["tenant_id"]), ProjectionExpression="analysis, #status", ExpressionAttributeNames={"#status": "status"})
    calls = response.get("Items", [])
    completed = [call for call in calls if call.get("status") == "completed"]
    total = len(completed)
    resolved = sum(bool(call.get("analysis", {}).get("issue_resolved")) for call in completed)
    performance = sum(int(call.get("analysis", {}).get("agent_performance_score", 0)) for call in completed)
    audit(user["tenant_id"], user["sub"], "dashboard_metrics_read")
    return {"calls_processed": total, "resolution_rate": round(resolved * 100 / total, 1) if total else 0, "average_performance": round(performance / total, 1) if total else 0}


@router.delete("/privacy/calls/{call_sid}")
def erase_call(call_sid: str, user: dict[str, str] = Depends(principal), x_data_subject_verified: Annotated[str | None, Header()] = None) -> dict[str, str]:
    if user["role"] != "admin" or x_data_subject_verified != "true":
        raise HTTPException(403, "An administrator and verified data-subject request are required.")
    call = calls_table().get_item(Key={"call_sid": call_sid}).get("Item")
    if not call or call.get("tenant_id") != user["tenant_id"]:
        raise HTTPException(404, "Call not found.")
    if call.get("legal_hold"):
        raise HTTPException(409, "Call is subject to a legal hold.")
    storage = boto3.client("s3", region_name=env("AWS_REGION"))
    for key_name in ("audio_key", "transcript_key", "report_key"):
        if call.get(key_name):
            storage.delete_object(Bucket=env("CALL_DATA_BUCKET"), Key=call[key_name])
    calls_table().delete_item(Key={"call_sid": call_sid})
    audit(user["tenant_id"], user["sub"], "data_subject_erasure_completed", call_sid)
    return {"status": "erased"}