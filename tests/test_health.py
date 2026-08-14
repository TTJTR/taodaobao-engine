import httpx
from fastapi.testclient import TestClient

from app.main import app


def test_health_check_uses_standard_envelope(monkeypatch) -> None:
    from app.api.v1.routes import health

    monkeypatch.setattr(health.settings, "open_enrich_svc_url", None)
    monkeypatch.setattr(health.settings, "database_url", None)
    monkeypatch.setattr(health, "bailian_is_configured", lambda: False)
    monkeypatch.setattr(health.settings, "ai_mode", "live")
    monkeypatch.setattr(health.settings, "feishu_mode", "live")
    monkeypatch.setattr(health.settings, "presentation_mode", "live")
    monkeypatch.setattr(health.settings, "interactive_html_mode", "live")
    monkeypatch.setattr(health.settings, "presentation_service_url", None)
    monkeypatch.setattr(health.settings, "interactive_html_api_key", None)
    monkeypatch.setattr(health.settings, "feishu_app_id", None)
    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["request_id"].startswith("req_")
    assert payload["data"]["status"] == "degraded"
    assert payload["data"]["database"] == "not_configured"
    assert payload["data"]["ai"] == "not_configured"
    assert payload["data"]["ai_mode"] == "live"
    assert payload["data"]["feishu"] == "not_configured"
    assert payload["data"]["feishu_mode"] == "live"
    assert payload["data"]["interactive_html"] == "not_configured"
    assert payload["data"]["interactive_html_mode"] == "live"
    assert payload["data"]["sidecar_status"] == "not_configured"
    assert response.headers["X-Request-ID"] == payload["request_id"]


def test_health_reports_sidecar_ok_without_exposing_its_url(monkeypatch) -> None:
    from app.api.v1.routes import health

    monkeypatch.setattr(health.settings, "open_enrich_svc_url", "http://sidecar.internal:8090")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/healthz"
        return httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(
        health,
        "_sidecar_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    payload = TestClient(app).get("/api/v1/health").json()["data"]

    assert payload["sidecar_status"] == "ok"
    assert "sidecar.internal" not in str(payload)


def test_health_reports_sidecar_unreachable_without_error_details(monkeypatch) -> None:
    from app.api.v1.routes import health

    monkeypatch.setattr(health.settings, "open_enrich_svc_url", "http://sidecar.internal:8090")

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("internal address must stay private", request=request)

    monkeypatch.setattr(
        health,
        "_sidecar_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    payload = TestClient(app).get("/api/v1/health").json()["data"]

    assert payload["sidecar_status"] == "unreachable"
    assert "sidecar.internal" not in str(payload)
    assert "internal address" not in str(payload)
