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
