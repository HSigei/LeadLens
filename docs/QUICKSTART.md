# Quickstart

Choose a deployment path before configuring the application.

## Local Evaluation

1. Install Python 3.12 and Docker Desktop.
2. Copy `.env.example` to `.env` and use non-production provider values.
3. Copy `policies/tenants.example.json` to `policies/tenants.json` and complete the example policy.
4. Run `python tools/validate_tenant_policy.py policies/tenants.json`.
5. Run `docker compose up --build`.
6. Open `http://localhost:8080` and verify `/healthz` returns `{"status":"ok"}`.

Local evaluation does not make external Twilio, AWS, OpenAI, CRM, Stripe, or Bedrock integrations available until each provider is configured.

## Managed deployment

Use the onboarding flow to create an organization, select its jurisdiction, record approval state, connect providers, upload approved knowledge, and complete the sandbox acceptance tests. Deployment, monitoring, secrets, and production access are handled by the hosting environment.

## Self-Hosted AWS

1. Read [SELF_HOSTING.md](SELF_HOSTING.md).
2. Build and publish the container image to a registry available to your AWS account.
3. Create Secrets Manager values and copy `infra/terraform.tfvars.example` to `infra/terraform.tfvars`.
4. Place reviewed tenant policy JSON in `tenant_policy_json` and never commit the tfvars file.
5. Run `terraform -chdir=infra init`, `validate`, `plan`, then `apply`.
6. Set the public DNS name, configure Twilio webhooks, confirm the alert subscription, and run sandbox acceptance tests before taking live calls.