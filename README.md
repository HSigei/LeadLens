# LeadLens

LeadLens is a Vapi call-event, post-call analytics, reporting, and outbound-dialing service, with a Custom LLM endpoint that generates live conversational replies. Vapi owns the live call, speech recognition, and speech synthesis. LeadLens has no CRM read/lookup or booking provider. Unstructured document-based knowledge retrieval exists but is disabled by default (`KNOWLEDGE_BACKEND=disabled`; see the Custom LLM section).

## What Runs Today

### Inbound and Outbound Vapi Events

`POST /webhooks/vapi/call` accepts a signed, normalized Vapi call event. It validates `X-Vapi-Signature` with `VAPI_WEBHOOK_SECRET`, verifies the tenant for a newly seen call, writes or updates the call record, and queues processing whenever `recording_url` is present.

For outbound calling:

- `POST /webhooks/ghl/outbound` accepts a GoHighLevel trigger signed with `GHL_OUTBOUND_WEBHOOK_SECRET`. It requires a `contact_id`.
- `POST /webhooks/outbound/trigger` accepts either the same GHL flow or a generic CRM/form trigger. Generic triggers require `X-Outbound-Trigger-Secret` to match `OUTBOUND_TRIGGER_SECRET`.
- LeadLens calls Vapi's call API using `VAPI_API_KEY`, `VAPI_ASSISTANT_ID`, and `VAPI_PHONE_NUMBER_ID`, then stores an `outbound_started` call record.

The generic call-center bridge also exposes `POST /api/call-center/events`, signed by `CALL_CENTER_WEBHOOK_SECRET`, and an admin-only `POST /api/call-center/preflight` endpoint. Its recording events are not currently compatible with the worker, which only processes call records whose provider is `vapi`.

### Post-Call Processing

When a Vapi event supplies a recording URL, LeadLens either adds `process(call_sid)` as a FastAPI background task when `QUEUE_MODE=inline`, or sends `{call_sid, tenant_id}` to `PROCESSING_QUEUE_URL` for `worker.py` to consume.

The worker downloads the Vapi recording, stores it in S3-compatible object storage, transcribes it through Groq, redacts common email, phone, national-ID, and SSN patterns before Groq analysis, creates an Excel report, optionally posts a call outcome to one configured CRM webhook, and emails a presigned report URL.

Only the `QUEUE_MODE=sqs` path calls `handle_processing_failure()`: it retries failures up to `WORKER_MAX_RETRY_COUNT` (default `5`) and then marks the call `failed`. `QUEUE_MODE=inline` uses FastAPI `BackgroundTasks` directly; failures in that mode are neither retried nor recorded as `failed`.

Object keys are:

- `tenants/{tenant_id}/recordings/{call_sid}/recording.mp3`
- `tenants/{tenant_id}/transcripts/{call_sid}/transcript.json`
- `tenants/{tenant_id}/reports/{call_sid}/call-analysis.xlsx`

Raw transcript storage is disabled by default with `STORE_RAW_TRANSCRIPTS=false`; the stored transcript is redacted unless it is explicitly enabled.

### Tenant Policies and Reporting

Tenant policy comes from `TENANT_POLICY_FILE`, or from `TENANT_ROUTING_JSON` when no file is configured. For a bare local Uvicorn run, set `TENANT_POLICY_FILE` to a relative path such as `policies/tenants.json`; the container image's `/service/policies/tenants.json` does not exist outside the container. A tenant must provide its identifier, escalation number, privacy notice version, lawful basis, jurisdiction, processing region, cross-border safeguard, approval metadata, and a DPIA approval for configured high-risk jurisdictions.

Tenant `analysis_fields` can add `boolean` or `text` fields to the post-call Groq analysis schema. They are not live-agent context.

The authenticated supervisor API exposes:

- `GET /api/calls` for tenant-filtered call records.
- `GET /api/calls/{call_sid}/report-url` for a 15-minute report URL after an application-level tenant check.
- `GET /api/metrics` for call metrics and stored learning guidance.
- `DELETE /api/privacy/calls/{call_sid}` for an admin with `X-Data-Subject-Verified: true`; it deletes that call record and its audio, transcript, and report objects.

`learning.py` can aggregate completed calls into prompt guidance stored on the tenant's organization record. Nothing currently injects this guidance into a live Vapi assistant or an LLM prompt.

## Data Stores

`database.py` uses PostgreSQL through `DATABASE_URL` and creates `calls`, `audit_events`, and `organizations` tables. Call queries are filtered by `tenant_id`; point reads require the caller to check the record tenant ID in application code. PostgreSQL is required for every data path.

S3-compatible storage is accessed through boto3. Set `AWS_ENDPOINT_URL` for an S3-compatible provider such as Cloudflare R2 or Backblaze B2. `CALL_DATA_KMS_KEY_ID` enables AWS KMS headers; leave it empty for providers that do not support them.

There is no CRM read implementation. `CRM_WEBHOOK_URL` provides a write-only CRM webhook sync for completed-call outcomes.

## Configuration

Copy [.env.example](.env.example) to a protected local `.env` only for development. Do not commit real credentials.

Core runtime configuration:

- `DATABASE_URL`: PostgreSQL connection string.
- `VAPI_API_KEY`, `VAPI_ASSISTANT_ID`, `VAPI_PHONE_NUMBER_ID`, `VAPI_WEBHOOK_SECRET`: Vapi outbound and event integration.
- `CALL_CENTER_WEBHOOK_SECRET`: HMAC protection for `POST /api/call-center/events`.
- `GHL_OUTBOUND_WEBHOOK_SECRET`: HMAC protection for `POST /webhooks/ghl/outbound` and GHL requests to `POST /webhooks/outbound/trigger`.
- `OUTBOUND_TRIGGER_SECRET`: shared-secret protection for generic requests to `POST /webhooks/outbound/trigger`.
- `GROQ_API_KEY`, `GROQ_TRANSCRIPTION_MODEL`, `GROQ_ANALYSIS_MODEL`: post-call transcription and analysis.
- `CALL_DATA_BUCKET`, optional `CALL_DATA_KMS_KEY_ID`, and optional `AWS_ENDPOINT_URL`: object storage.
- `PROCESSING_QUEUE_URL` and `QUEUE_MODE`: durable worker queue or local inline mode.
- `REPORT_SENDER`, `REPORT_RECIPIENTS`: report delivery.
- `DASHBOARD_JWT_SECRET`, or `OIDC_ISSUER` and `OIDC_AUDIENCE`: dashboard authentication.

`GROQ_AGENT_MODEL`, `AGENT_GREETING`, and `CUSTOM_LLM_API_KEY` are reserved for the planned Custom LLM endpoint; no current runtime code consumes them.

## Local Run

Install dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Create a policy file from the example and validate it:

```powershell
Copy-Item policies\tenants.example.json policies\tenants.json
.\.venv\Scripts\python.exe tools\validate_tenant_policy.py policies\tenants.json
```

Start the API:

```powershell
.\.venv\Scripts\uvicorn.exe app:app --host 0.0.0.0 --port 8080
```

For local inline processing set `QUEUE_MODE=inline`; do not start `worker.py`. For SQS processing, set `QUEUE_MODE=sqs` and run the worker separately:

```powershell
.\.venv\Scripts\python.exe worker.py
```

Run project checks:

```powershell
.\scripts\check.ps1
```

## Deployment

The optional [infra](infra) Terraform configuration provisions an S3 bucket, KMS key, FIFO SQS queue and DLQ, ECS API and worker services, ALB, IAM roles, CloudWatch logging/alarms, and an SNS alert subscription. It expects ARNs for existing Secrets Manager secrets, including `DATABASE_URL` and Vapi/Groq credentials. It does not provision PostgreSQL, Vapi, Groq, SES verification, or a scheduler for `aggregator.py`.

Terraform commands:

```powershell
terraform -chdir=infra init
terraform -chdir=infra validate
terraform -chdir=infra plan
```

## Custom LLM

LeadLens exposes `POST /custom-llm/{secret_key}/chat/completions` for Vapi Custom LLM requests. It validates the path-segment key against `CUSTOM_LLM_API_KEY`, resolves the tenant through the request `assistant.id`, prepends a LeadLens system prompt, and streams Groq output back as OpenAI-compatible Server-Sent Events.

Each tenant policy must include a unique `vapi_assistant_id` matching its Vapi assistant. The endpoint fails closed when the assistant ID, call ID, messages, key, or tenant mapping is missing or invalid. It uses Vapi's provided message array for every request and does not retain server-side conversation history.

Configure the Vapi assistant with `model.provider: "custom-llm"` and a base URL of `https://YOUR_DOMAIN/custom-llm/YOUR_SECRET_KEY`; Vapi appends `/chat/completions`. Set Model Advanced "Metadata Send Mode" to "Destructured" only when you need the observed request metadata fields; tenant resolution does not depend on metadata or variable values.

When the latest caller message contains `representative`, `human`, `agent`, `supervisor`, `stop calling`, or `do not call`, LeadLens streams a conversational handoff confirmation and records an escalation audit event. It does not yet invoke Vapi's native Transfer Call tool.

Before deploying, confirm `GROQ_AGENT_MODEL` (and `GROQ_ANALYSIS_MODEL`, `GROQ_TRANSCRIPTION_MODEL`) are still valid for the configured Groq account by querying `GET https://api.groq.com/openai/v1/models`; Groq's available models can change per account/tier without notice, and an outdated model name fails with `model_not_found` only at request time.

### Knowledge Retrieval

Set `KNOWLEDGE_BACKEND=postgres` to enable unstructured document retrieval (default `disabled`). An authenticated admin can upload document text with `POST /api/knowledge/upload` (`filename`, `text_content`); LeadLens chunks the text and stores it per tenant in PostgreSQL. Retrieval uses PostgreSQL full-text search (`to_tsvector`/`plainto_tsquery`/`ts_rank`), scoped by `tenant_id`, not semantic/embedding search — see [docs/CUSTOM_LLM_FINDINGS.md](docs/CUSTOM_LLM_FINDINGS.md) for why Chroma was evaluated and rejected, and the resulting search-quality trade-off.
