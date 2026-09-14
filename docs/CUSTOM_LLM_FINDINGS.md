# Custom LLM Findings

Date confirmed: 2026-09-13.

## Confirmed Request Shape

A real Vapi Custom LLM request includes `model`, `messages`, `temperature`, `max_tokens`, and `stream`. `stream` is `true` in real calls. The request also includes `call.id`, `assistant.id`, `assistantOverrides.variableValues`, and `metadata`.

`assistantOverrides.variableValues` is present but empty by default. `metadata` is present but empty and cannot be set from Vapi's assistant dashboard UI.

## Tenant Identification

The live endpoint resolves the tenant from `assistant.id` through the required `vapi_assistant_id` tenant-policy field. It does not derive tenant identity from message content, `metadata`, or `assistantOverrides.variableValues`.

## Interrupted Turns

Vapi can send multiple Custom LLM requests for one conversational turn when the caller continues speaking. In those requests, `numModelRequestInTurn` increments and `assistantTurnInterrupted` is `true`.

## Authentication

Vapi's Custom LLM Model card has no credential field. The diagnostic endpoint uses a constant-time path-secret check at:

```text
/custom-llm/{secret_key}/chat/completions
```

This avoids a query string because Vapi appends `/chat/completions` directly to the configured Custom LLM base URL.

## Live Implementation

`POST /custom-llm/{secret_key}/chat/completions` validates the path secret, resolves the tenant from `assistant.id`, removes Vapi-supplied system messages, prepends the LeadLens system prompt, and streams Groq Chat Completions output as OpenAI-compatible SSE chunks. The endpoint does not retain conversation state; each request uses only the non-system message history Vapi supplied.

It audits the first request for a `call.id`, every processed request, and detected escalation phrases.

## Known Limitations

Escalation phrases receive a streamed conversational handoff response and an audit event. Vapi's native Transfer Call tool invocation requires separate tool-calling support and is not implemented.

Escalation uses a hard substring keyword gate inherited from the legacy call flow. It can produce false positives, for example when a caller says that their supervisor mentioned something; it does not call Groq before responding.

## Environment Loading

`core.py` calls `load_dotenv()` before runtime configuration is read, so a bare Uvicorn process loads the local `.env` file. Docker Compose-provided variables remain authoritative because `load_dotenv()` does not override process environment variables by default.

Tests set `PYTHON_DOTENV_DISABLED=1` in `tests/conftest.py` so local `.env` values do not affect test configuration.

## Additional Real-World Findings (2026-09-13/14)

### `TENANT_POLICY_FILE` path differs by environment

The container image expects `/service/policies/tenants.json`, an absolute path that does not exist on a bare local Uvicorn process. Local bare-Uvicorn runs must set `TENANT_POLICY_FILE=policies/tenants.json` (relative to the repository root). Using the container's absolute path locally makes `policy_registry()` raise a `ValueError`, which `tenant_by_vapi_assistant_id()` now surfaces as `503`.

### Neon pooled connection intermittently failed

The Neon pooled connection endpoint (`...c-12.us-east-1.aws.neon.tech` with pooling in the connection string) intermittently returned `server closed the connection unexpectedly`, even with the Neon compute active and reachable on port `5432`. Switching to the direct (non-pooled) Neon connection string resolved this for local testing. This is a known rough edge to monitor, not a confirmed permanent fix, and may recur under the free tier.

### Groq model availability changed

`llama-3.1-8b-instant`, `llama-3.3-70b-versatile`, and `whisper-large-v3-turbo` all returned `model_not_found` for this account. A direct `GET https://api.groq.com/openai/v1/models` query confirmed the account's actual available models and led to these corrected assignments:

- `GROQ_AGENT_MODEL=openai/gpt-oss-20b`
- `GROQ_ANALYSIS_MODEL=openai/gpt-oss-120b`
- `GROQ_TRANSCRIPTION_MODEL=whisper-large-v3`

Re-query `/v1/models` before assuming any hardcoded Groq model name is still valid; Groq's available models can change per account/tier without notice.

### `num_model_request_in_turn` is currently always `None`

The `turn_processed` audit event's `detail.num_model_request_in_turn` field is not populated from Vapi's request body; it is not extracted from `metadata.numModelRequestInTurn` (or wherever Vapi actually places it). This is a minor known issue, not blocking, and is a candidate for a small follow-up fix.

### End-to-end verification via a real Vapi call

A real Vapi call (`call.id` `01a09f01-98b8-7000-a2a4-5b285e8b0e8a`) on 2026-09-14 produced a `custom_llm_started` conditional marker, a `call_started` audit event, and multiple `turn_processed` audit events, all correctly scoped to tenant `client-acme` via `assistant.id` `c3a7e2f2-c6f9-419c-9cce-e0ce9d1076e6`. This was confirmed by querying `audit_events` directly against the live PostgreSQL database.

This confirms tenant resolution and the audit trail work end-to-end against a real Vapi call. It does **not** by itself confirm Groq's streamed response content, because `turn_processed` is audited before the Groq request is made; Groq access was verified separately (see above) once the model names were corrected.

