from fastapi.testclient import TestClient

from app.main import app


def test_static_index_is_served_at_root() -> None:
    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert "淘到宝引擎" in response.text
    assert 'const API_BASE="/api/v1"' in response.text
    assert 'apiFetch("/customer-profiles?page=1&page_size=100")' in response.text
    assert 'apiFetch("/experiences?page=1&page_size=100")' in response.text
    assert 'apiFetch("/capabilities?page=1&page_size=100")' in response.text
    assert 'apiFetch("/sessions?page=1&page_size=100")' in response.text
    assert 'body:{content:query,mode:"quick"}' in response.text


def test_static_mount_does_not_shadow_api_routes() -> None:
    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"
