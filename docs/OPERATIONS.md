# Operations Runbook

## Deployment

1. For the managed SaaS model, platform operators provision and secure the hosted runtime, secrets, data stores, monitoring, and HTTPS endpoint. For self-hosting, use the optional Terraform module and the self-hosting guide.
2. Store Twilio credentials, OpenAI key, and dashboard JWT secret in the platform secret store. Never place live values in `.env`.
3. Set `PUBLIC_BASE_URL` to the HTTPS hostname, configure Twilio callbacks, and run a consented sandbox call.
4. Run the web service, worker, and daily aggregation as independently monitored workloads. Terraform provisions the API and worker; configure aggregation through the target platform scheduler.

## Failure Handling

- SQS retries failed recordings five times, then moves them to the encrypted DLQ. Alert and replay only after resolving the root cause.
- Webhook delivery is idempotent. Do not manually replay a callback until confirming the recording and call record state.
- CloudWatch alarms must cover non-empty DLQ, ALB 5xx, ECS task failures, and missing daily reports.
- For a provider outage, remove the number's Twilio voice webhook or route directly to the configured human escalation number.
- Confirm queue-failure and service-health alerts reach the on-call system; alerts are not operational until their delivery path is tested.

## Pre-Launch Testing

- Use a Twilio subaccount and a sandbox tenant to run the scenarios in `ACCEPTANCE_TESTS.md`.
- The `tests/load/webhook_smoke.py` probe is restricted to approved sandbox URLs and only tests `/healthz`; it does not create telephone traffic.

## Data Rights and Incidents

- Process deletion requests by deleting all `tenants/{tenant_id}/` objects for the call and deleting its DynamoDB and audit records subject to legal hold.
- Restrict reports to tenant managers. Presigned report URLs expire after 15 minutes on the dashboard and 60 minutes in delivery email.
- Treat recordings and transcripts as sensitive personal data. Notify the client's security lead and follow the incident process if unauthorized access is suspected.