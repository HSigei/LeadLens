[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    .\.venv\Scripts\python.exe -m py_compile app.py core.py dashboard.py saas.py integrations.py billing.py knowledge.py worker.py aggregator.py tenant_policy.py
    .\.venv\Scripts\python.exe -m pytest tests -q
    .\.venv\Scripts\python.exe tools\validate_tenant_policy.py policies\tenants.example.json
    Write-Host "LeadLens checks passed." -ForegroundColor Green
}
finally { Pop-Location }