# Call Center Analysis Agent

This project implements a voice-call analysis workflow. Twilio handles inbound voice, OpenAI generates responses and post-call analysis, and a worker process stores the results in AWS, generates reports, and sends notifications.

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

The generated report is a per-call workbook. DynamoDB retains the analysis records so the scheduled aggregation job creates daily, weekly, and client-wide master workbooks without unsafe concurrent edits to an Excel file.
