# Call Center Analysis Agent

This project implements a voice-call analysis workflow. Twilio handles inbound voice, OpenAI generates responses and post-call analysis, and a worker process stores the results in AWS, generates reports, and sends notifications.

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

## Required infrastructure

- A Twilio Programmable Voice number. Set its inbound webhook to `https://YOUR_DOMAIN/webhooks/twilio/voice`. Enable call recording and set its recording status callback to `https://YOUR_DOMAIN/webhooks/twilio/recording`.
- A private S3 bucket with Block Public Access, versioning, lifecycle retention, and SSE-KMS enabled.
- PostgreSQL database for calls, organizations, and audit events. Set `DATABASE_URL` in the runtime secret store.
- SQS queue with a dead-letter queue. Run `worker.py` as a separate ECS/Fargate service or worker process.
- SES verified sender and recipients. Store all secrets in AWS Secrets Manager or the hosting platform's secret store.

## Setup

Copy the names in `.env.example` into your secret store with real values. Install dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

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

The generated `terraform.tfvars` is ignored by Git. Configure Secrets Manager, a container image, a tenant policy, and Twilio webhooks before accepting live calls.

Start the signed Twilio webhook service:

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
