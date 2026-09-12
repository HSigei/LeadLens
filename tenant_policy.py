from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from core import validate_tenant_compliance


POLICY_VERSION = 1
REQUIRED_APPROVALS = ("approved_by", "approved_at", "data_protection_contact")


def read_policy_file(path: str) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read tenant policy file: {error}") from error


def policy_registry() -> dict[str, Any]:
    path = os.getenv("TENANT_POLICY_FILE")
    registry = read_policy_file(path) if path else json.loads(os.getenv("TENANT_ROUTING_JSON", "{}"))
    if "tenants" not in registry:
        registry = {"version": POLICY_VERSION, "tenants": registry}
    if registry.get("version") != POLICY_VERSION or not isinstance(registry.get("tenants"), dict):
        raise ValueError("Tenant policy must contain version 1 and a tenants object.")
    return registry


def validate_registry(registry: dict[str, Any]) -> None:
    if registry.get("version") != POLICY_VERSION or not registry.get("tenants"):
        raise ValueError("Tenant policy requires version 1 and at least one tenant mapping.")
    for number, tenant in registry["tenants"].items():
        if not number.startswith("+") or not isinstance(tenant, dict):
            raise ValueError("Each tenant mapping needs an E.164 phone number and an object policy.")
        validate_tenant_compliance(tenant)
        missing = [field for field in REQUIRED_APPROVALS if not tenant.get(field)]
        if missing:
            raise ValueError(f"{number} is missing documented approvals: {', '.join(missing)}.")
        validate_analysis_fields(tenant.get("analysis_fields", []))
        validate_booking_config(tenant)


def validate_analysis_fields(analysis_fields: Any) -> None:
    if not isinstance(analysis_fields, list):
        raise ValueError("analysis_fields must be a list.")
    for field in analysis_fields:
        if not isinstance(field, dict) or not field.get("name") or field.get("type", "text") not in {"boolean", "text"}:
            raise ValueError("Each analysis_fields entry needs a 'name' and a type of 'boolean' or 'text'.")


def validate_booking_config(tenant: dict[str, Any]) -> None:
    provider = tenant.get("booking_provider")
    if provider is None:
        return
    if provider != "calcom":
        raise ValueError("booking_provider must be 'calcom'.")
    if not tenant.get("booking_event_type_id"):
        raise ValueError("Cal.com booking requires booking_event_type_id.")
    api_key_env = tenant.get("booking_api_key_env")
    if not isinstance(api_key_env, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", api_key_env):
        raise ValueError("Cal.com booking requires a valid booking_api_key_env.")


def tenant_for_number(number: str) -> dict[str, str]:
    try:
        registry = policy_registry()
        validate_registry(registry)
    except ValueError as error:
        raise HTTPException(503, f"Tenant policy is invalid: {error}") from error
    tenant = registry["tenants"].get(number)
    if not tenant:
        raise HTTPException(404, "No active tenant is configured for this number.")
    return tenant


def tenant_by_id(tenant_id: str) -> dict[str, Any] | None:
    try:
        registry = policy_registry()
        validate_registry(registry)
    except ValueError:
        return None
    return next((tenant for tenant in registry["tenants"].values() if tenant.get("tenant_id") == tenant_id), None)