from fastapi.testclient import TestClient

import app


def test_hosted_setup_page_renders():
    response = TestClient(app.app).get("/")
    assert response.status_code == 200
    assert "LeadLens" in response.text


def test_health_endpoint_is_available_without_configuration():
    response = TestClient(app.app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_security_headers_are_applied():
    response = TestClient(app.app).get("/healthz")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]