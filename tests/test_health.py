from fastapi.testclient import TestClient

from app.main import app


def test_health_check_uses_standard_envelope() -> None:
    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["request_id"].startswith("req_")
    assert payload["data"]["status"] == "ok"
    assert payload["data"]["ai_mode"] == "mock"
    assert response.headers["X-Request-ID"] == payload["request_id"]
