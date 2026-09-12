from __future__ import annotations

import io
import json
import logging
import os
import time
from datetime import UTC, datetime

import boto3
import httpx
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from core import audit, calls_table, redact_pii, retention_expiry, utc_now, validate_analysis
from observability import capture_exception, configure_logging, log_event
from storage import storage_client
from tenant_policy import tenant_by_id


configure_logging()


def env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} must be configured")
    return value


REGION = os.getenv("AWS_REGION", "us-east-1")
MAX_RETRY_COUNT = int(os.getenv("WORKER_MAX_RETRY_COUNT", "5"))
s3 = storage_client()
sqs = boto3.client("sqs", region_name=REGION)
ses = boto3.client("sesv2", region_name=REGION)


def handle_processing_failure(call_sid: str, error: Exception) -> bool:
    call = calls_table().get_item(Key={"call_sid": call_sid}).get("Item")
    if not call:
        return False
    retry_count = int(call.get("retry_count", 0)) + 1
    if retry_count >= MAX_RETRY_COUNT:
        calls_table().update_item(
            Key={"call_sid": call_sid},
            UpdateExpression="SET #status=:status, retry_count=:retry_count, last_error=:error, updated_at=:updated_at",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": "failed", ":retry_count": retry_count, ":error": {"type": type(error).__name__, "message": str(error)}, ":updated_at": utc_now()},
        )
        audit(call["tenant_id"], "worker", "call_processing_failed", call_sid, {"error_type": type(error).__name__, "retry_count": retry_count, "max_retries": MAX_RETRY_COUNT})
        return False
    calls_table().update_item(
        Key={"call_sid": call_sid},
        UpdateExpression="SET retry_count=:retry_count, last_error=:error, #status=:status, updated_at=:updated_at",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":retry_count": retry_count, ":error": {"type": type(error).__name__, "message": str(error)}, ":status": "queued", ":updated_at": utc_now()},
    )
    return True


def groq_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {env('GROQ_API_KEY')}"}


def transcribe(audio: bytes, filename: str) -> str:
    response = httpx.post("https://api.groq.com/openai/v1/audio/transcriptions", headers=groq_headers(), files={"file": (filename, audio, "audio/mpeg")}, data={"model": os.getenv("GROQ_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo")}, timeout=120)
    response.raise_for_status()
    return response.json()["text"].strip()


def analyze(transcript: str, custom_fields: list[dict[str, str]] | None = None) -> dict:
    custom_fields = custom_fields or []
    prompt = """Analyze this call-center transcript. Return JSON only with: keywords (string array), objections (string array), sentiment (positive|mixed|negative), sentiment_score (integer 0-100), agent_performance_score (integer 0-100), issue_resolved (boolean), missed_opportunities (string array), training_recommendations (string array), revenue_opportunity (low|medium|high), customer_experience_notes (string). Do not infer facts absent from the transcript."""
    if custom_fields:
        field_lines = "\n".join(f"- {field['name']} ({field.get('type', 'text')}): {field.get('prompt', field['name'])}" for field in custom_fields)
        prompt += f"\n\nAlso return a top-level \"custom\" object with exactly these additional fields, inferred only from the transcript:\n{field_lines}"
    response = httpx.post("https://api.groq.com/openai/v1/chat/completions", headers={**groq_headers(), "Content-Type": "application/json"}, json={"model": os.getenv("GROQ_ANALYSIS_MODEL", "llama-3.3-70b-versatile"), "response_format": {"type": "json_object"}, "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": transcript}]}, timeout=90)
    response.raise_for_status()
    return validate_analysis(json.loads(response.json()["choices"][0]["message"]["content"]), custom_fields)


def workbook_bytes(call: dict, analysis: dict) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Call Analysis"
    sheet.append(["Call ID", "Processed At", "Sentiment", "Performance", "Resolved", "Revenue Opportunity", "Keywords", "Objections", "Missed Opportunities", "Training Recommendations", "Customer Experience", "Custom Fields"])
    sheet.append([call["call_sid"], datetime.now(UTC).isoformat(), analysis["sentiment"], analysis["agent_performance_score"], analysis["issue_resolved"], analysis["revenue_opportunity"], "; ".join(analysis["keywords"]), "; ".join(analysis["objections"]), " ".join(analysis["missed_opportunities"]), " ".join(analysis["training_recommendations"]), analysis["customer_experience_notes"], "; ".join(f"{name}: {field_value}" for name, field_value in analysis.get("custom", {}).items())])
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="0B525B")
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = min(max(len(str(cell.value or "")) for cell in column) + 2, 45)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def put_encrypted(key: str, content: bytes, content_type: str) -> None:
    options = {"Bucket": env("CALL_DATA_BUCKET"), "Key": key, "Body": content, "ContentType": content_type, "Metadata": {"retention-expires-at": str(retention_expiry())}}
    if kms_key_id := os.getenv("CALL_DATA_KMS_KEY_ID"):
        options.update({"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": kms_key_id})
    s3.put_object(**options)


def sync_crm(call: dict, analysis: dict) -> None:
    endpoint = os.getenv("CRM_WEBHOOK_URL")
    if not endpoint:
        return
    payload = {"event": "call.analyzed", "tenant_id": call["tenant_id"], "call_sid": call["call_sid"], "disposition": "resolved" if analysis["issue_resolved"] else "follow_up", "revenue_opportunity": analysis["revenue_opportunity"], "sentiment": analysis["sentiment"], "agent_performance_score": analysis["agent_performance_score"], "follow_up_required": not analysis["issue_resolved"]}
    response = httpx.post(endpoint, json=payload, headers={"Authorization": f"Bearer {env('CRM_WEBHOOK_TOKEN')}", "Idempotency-Key": call["call_sid"]}, timeout=30)
    response.raise_for_status()


def process(call_sid: str) -> None:
    log_event("worker.process.started", call_sid=call_sid)
    call = calls_table().get_item(Key={"call_sid": call_sid}).get("Item")
    if not call:
        raise ValueError(f"Call not found for worker: {call_sid}")
    if call.get("status") == "completed":
        return
    if call.get("tenant_id") is None or call.get("recording_url") is None:
        raise ValueError(f"Call state is incomplete before processing: {call_sid}")
    if call.get("provider") != "vapi":
        raise ValueError(f"Unsupported recording provider for worker: {call.get('provider')}")
    recording = httpx.get(call["recording_url"], timeout=120)
    recording.raise_for_status()
    tenant_id = call["tenant_id"]
    audio_key = f"tenants/{tenant_id}/recordings/{call_sid}/recording.mp3"
    put_encrypted(audio_key, recording.content, "audio/mpeg")
    transcript = transcribe(recording.content, f"{call_sid}.mp3")
    custom_fields = (tenant_by_id(tenant_id) or {}).get("analysis_fields", [])
    analysis = analyze(redact_pii(transcript), custom_fields)
    transcript_key = f"tenants/{tenant_id}/transcripts/{call_sid}/transcript.json"
    stored_transcript = transcript if os.getenv("STORE_RAW_TRANSCRIPTS", "false").lower() == "true" else redact_pii(transcript)
    put_encrypted(transcript_key, json.dumps({"transcript": stored_transcript, "analysis": analysis}).encode(), "application/json")
    report_key = f"tenants/{tenant_id}/reports/{call_sid}/call-analysis.xlsx"
    put_encrypted(report_key, workbook_bytes(call, analysis), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    sync_crm(call, analysis)
    calls_table().update_item(Key={"call_sid": call_sid}, UpdateExpression="SET #status=:status, analysis=:analysis, audio_key=:audio, transcript_key=:transcript, report_key=:report, processed_at=:processed", ExpressionAttributeNames={"#status": "status"}, ExpressionAttributeValues={":status": "completed", ":analysis": analysis, ":audio": audio_key, ":transcript": transcript_key, ":report": report_key, ":processed": utc_now()})
    audit(tenant_id, "worker", "call_processed", call_sid)
    report_url = s3.generate_presigned_url("get_object", Params={"Bucket": env("CALL_DATA_BUCKET"), "Key": report_key}, ExpiresIn=3600)
    recipients = [address.strip() for address in env("REPORT_RECIPIENTS").split(",") if address.strip()]
    ses.send_email(FromEmailAddress=env("REPORT_SENDER"), Destination={"ToAddresses": recipients}, Content={"Simple": {"Subject": {"Data": f"Call intelligence report: {call_sid}"}, "Body": {"Text": {"Data": f"Call analysis complete. Secure report link, expiring in 24 hours: {report_url}"}}}})
    log_event("worker.process.completed", call_sid=call_sid)


def run() -> None:
    if os.getenv("QUEUE_MODE", "sqs") == "inline":
        raise RuntimeError("worker.py must not run when QUEUE_MODE=inline.")
    while True:
        try:
            messages = sqs.receive_message(QueueUrl=env("PROCESSING_QUEUE_URL"), MaxNumberOfMessages=1, WaitTimeSeconds=20).get("Messages", [])
        except Exception as error:
            log_event("worker.queue.exception", logging.ERROR, error_type=type(error).__name__, error=str(error))
            capture_exception(error)
            time.sleep(5)
            continue
        for message in messages:
            call_sid = "unknown"
            try:
                call_sid = json.loads(message["Body"])["call_sid"]
                process(call_sid)
                sqs.delete_message(QueueUrl=env("PROCESSING_QUEUE_URL"), ReceiptHandle=message["ReceiptHandle"])
            except Exception as error:
                log_event("worker.process.exception", logging.ERROR, call_sid=call_sid, error_type=type(error).__name__, error=str(error))
                capture_exception(error)
                if not handle_processing_failure(call_sid, error):
                    sqs.delete_message(QueueUrl=env("PROCESSING_QUEUE_URL"), ReceiptHandle=message["ReceiptHandle"])
        time.sleep(1)


if __name__ == "__main__":
    run()
