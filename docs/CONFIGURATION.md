# Configuration Reference

Use AWS Secrets Manager or the deployment platform's protected secret store for all secret values. `.env.example` is a names-only local-development reference; never commit a real `.env` file.

## Required Core Settings

| Setting | Purpose |
| --- | --- |
| `AWS_REGION` | AWS region for application resources. |
| `CALL_DATA_BUCKET` | Private KMS-encrypted S3 bucket. |
| `CALL_DATA_KMS_KEY_ID` | KMS key ARN for data encryption. |
| `CALLS_TABLE`, `AUDIT_TABLE`, `ORGANIZATIONS_TABLE` | DynamoDB table names. |
| `PROCESSING_QUEUE_URL` | FIFO queue URL. |
| `PUBLIC_BASE_URL` | External HTTPS base URL, with no trailing slash. |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` | Twilio account credentials. |
| `OPENAI_API_KEY` | OpenAI API credential. |
| `REPORT_SENDER`, `REPORT_RECIPIENTS` | SES sender and comma-separated report recipients. |

## Identity and Dashboard

| Setting | Purpose |
| --- | --- |
| `DASHBOARD_JWT_SECRET` | Development HMAC signing key and OAuth state key. |
| `OIDC_ISSUER`, `OIDC_AUDIENCE` | Set both to validate RS256 JWTs from a managed OIDC provider. |
| `OIDC_CLAIMS_NAMESPACE` | Optional prefix for provider custom `role` and `tenant_id` claims. |

## Agent and Data Controls

| Setting | Default | Purpose |
| --- | --- | --- |
| `OPENAI_AGENT_MODEL` | `gpt-4.1-mini` | Live conversation model. |
| `OPENAI_ANALYSIS_MODEL` | `gpt-4.1-mini` | Post-call structured analysis model. |
| `OPENAI_TRANSCRIPTION_MODEL` | `gpt-4o-mini-transcribe` | Recording transcription model. |
| `TWILIO_VOICE` | `Polly.Joanna` | Twilio speech voice. |
| `STORE_RAW_TRANSCRIPTS` | `false` | Enable only with approved retention and privacy controls. |
| `RETENTION_DAYS` | `365` | Default call-data retention. |
| `AUDIT_RETENTION_DAYS` | `2555` | Default audit-event retention. |

## Integrations

| Setting | Purpose |
| --- | --- |
| `HUBSPOT_OAUTH_CLIENT_ID`, `HUBSPOT_OAUTH_CLIENT_SECRET` | HubSpot OAuth application credentials. |
| `SALESFORCE_OAUTH_CLIENT_ID`, `SALESFORCE_OAUTH_CLIENT_SECRET` | Salesforce connected-app credentials. |
| `ZOHO_OAUTH_CLIENT_ID`, `ZOHO_OAUTH_CLIENT_SECRET` | Zoho OAuth client credentials. |
| `CONNECTION_SECRET_PREFIX` | Secrets Manager path for tenant provider tokens. |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID` | Stripe billing configuration. |
| `KNOWLEDGE_BASE_ID`, `KNOWLEDGE_DATA_SOURCE_ID` | Bedrock knowledge base and S3 data source IDs. |
| `CRM_WEBHOOK_URL`, `CRM_WEBHOOK_TOKEN` | Optional outcome-webhook destination and bearer token. |

## Tenant Policy

Copy `policies/tenants.example.json`, complete every field, and validate it with `python tools/validate_tenant_policy.py policies/tenants.json`. See [GLOBAL_COMPLIANCE.md](GLOBAL_COMPLIANCE.md).

| Deployment mode | Policy setting |
| --- | --- |
| Local Docker Compose | Set `TENANT_POLICY_FILE=/service/policies/tenants.json`; Compose mounts `./policies` read-only. |
| Managed SaaS | The service operator maintains an approved tenant-policy registry. Do not place customer policy content in browser storage. |
| Terraform self-hosting | Set the sensitive `tenant_policy_json` input in `infra/terraform.tfvars`. Terraform provides it as `TENANT_ROUTING_JSON`; leave `TENANT_POLICY_FILE` unset in ECS. |

`TENANT_ROUTING_JSON` remains a runtime fallback for automation and should not be committed to source control when it contains customer routing or approval information.