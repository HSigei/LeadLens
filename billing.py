from __future__ import annotations

import os

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request

from core import audit, organizations_table
from dashboard import principal

router = APIRouter(prefix="/api/billing", tags=["billing"])


def admin(user: dict[str, str] = Depends(principal)) -> dict[str, str]:
    if user["role"] != "admin":
        raise HTTPException(403, "Organization administrator access is required.")
    return user


@router.post("/checkout")
def checkout(user: dict[str, str] = Depends(admin)) -> dict[str, str]:
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
    if not stripe.api_key or not os.getenv("STRIPE_PRICE_ID"):
        raise HTTPException(503, "Billing is not configured.")
    session = stripe.checkout.Session.create(mode="subscription", line_items=[{"price": os.environ["STRIPE_PRICE_ID"], "quantity": 1}], client_reference_id=user["tenant_id"], success_url=f"{os.environ['PUBLIC_BASE_URL'].rstrip('/')}/?billing=success", cancel_url=f"{os.environ['PUBLIC_BASE_URL'].rstrip('/')}/?billing=cancel")
    audit(user["tenant_id"], user["sub"], "billing_checkout_started")
    return {"checkout_url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request) -> dict[str, str]:
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload, request.headers.get("Stripe-Signature", ""), os.environ["STRIPE_WEBHOOK_SECRET"])
    except (ValueError, stripe.error.SignatureVerificationError) as error:
        raise HTTPException(400, "Invalid Stripe webhook.") from error
    if event["type"] in {"checkout.session.completed", "customer.subscription.updated", "customer.subscription.deleted"}:
        data = event["data"]["object"]
        tenant_id = data.get("client_reference_id") or data.get("metadata", {}).get("tenant_id")
        if tenant_id:
            organizations_table().update_item(Key={"tenant_id": tenant_id}, UpdateExpression="SET billing_status=:status", ExpressionAttributeValues={":status": data.get("status", "active")})
            audit(tenant_id, "stripe", "billing_status_updated", detail={"event": event["type"]})
    return {"status": "received"}