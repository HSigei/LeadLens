# Testing

## Local Checks

Install development dependencies and run the suite:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m py_compile app.py worker.py aggregator.py dashboard.py saas.py integrations.py billing.py knowledge.py core.py tenant_policy.py
```

Validate a tenant policy independently:

```powershell
.\.venv\Scripts\python.exe tools\validate_tenant_policy.py policies\tenants.json
```

## Test Scope

The current automated suite covers PII masking, analysis schema validation, and tenant-bearing JWT validation. CI also compiles application modules and builds the Docker image on GitHub-hosted runners.

`pytest.ini` filters one known deprecation emitted by Starlette's `TestClient` against AnyIO. It does not suppress warnings emitted by application modules.

Provider integration tests must use non-production resources: a Twilio subaccount, sandbox tenant policy, separate AWS account, test CRM tenant, Stripe test mode, and non-production identity provider.

## Load Smoke Test

The included probe targets `/healthz` only and deliberately does not initiate calls:

```powershell
$env:LOAD_TEST_TOKEN = "approved-sandbox-token"
.\.venv\Scripts\python.exe tests\load\webhook_smoke.py https://sandbox.example.com 25
```

Do not load-test real customer phone numbers, recordings, CRM accounts, or production infrastructure without approved test plans and written authorization.