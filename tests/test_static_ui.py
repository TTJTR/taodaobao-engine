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
    assert "confirm-collaboration-button" in response.text
    assert "syncCollaborationConfirmButton" in response.text
    assert "Services.listExperiences(),Services.listCapabilities()" in response.text
    assert "retry-source" in response.text
    assert 'apiFetch("/auth/invitation/verify"' in response.text
    assert 'data-action="verify-invitation"' in response.text
    assert "function verifyInvitationAndContinue()" in response.text
    assert "邀请码无效、已使用或已过期" in response.text
    assert "function createIdempotencyKey()" in response.text
    assert 'headers["Idempotency-Key"]=createIdempotencyKey()' in response.text
    assert "apiFetch(`/feishu/resources?${query}`)" in response.text
    assert "function runtimeLabel(status)" in response.text
    assert "AI ${runtimeLabel(s.runtime.ai)}" in response.text
    assert "飞书 ${runtimeLabel(s.runtime.feishu)}" in response.text
    assert "前端已停止渲染，不会使用固定文案补齐结果" in response.text
    assert "当前页面全部为结构演示数据" not in response.text
    assert "reset-demo" not in response.text
    assert 'id="calm-pet"' not in response.text
    assert "const CalmPet" not in response.text
    assert "功德＋1" not in response.text
    assert "某智能制造集团" not in response.text
    assert 'source.is_demo?"（演示数据）"' in response.text
    assert "TAO2026" not in response.text
    assert "制造业智能质检PRD v3.1" not in response.text
    assert "项目交付风险复盘" not in response.text
    assert "example.feishu.cn/docx/demoToken" not in response.text
    assert "async function bootstrap()" in response.text
    assert "if(Store.state.loggedIn){try{await loadWorkspace()}" in response.text
    assert "function renderDeep()" in response.text
    assert "function renderExperts()" in response.text
    assert "function renderPresentations()" in response.text
    assert "/interactive-presentations" in response.text
    assert 'sandbox="allow-scripts"' in response.text
    assert "互动 HTML 参数" in response.text
    assert "无需上传参考稿" in response.text
    assert "新窗口全屏打开" in response.text
    assert 'data-view="intelligence"' in response.text
    assert 'data-view="rehearsals"' in response.text
    assert 'data-view="about"' in response.text
    assert "V2Integrated.renderBusiness()" in response.text
    assert "V2Integrated.renderRehearsals()" in response.text
    assert "function renderRuntime()" in response.text
    assert "function renderModels()" in response.text
    assert "apiFetch(`/style-profiles/${id}`)" in response.text
    assert "V1.0 规划能力，不进入当前MVP主流程" not in response.text


def test_truthful_youthful_shell_styles_are_served() -> None:
    response = TestClient(app).get("/app-shell-v3.css")

    assert response.status_code == 200
    assert "TRUSTED PRESALES" not in response.text
    assert ".home-hero" in response.text
    assert ".journey-grid" in response.text


def test_static_mount_does_not_shadow_api_routes() -> None:
    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["data"]["status"] in {"ok", "degraded"}


def test_style_profile_polling_route_is_registered() -> None:
    schema = app.openapi()

    assert "get" in schema["paths"]["/api/v1/style-profiles/{profile_id}"]


def test_interactive_presentation_incremental_route_is_registered() -> None:
    schema = app.openapi()
    path = "/api/v1/solution-runs/{run_id}/interactive-presentations"

    assert "post" in schema["paths"][path]
    request_schema = schema["components"]["schemas"]["CreateInteractivePresentationRequest"]
    assert "style_profile_id" not in request_schema.get("required", [])
    assert "get" in schema["paths"]["/api/v1/presentations/{presentation_id}/artifact"]


def test_v2_business_workbenches_are_served_with_real_api_contracts() -> None:
    client = TestClient(app)

    intelligence = client.get("/v2_intelligence.html")
    tender = client.get("/v2_tender.html")
    adapter = client.get("/v2-workbench.js")
    integrated = client.get("/v2-integrated.js")
    index = client.get("/")

    assert intelligence.status_code == 200
    assert "情报工作台" in intelligence.text
    assert "/intelligence/items" in intelligence.text
    assert "intelligence-proposals" in intelligence.text
    assert tender.status_code == 200
    assert "招标工作台" in tender.text
    assert "/response-matrices/" in tender.text
    assert "/review" in tender.text
    assert "/batch-review" in tender.text
    assert "/export" in tender.text
    assert "批量补充证据" in tender.text
    assert 'headers["Idempotency-Key"]' in adapter.text
    assert integrated.status_code == 200
    assert "/v2_intelligence.html?embedded=1" in integrated.text
    assert "/v2_tender.html?embedded=1" in integrated.text
    assert 'request("/rehearsals?page=1&page_size=100")' in integrated.text
    assert 'request(`/rehearsals/${item.id}/start`' in integrated.text
    assert "磋商前 · 识别机会" in integrated.text
    assert "关于我们" in index.text


def test_profile_intelligence_proposals_have_a_read_route() -> None:
    schema = app.openapi()
    path = "/api/v1/customer-profiles/{profile_id}/intelligence-proposals"

    assert "get" in schema["paths"][path]
    assert "post" in schema["paths"][path]
