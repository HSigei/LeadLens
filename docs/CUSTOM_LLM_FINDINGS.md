# Custom LLM Diagnostic Findings

Date confirmed: 2026-09-13.

## Confirmed Request Shape

A real Vapi Custom LLM request includes `model`, `messages`, `temperature`, `max_tokens`, and `stream`. `stream` is `true` in real calls. The request also includes `call.id`, `assistant.id`, `assistantOverrides.variableValues`, and `metadata`.

`assistantOverrides.variableValues` is present but empty by default. `metadata` is present but empty and cannot be set from Vapi's assistant dashboard UI.

## Tenant Identification

Resolve the tenant from `assistant.id` through a tenant-policy mapping. The tenant policy schema needs a `vapi_assistant_id` field for this mapping. Do not derive tenant identity from message content.

## Interrupted Turns

Vapi can send multiple Custom LLM requests for one conversational turn when the caller continues speaking. In those requests, `numModelRequestInTurn` increments and `assistantTurnInterrupted` is `true`.

## Authentication

Vapi's Custom LLM Model card has no credential field. The diagnostic endpoint uses a constant-time path-secret check at:

```text
/custom-llm/{secret_key}/chat/completions
```

This avoids a query string because Vapi appends `/chat/completions` directly to the configured Custom LLM base URL.

## Environment Loading

`core.py` calls `load_dotenv()` before runtime configuration is read, so a bare Uvicorn process loads the local `.env` file. Docker Compose-provided variables remain authoritative because `load_dotenv()` does not override process environment variables by default.

Tests set `PYTHON_DOTENV_DISABLED=1` in `tests/conftest.py` so local `.env` values do not affect test configuration.
