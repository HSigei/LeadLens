from __future__ import annotations

from collections import Counter
from typing import Any

from core import audit, calls_table, organizations_table, utc_now


def _phrases(call: dict[str, Any]) -> list[str]:
    return [turn["content"].strip() for turn in call.get("turns", []) if turn.get("role") == "user" and isinstance(turn.get("content"), str) and turn["content"].strip()]


def summarize_tenant_performance(tenant_id: str) -> dict[str, Any]:
    calls = [call for call in calls_table().query(tenant_id=tenant_id).get("Items", []) if call.get("status") == "completed"]
    resolved_phrases: Counter[str] = Counter()
    unresolved_phrases: Counter[str] = Counter()
    missed_opportunities: Counter[str] = Counter()
    training_recommendations: Counter[str] = Counter()
    bookings = 0
    for call in calls:
        analysis = call.get("analysis", {})
        target = resolved_phrases if analysis.get("issue_resolved") else unresolved_phrases
        target.update(_phrases(call))
        missed_opportunities.update(item for item in analysis.get("missed_opportunities", []) if isinstance(item, str))
        training_recommendations.update(item for item in analysis.get("training_recommendations", []) if isinstance(item, str))
        bookings += bool(call.get("booking_id"))
    return {
        "total_calls": len(calls),
        "booking_conversion_rate": round(bookings * 100 / len(calls), 1) if calls else 0,
        "resolved_phrases": resolved_phrases.most_common(3),
        "unresolved_phrases": unresolved_phrases.most_common(3),
        "missed_opportunities": missed_opportunities.most_common(3),
        "training_recommendations": training_recommendations.most_common(3),
    }


def generate_prompt_guidance(tenant_id: str) -> str:
    summary = summarize_tenant_performance(tenant_id)
    parts = []
    if summary["unresolved_phrases"]:
        phrase, count = summary["unresolved_phrases"][0]
        parts.append(f"Calls mentioning '{phrase}' were unresolved {count} time(s); clarify that topic early and offer a human handoff when needed.")
    if summary["training_recommendations"]:
        recommendation, _ = summary["training_recommendations"][0]
        parts.append(f"Prioritize this coaching pattern: {recommendation}.")
    if summary["missed_opportunities"]:
        opportunity, _ = summary["missed_opportunities"][0]
        parts.append(f"When appropriate, do not miss this opportunity: {opportunity}.")
    if not parts:
        parts.append("No recurring call pattern is available yet; continue following the approved workflow and escalate when appropriate.")
    guidance = " ".join(parts)
    organizations_table().update_item(Key={"tenant_id": tenant_id}, UpdateExpression="SET prompt_guidance=:guidance, prompt_guidance_updated_at=:updated", ExpressionAttributeValues={":guidance": guidance, ":updated": utc_now()})
    audit(tenant_id, "learning", "prompt_guidance_generated", detail={"completed_calls": summary["total_calls"]})
    return guidance