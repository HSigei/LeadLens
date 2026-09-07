# API Reference

All `/api` endpoints require `Authorization: Bearer <access-token>` unless noted otherwise. Tokens need `tenant_id`, `role`, and `sub` claims. Organization-changing endpoints require the `admin` role.

## Public and Telephony

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Hosted onboarding page. |
| `GET` | `/healthz` | Unauthenticated health response: `{"status":"ok"}`. |
| `POST` | `/webhooks/twilio/voice` | Signed inbound-call webhook. |
| `POST` | `/webhooks/twilio/consent` | Signed caller consent response. |
| `POST` | `/webhooks/twilio/turn` | Signed caller speech turn. |
| `POST` | `/webhooks/twilio/recording` | Signed completed-recording callback. |
| `POST` | `/api/billing/webhook` | Stripe-signed billing event. |

Twilio callbacks use form data and must include `X-Twilio-Signature`. Configure `PUBLIC_BASE_URL` to the external HTTPS URL Twilio invokes.

## Supervisor API

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/calls?limit=50` | Supervisor+ | Lists calls for the token's tenant only. |
| `GET` | `/api/calls/{call_sid}/report-url` | Supervisor+ | Returns a 15-minute S3 report URL. |
| `GET` | `/api/metrics` | Supervisor+ | Returns processed-call count, resolution rate, and average performance. |
| `DELETE` | `/api/privacy/calls/{call_sid}` | Admin | Erases eligible data after `X-Data-Subject-Verified: true`. |

The deletion endpoint returns `409` for records on legal hold and `404` if the call belongs to another tenant.

## Onboarding API

| Method | Path | Body | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/onboarding/status` | None | Returns readiness checks. |
| `PUT` | `/api/onboarding/organization` | `name`, `jurisdiction`, `speech_language`, `privacy_contact` | Saves the organization profile. |
| `POST` | `/api/onboarding/privacy-approval` | None | Records the administrator's privacy approval. |
| `POST` | `/api/onboarding/connections` | `provider`, `connection_id`, `scopes` | Records non-secret connection metadata. |

## Provider APIs

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/integrations/{hubspot|salesforce|zoho}/authorize` | Returns the provider authorization URL. |
| `GET` | `/api/integrations/{provider}/callback?code=...&state=...` | Exchanges authorization code and stores secret token material. |
| `POST` | `/api/billing/checkout` | Creates a Stripe subscription checkout URL. |
| `POST` | `/api/knowledge/documents` | Uploads a knowledge document as multipart field `document`; maximum 10 MB. |

Do not call webhook or OAuth callback endpoints manually in production. Provider signatures, state, and authorization codes are required.