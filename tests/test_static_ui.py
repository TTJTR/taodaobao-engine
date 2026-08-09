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
    assert 'apiFetch("/research-tasks?page=1&page_size=100")' in response.text
    assert 'apiFetch("/expert-collaborations?page=1&page_size=100")' in response.text
    assert 'apiFetch("/auth/invitation/verify"' in response.text
    assert 'data-action="verify-invitation"' in response.text
    assert "function verifyInvitationAndContinue()" in response.text
    assert "邀请码无效、已使用或已过期" in response.text
    assert "function createIdempotencyKey()" in response.text
    assert 'headers["Idempotency-Key"]=createIdempotencyKey()' in response.text
    assert 'apiFetch(`/feishu/resources?${query}`)' in response.text
    assert 'AI ${s.runtime.ai_mode==="live"?"真实":"模拟"}' in response.text
    assert '飞书 ${s.runtime.feishu_mode==="live"?"真实":"模拟"}' in response.text
    assert 'source.is_demo?"（演示数据）"' in response.text
    assert "TAO2026" not in response.text
    assert "制造业智能质检PRD v3.1" not in response.text
    assert "项目交付风险复盘" not in response.text
    assert "example.feishu.cn/docx/demoToken" not in response.text
    assert "async function bootstrap()" in response.text
    assert "if(Store.state.loggedIn){try{await loadWorkspace()}" in response.text
    assert "function renderDeep()" in response.text
    assert "function renderExperts()" in response.text
    assert "V1.0 规划能力，不进入当前MVP主流程" not in response.text


def test_static_mount_does_not_shadow_api_routes() -> None:
    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"
