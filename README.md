# Call Center Analysis Agent

This project implements a Vapi voice-call analytics workflow. Vapi handles inbound and outbound voice conversations, while a worker transcribes, analyzes, stores, and reports on completed calls.

## Documentation

Detailed docs are kept locally in `local-notes/` (gitignored, not part of this repo). See that folder on your own machine for architecture, API, configuration, integration, operations, security, compliance, and testing notes.

## Database integration

The application uses PostgreSQL for calls, organizations, and audit events when `DATABASE_URL` is configured. This is the recommended production path for relational reporting, audit queries, retention workflows, and deduplication.

```text
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DATABASE
```

The application creates the initial PostgreSQL tables and indexes on first connection:

- `calls`
- `organizations`
- `audit_events`

Use a managed PostgreSQL service such as Aurora PostgreSQL, keep the database in private subnets, restrict access to the application security group, enable encryption and backups, and store `DATABASE_URL` in Secrets Manager. Do not commit the connection string.

`DATABASE_URL` is required in every environment; the application will not start without it.

## Per-tenant analysis criteria

Every call is always scored on the same fixed core schema (sentiment, agent performance, resolution, keywords, etc.). On top of that, each tenant can define its own extra fields in its tenant policy entry so different companies can track what actually matters to them:

```json
"analysis_fields": [
  { "name": "kyc_verified", "type": "boolean", "prompt": "Whether the agent completed identity/KYC verification during the call." },
  { "name": "upsell_offered", "type": "text", "prompt": "Which product, if any, the agent offered as an upsell." }
]
```

`type` is `boolean` or `text`. These are appended to the analysis prompt for that tenant only, validated against the declared type, and surfaced as a "Custom Fields" column in both the per-call and master Excel reports. A tenant with no `analysis_fields` gets the same output as before this feature existed.

## Required infrastructure

- A Vapi phone number and assistant for inbound and outbound conversations. Configure Vapi to send signed call events to LeadLens.
- A private S3 bucket with Block Public Access, versioning, lifecycle retention, and SSE-KMS enabled.
- PostgreSQL database for calls, organizations, and audit events. Set `DATABASE_URL` in the runtime secret store.
- SQS queue with a dead-letter queue. Run `worker.py` as a separate ECS/Fargate service or worker process.
- SES verified sender and recipients. Store all secrets in AWS Secrets Manager or the hosting platform's secret store.

## Vapi voice calls

Vapi is the sole voice provider. It owns the phone number, speech recognition, call recording, voice output, and live assistant conversation. Configure Vapi to send normalized inbound and outbound call events to `https://YOUR_DOMAIN/webhooks/vapi/call`, signed with `VAPI_WEBHOOK_SECRET` in `X-Vapi-Signature`.

Each event needs `event_id`, `call_id`, `tenant_id`, `direction` (`inbound` or `outbound`), and `status`; it can additionally contain `caller_number`, `recording_url`, `transcript`, `ended_at`, and `metadata`. LeadLens stores each call and queues post-call processing when a recording URL is supplied.

## GHL + Vapi outbound calls

GoHighLevel (GHL) is the campaign trigger; Vapi dials and runs the voice conversation. Configure a GHL workflow to send a signed `POST` request to `https://YOUR_DOMAIN/webhooks/ghl/outbound` with:

```json
{
  "tenant_id": "client-acme",
  "contact_id": "ghl-contact-id",
  "phone_number": "+15551234567",
  "name": "Customer name",
  "campaign_id": "renewal-september",
  "metadata": {"lead_source": "renewal"}
}
```

Sign the exact request body with HMAC-SHA256 using `GHL_OUTBOUND_WEBHOOK_SECRET` and send it as `X-GHL-Signature: sha256=<digest>`. The tenant must be in the approved tenant policy. The service sends the call request to Vapi using `VAPI_API_KEY`, `VAPI_ASSISTANT_ID`, and `VAPI_PHONE_NUMBER_ID`.

Any CRM or form tool can instead call `/webhooks/outbound/trigger` with `provider: "generic"`, the same tenant, phone, name, campaign, and metadata fields, and `X-Outbound-Trigger-Secret` equal to `OUTBOUND_TRIGGER_SECRET`. Use the original GHL HMAC path for `provider: "ghl"`; it requires `contact_id` and `X-GHL-Signature`.

## Booking and learning

Booking conversion is tracked from `booking_id` values attached by Vapi or an external booking workflow. LeadLens does not currently create Cal.com bookings itself; Vapi owns the live voice conversation and any in-call booking action.

`learning.generate_prompt_guidance(tenant_id)` aggregates completed calls into common resolved/unresolved caller phrases, missed opportunities, training recommendations, and booking conversion. It saves a short, labeled `prompt_guidance` addendum on the tenant organization record. Dashboard `/api/metrics` returns that guidance and `booking_conversion_rate`.

## Setup

Copy the names in `.env.example` into your secret store with real values. Install dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Free local testing

You can run the inbound call path without AWS. Create a free PostgreSQL database in Neon or Supabase and set `DATABASE_URL`. Create a Cloudflare R2 bucket and set its S3-compatible `AWS_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `CALL_DATA_BUCKET`; leave `CALL_DATA_KMS_KEY_ID` unset. Set `QUEUE_MODE=inline` so recording work runs in the API process. Chroma runs locally in `./data/chroma`, and `KNOWLEDGE_BASE_ID` can remain unset.

Start the API with `docker compose up --build` or `uvicorn app:app --port 8000`, then expose it to Vapi:

```powershell
ngrok http 8080
```

In Vapi, set the server URL to `https://YOUR_NGROK_DOMAIN/webhooks/vapi/call` and configure the same `VAPI_WEBHOOK_SECRET`. A completed recording then follows transcription, Groq analysis, report creation, and R2 storage. Do not start `worker.py` when `QUEUE_MODE=inline`.

For a local container run, copy `.env.example` to `.env`, provide non-production values, then run `docker compose up --build`. The unauthenticated health endpoint at `http://localhost:8080/healthz` works without provider credentials; live voice and data paths require their configured services.

Run the local verification suite with:

```powershell
.\scripts\check.ps1
```

Create `policies/tenants.json` from `policies/tenants.example.json`. Have the client privacy owner complete and approve its jurisdiction, consent, transfer, and contact fields, then validate it before deployment:

```powershell
.\.venv\Scripts\python.exe tools\validate_tenant_policy.py policies\tenants.json
```

## Deployment options

For local development, mount the reviewed policy file at `TENANT_POLICY_FILE`. For Terraform self-hosting, provide the same reviewed policy as the sensitive `tenant_policy_json` variable; it is injected as `TENANT_ROUTING_JSON` and is not baked into the container image.

## Self-Hosting

The optional [infra](infra) Terraform module deploys this application into an AWS account. Copy `infra/terraform.tfvars.example` to `infra/terraform.tfvars`, fill in account-specific values, then run:

```powershell
terraform -chdir=infra init
terraform -chdir=infra validate
terraform -chdir=infra plan
terraform -chdir=infra apply
```

The generated `terraform.tfvars` is ignored by Git. Configure Secrets Manager, a container image, a tenant policy, and Vapi webhooks before accepting live calls.

Start the Vapi event receiver:

```powershell
.\.venv\Scripts\uvicorn.exe app:app --host 0.0.0.0 --port 8000
```

Start the durable processor separately:

```powershell
.\.venv\Scripts\python.exe worker.py
```
Schedule the master workbook job once daily through the platform scheduler:

```powershell
.\.venv\Scripts\python.exe aggregator.py
```

## Required go-live controls

Before connecting a client number, confirm consent rules, disclosure language, retention and deletion policy, permitted AI uses, escalation paths, and the approved knowledge base. Ensure the AWS runtime role follows least privilege, CloudTrail logging is enabled, S3 has no public access, and the SQS DLQ is monitored. Every webhook is signature-validated before call data is accepted.

> **Operational note:** Worker retries are bounded and failed calls retain retry/error metadata. Data-subject erasure records the deleted storage keys and retention period in the audit trail. API routes are rate-limited, with stricter limits for privacy and knowledge-management endpoints.

The generated report is a per-call workbook. The selected database retains call metadata and analysis records so the scheduled aggregation job can create daily, weekly, and client-wide master workbooks without unsafe concurrent edits to an Excel file.
