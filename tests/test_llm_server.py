import json

from fastapi.testclient import TestClient

import app
import llm_server


def test_custom_llm_diagnostic_captures_request_and_returns_completion(monkeypatch, tmp_path):
    capture_file = tmp_path / "vapi_request_capture.jsonl"
    monkeypatch.setattr(llm_server, "CAPTURE_FILE", capture_file)
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "test-secret")

    response = TestClient(app.app).post(
        "/custom-llm/test-secret/chat/completions",
        headers={"Authorization": "Bearer diagnostic-key", "X-Vapi-Test": "true"},
        json={"model": "custom", "messages": [{"role": "user", "content": "Hello"}], "stream": False},
    )

    assert response.status_code == 200
    assert response.json()["object"] == "chat.completion"
    assert response.json()["choices"][0]["message"] == {"role": "assistant", "content": "Hello, this is a test response."}
    captured = json.loads(capture_file.read_text(encoding="utf-8"))
    assert captured["headers"]["authorization"] == "Bearer diagnostic-key"
    assert captured["body"] == {"model": "custom", "messages": [{"role": "user", "content": "Hello"}], "stream": False}


def test_custom_llm_diagnostic_rejects_invalid_or_missing_secret_before_capture(monkeypatch, tmp_path):
    capture_file = tmp_path / "vapi_request_capture.jsonl"
    monkeypatch.setattr(llm_server, "CAPTURE_FILE", capture_file)
    monkeypatch.setenv("CUSTOM_LLM_API_KEY", "test-secret")
    client = TestClient(app.app)

    invalid = client.post("/custom-llm/wrong-secret/chat/completions", json={"messages": []})
    missing = client.post("/custom-llm/chat/completions", json={"messages": []})

    assert invalid.status_code == 401
    assert missing.status_code == 404
    assert not capture_file.exists()