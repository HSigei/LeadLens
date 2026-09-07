from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import boto3
import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Query

from core import audit, env, jwt_signing_secret, organizations_table
from dashboard import principal

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

OAUTH = {
    "hubspot": ("https://app.hubspot.com/oauth/authorize", "https://api.hubapi.com/oauth/v1/token"),
    "salesforce": ("https://login.salesforce.com/services/oauth2/authorize", "https://login.salesforce.com/services/oauth2/token"),
    "zoho": ("https://accounts.zoho.com/oauth/v2/auth", "https://accounts.zoho.com/oauth/v2/token"),
}


def admin(user: dict[str, str] = Depends(principal)) -> dict[str, str]:
    if user["role"] != "admin":
        raise HTTPException(403, "Organization administrator access is required.")
    return user


def oauth_setting(provider: str, name: str) -> str:
    return env(f"{provider.upper()}_OAUTH_{name}")


def callback_url(provider: str) -> str:
    return f"{env('PUBLIC_BASE_URL').rstrip('/')}/api/integrations/{provider}/callback"


@router.get("/{provider}/authorize")
def authorize(provider: str, user: dict[str, str] = Depends(admin)) -> dict[str, str]:
    if provider not in OAUTH:
        raise HTTPException(404, "Unsupported OAuth provider.")
    state = jwt.encode({"tenant_id": user["tenant_id"], "sub": user["sub"], "provider": provider, "aud": "callsignal-oauth", "exp": datetime.now(UTC) + timedelta(minutes=10)}, jwt_signing_secret(), algorithm="HS256")
    params = {"client_id": oauth_setting(provider, "CLIENT_ID"), "redirect_uri": callback_url(provider), "response_type": "code", "state": state}
    if provider == "hubspot":
        params["scope"] = "crm.objects.contacts.read crm.objects.contacts.write"
    if provider == "zoho":
        params.update({"scope": "ZohoCRM.modules.ALL", "access_type": "offline"})
    return {"authorization_url": f"{OAUTH[provider][0]}?{urlencode(params)}"}


@router.get("/{provider}/callback")
def callback(provider: str, code: str = Query(min_length=1), state: str = Query(min_length=1)) -> dict[str, str]:
    if provider not in OAUTH:
        raise HTTPException(404, "Unsupported OAuth provider.")
    try:
        claims = jwt.decode(state, jwt_signing_secret(), algorithms=["HS256"], audience="callsignal-oauth")
    except jwt.PyJWTError as error:
        raise HTTPException(401, "OAuth state is invalid or expired.") from error
    if claims.get("provider") != provider:
        raise HTTPException(400, "OAuth provider does not match state.")
    response = httpx.post(OAUTH[provider][1], data={"grant_type": "authorization_code", "client_id": oauth_setting(provider, "CLIENT_ID"), "client_secret": oauth_setting(provider, "CLIENT_SECRET"), "redirect_uri": callback_url(provider), "code": code}, timeout=30)
    response.raise_for_status()
    secret_name = f"{env('CONNECTION_SECRET_PREFIX').rstrip('/')}/{claims['tenant_id']}/{provider}"
    secret = boto3.client("secretsmanager", region_name=env("AWS_REGION"))
    try:
        result = secret.create_secret(Name=secret_name, SecretString=response.text)
    except secret.exceptions.ResourceExistsException:
        result = secret.put_secret_value(SecretId=secret_name, SecretString=response.text)
        result["ARN"] = secret.describe_secret(SecretId=secret_name)["ARN"]
    organizations_table().update_item(Key={"tenant_id": claims["tenant_id"]}, UpdateExpression="SET connections.#provider=:connection", ExpressionAttributeNames={"#provider": provider}, ExpressionAttributeValues={":connection": {"secret_arn": result["ARN"], "status": "connected", "connected_at": datetime.now(UTC).isoformat()}})
    audit(claims["tenant_id"], claims["sub"], "oauth_connected", detail={"provider": provider})
    return {"status": "connected", "provider": provider}