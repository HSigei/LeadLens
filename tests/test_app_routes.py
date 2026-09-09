import logging

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


def test_request_id_is_returned_and_logged_without_pii(caplog):
    with caplog.at_level(logging.INFO):
        response = TestClient(app.app).get(
            "/healthz",
            headers={"X-Request-ID": "request-test-123"},
        )

    assert response.headers["x-request-id"] == "request-test-123"
    records = [record for record in caplog.records if record.getMessage() == "request.complete"]
    assert records
    assert records[-1].request_id == "request-test-123"
    assert "jane@example.com" not in str(records[-1].__dict__)
    assert "212-555-0199" not in str(records[-1].__dict__)