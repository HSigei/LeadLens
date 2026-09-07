from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import audit, organizations_table, utc_now
from dashboard import principal


router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])
Provider = Literal["twilio", "hubspot", "salesforce", "zoho", "stripe"]


class OrganizationProfile(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    jurisdiction: str = Field(pattern=r"^[A-Z]{2}(?:-[A-Z]{2})?$")
    speech_language: str = Field(default="en-US", pattern=r"^[a-z]{2}-[A-Z]{2}$")
    privacy_contact: str = Field(min_length=5, max_length=254)


class ConnectionRequest(BaseModel):
    provider: Provider
    connection_id: str = Field(min_length=4, max_length=160)
    scopes: list[str] = Field(default_factory=list, max_length=20)


def admin(user: dict[str, str] = Depends(principal)) -> dict[str, str]:
    if user["role"] != "admin":
        raise HTTPException(403, "Organization administrator access is required.")
    return user


@router.get("/status")
def status(user: dict[str, str] = Depends(admin)) -> dict:
    organization = organizations_table().get_item(Key={"tenant_id": user["tenant_id"]}).get("Item", {})
    connections = organization.get("connections", {})
    checks = {
        "organization_profile": bool(organization.get("name") and organization.get("privacy_contact")),
        "privacy_policy": bool(organization.get("privacy_approved_at")),
        "voice_connection": "twilio" in connections,
        "crm_connection": any(provider in connections for provider in ("hubspot", "salesforce", "zoho")),
        "billing_connection": "stripe" in connections,
    }
    return {"organization": organization, "checks": checks, "ready": all(checks.values())}


@router.put("/organization")
def save_organization(profile: OrganizationProfile, user: dict[str, str] = Depends(admin)) -> dict:
    record = {"tenant_id": user["tenant_id"], **profile.model_dump(), "updated_at": utc_now()}
    organizations_table().update_item(Key={"tenant_id": user["tenant_id"]}, UpdateExpression="SET #name=:name, jurisdiction=:jurisdiction, speech_language=:language, privacy_contact=:contact, updated_at=:updated", ExpressionAttributeNames={"#name": "name"}, ExpressionAttributeValues={":name": record["name"], ":jurisdiction": record["jurisdiction"], ":language": record["speech_language"], ":contact": record["privacy_contact"], ":updated": record["updated_at"]})
    audit(user["tenant_id"], user["sub"], "organization_profile_updated")
    return record


@router.post("/privacy-approval")
def approve_privacy(user: dict[str, str] = Depends(admin)) -> dict[str, str]:
    organizations_table().update_item(Key={"tenant_id": user["tenant_id"]}, UpdateExpression="SET privacy_approved_at=:time, privacy_approved_by=:actor", ExpressionAttributeValues={":time": utc_now(), ":actor": user["sub"]})
    audit(user["tenant_id"], user["sub"], "privacy_policy_approved")
    return {"status": "recorded"}


@router.post("/connections")
def register_connection(request: ConnectionRequest, user: dict[str, str] = Depends(admin)) -> dict[str, str]:
    organizations_table().update_item(Key={"tenant_id": user["tenant_id"]}, UpdateExpression="SET connections.#provider=:connection", ExpressionAttributeNames={"#provider": request.provider}, ExpressionAttributeValues={":connection": {"connection_id": request.connection_id, "scopes": request.scopes, "status": "connected", "connected_at": utc_now()}})
    audit(user["tenant_id"], user["sub"], "provider_connection_registered", detail={"provider": request.provider})
    return {"status": "connected", "provider": request.provider}