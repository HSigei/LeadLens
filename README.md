# LeadLens

> Copyright (c) 2026 Harry Kipngetich Sigei. Licensed under the [Apache License, Version 2.0](LICENSE). LeadLens is a claimed product name; no trademark license is granted.

This is a real, provider-backed call agent. Twilio collects caller speech one turn at a time, OpenAI generates responses, and a separate durable worker processes the completed call into encrypted records, an Excel report, and an email delivery.

Read [DISCLAIMER.md](DISCLAIMER.md), [TRADEMARKS.md](TRADEMARKS.md), and [docs/GLOBAL_COMPLIANCE.md](docs/GLOBAL_COMPLIANCE.md) before production use.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [API reference](docs/API.md)
- [Configuration reference](docs/CONFIGURATION.md)
- [Quickstart](docs/QUICKSTART.md)
- [Self-hosting with Terraform](docs/SELF_HOSTING.md)
- [Integration guide](docs/INTEGRATIONS.md)
- [Operations runbook](docs/OPERATIONS.md)
- [Testing guide](docs/TESTING.md)
- [Security architecture](docs/SECURITY_ARCHITECTURE.md)
- [Global compliance](docs/GLOBAL_COMPLIANCE.md)

## Required infrastructure

- A Twilio Programmable Voice number. Set its inbound webhook to `https://YOUR_DOMAIN/webhooks/twilio/voice`. Enable call recording and set its recording status callback to `https://YOUR_DOMAIN/webhooks/twilio/recording`.
- A private S3 bucket with Block Public Access, versioning, lifecycle retention, and SSE-KMS enabled.
- DynamoDB table with partition key `call_sid` (String), point-in-time recovery enabled.
- SQS queue with a dead-letter queue. Run `worker.py` as a separate ECS/Fargate service or worker process.
- SES verified sender and recipients. Store all secrets in AWS Secrets Manager or the hosting platform's secret store.

## Setup

Copy the names in `.env.example` into your secret store with real values. Install dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

For a local container run, copy `.env.example` to `.env`, provide non-production values, then run `docker compose up --build`. The onboarding page is available at `http://localhost:8080`. The unauthenticated health endpoint works without provider credentials; live voice, data, and billing paths require their configured services.

Run the local verification suite with:

```powershell
.\scripts\check.ps1
```

Create `policies/tenants.json` from `policies/tenants.example.json`. Have the client privacy owner complete and approve its jurisdiction, consent, transfer, and contact fields, then validate it before deployment:

```powershell
.\.venv\Scripts\python.exe tools\validate_tenant_policy.py policies\tenants.json
```

## Hosted SaaS Model

LeadLens is intended to run as a managed SaaS service. Customers use the hosted onboarding flow to create their organization profile, record privacy approval, connect supported providers, and manage tenant policy. Platform infrastructure is operated separately by the LeadLens service operator and is not part of this repository.

For local development, mount the reviewed policy file at `TENANT_POLICY_FILE`. For Terraform self-hosting, provide the same reviewed policy as the sensitive `tenant_policy_json` variable; it is injected as `TENANT_ROUTING_JSON` and is not baked into the container image.

## Self-Hosting

The optional [infra](infra) Terraform module lets organizations deploy LeadLens into their own AWS account. Copy `infra/terraform.tfvars.example` to `infra/terraform.tfvars`, fill in account-specific values, then run:

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

Do not connect a client number before legal approval of recording consent, disclosure language, retention and deletion policy, permitted AI uses, escalation paths, and the agent's approved knowledge base. Ensure the AWS runtime role is least privilege, CloudTrail logging is enabled, S3 has no public access, and the SQS DLQ is monitored. Every webhook is signature-validated before call data is accepted.

The generated report is a per-call workbook. DynamoDB retains the analysis records so the scheduled aggregation job creates daily, weekly, and client-wide master workbooks without unsafe concurrent edits to an Excel file.
