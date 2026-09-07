from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tenant_policy import read_policy_file, validate_registry


if len(sys.argv) != 2:
    raise SystemExit("Usage: python tools/validate_tenant_policy.py policies/tenants.json")

validate_registry(read_policy_file(sys.argv[1]))
print("Tenant policy is valid for deployment.")