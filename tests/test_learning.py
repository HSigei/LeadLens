from types import SimpleNamespace

import learning


def test_learning_summarizes_patterns_and_stores_guidance(monkeypatch):
    calls = [
        {"status": "completed", "turns": [{"role": "user", "content": "What is the warranty?"}], "analysis": {"issue_resolved": False, "missed_opportunities": ["offer a callback"], "training_recommendations": ["clarify warranty terms"]}},
        {"status": "completed", "turns": [{"role": "user", "content": "What is the warranty?"}], "analysis": {"issue_resolved": False, "missed_opportunities": ["offer a callback"], "training_recommendations": ["clarify warranty terms"]}, "booking_id": "booking-1"},
        {"status": "completed", "turns": [{"role": "user", "content": "Can I book?"}], "analysis": {"issue_resolved": True, "missed_opportunities": [], "training_recommendations": []}},
    ]
    updates = []
    monkeypatch.setattr(learning, "calls_table", lambda: SimpleNamespace(query=lambda **kwargs: {"Items": calls}))
    monkeypatch.setattr(learning, "organizations_table", lambda: SimpleNamespace(update_item=lambda **kwargs: updates.append(kwargs)))
    monkeypatch.setattr(learning, "audit", lambda *args, **kwargs: None)

    summary = learning.summarize_tenant_performance("tenant-a")
    assert summary["booking_conversion_rate"] == 33.3
    assert summary["unresolved_phrases"] == [("What is the warranty?", 2)]
    guidance = learning.generate_prompt_guidance("tenant-a")
    assert "warranty" in guidance
    assert "clarify warranty terms" in guidance
    assert updates[0]["ExpressionAttributeValues"][":guidance"] == guidance
