# Integration Guide

## Twilio Voice

The application receives a signed inbound call webhook, requests consent, starts dual-channel recording after consent, and gathers caller speech one turn at a time. The caller can request a person, agent, representative, supervisor, or no further calls; the service transfers to `escalation_number` from the tenant policy.

Required values: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `PUBLIC_BASE_URL`, and a tenant E.164 number mapping.

## OpenAI

The API service sends recent call turns to the configured conversational model. The worker sends MP3 recordings for transcription and PII-redacted transcripts for structured analysis. The analysis schema includes keywords, objections, sentiment, performance, resolution, missed opportunities, training recommendations, revenue opportunity, and customer-experience notes.

Required value: `OPENAI_API_KEY`. Select models with `OPENAI_AGENT_MODEL`, `OPENAI_TRANSCRIPTION_MODEL`, and `OPENAI_ANALYSIS_MODEL`.

## CRM

The worker can send a normalized `call.analyzed` event to `CRM_WEBHOOK_URL` with an `Idempotency-Key` equal to the call SID. The payload includes tenant ID, disposition, revenue opportunity, sentiment, performance score, and follow-up requirement.

HubSpot, Salesforce, and Zoho authorization routes are implemented through OAuth. Register the application with each provider, set its callback URL to `/api/integrations/{provider}/callback`, and configure the provider client ID and secret. Access tokens are saved in Secrets Manager.

## Knowledge Base

An administrator uploads a document through `/api/knowledge/documents`. The service writes the document and a tenant metadata sidecar to encrypted S3 and starts a Bedrock ingestion job. During a call, retrieval filters results by `tenant_id` and adds at most four excerpts to the agent system prompt.

Provision and configure a Bedrock Knowledge Base plus an S3 data source before enabling uploads. Upload only approved, current material. Knowledge retrieval supplements the prompt; it does not remove the need for response review and escalation rules.

## Identity

For development, dashboard tokens use `DASHBOARD_JWT_SECRET`. Production uses an OIDC provider with `OIDC_ISSUER` and `OIDC_AUDIENCE`. Tokens must contain `sub`, `role`, and `tenant_id`, optionally prefixed by `OIDC_CLAIMS_NAMESPACE`.

Supported roles are `supervisor`, `manager`, and `admin`. Admin is required for onboarding changes, privacy approval, provider connection metadata, document upload, billing checkout, and data erasure.

## Stripe

The billing route creates a subscription checkout session using `STRIPE_PRICE_ID`. Configure Stripe to call `/api/billing/webhook` and set `STRIPE_WEBHOOK_SECRET`. The webhook only updates billing status after Stripe signature verification. Use Stripe test mode until the complete tenant flow is accepted.