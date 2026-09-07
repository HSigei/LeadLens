# Security Architecture

## Implemented Controls

| Area | Control |
| --- | --- |
| Telephony | Twilio callback signature validation. Consent is required before recording and AI processing by default. |
| Call state | AI turns and recording callbacks are accepted only for active, consented calls. Recording events use deterministic idempotency and SQS FIFO deduplication. |
| Data | S3 artifacts use SSE-KMS, public access blocking, versioning, and lifecycle retention. DynamoDB uses encryption, backups, and TTL. |
| Tenant isolation | One phone number maps to one tenant. S3 uses tenant prefixes; dashboard access and data reads are tenant-scoped. |
| AI safety | Analysis receives PII-redacted transcripts. Stored raw transcripts are disabled by default. AI output must match a strict schema. |
| Access | Dashboard requests require tenant, role, and subject claims. OIDC/JWKS is supported for production SSO. |
| Browser/API | CSP, HSTS for HTTPS, no-store API responses, anti-clickjacking, no MIME sniffing, referrer policy, permissions policy, and a request-body limit. |
| Credentials | Provider OAuth tokens are stored in Secrets Manager. ECS execution tasks can read only the named startup secrets. |
| Resilience | Queue retries, DLQ, alerting, audit events, presigned report links, and human escalation paths. |

## Operational Requirements

- Terminate TLS at the load balancer with the configured TLS 1.3/1.2 policy.
- Configure application and provider secrets in a secret store, never `.env` in production.
- Restrict IAM policies further for your account and tenant-secret naming scheme before production use.
- Confirm alert delivery, enable CloudTrail, centralize CloudWatch logs, and review privileged dashboard roles at least quarterly.
- Test consent, opt-out, human handoff, deletion, legal hold, DLQ replay, and provider outage flows in a sandbox before each release.

## Limitations

No source code can guarantee security. Deployers remain responsible for AWS account security, network egress controls, WAF/DDoS protection, OIDC configuration, provider permissions, endpoint monitoring, patch management, penetration testing, and all jurisdiction-specific obligations.

Report vulnerabilities according to [SECURITY.md](../SECURITY.md).