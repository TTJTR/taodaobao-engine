import uuid

from sqlalchemy import inspect

from app.db.models import PresentationRun, ResearchTask, ResearchTaskStatus
from app.services.presentation_service import PresentationService


def _research_task() -> ResearchTask:
    return ResearchTask(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        customer_profile_id=uuid.uuid4(),
        created_by_id=uuid.uuid4(),
        title="汽车质量知识闭环研究",
        question="如何完成 90 天试点？",
        completion_conditions=[],
        status=ResearchTaskStatus.COMPLETED,
        stage="completed",
        progress=100,
        trace_id="trace-research",
        profile_snapshot={},
        conversation_snapshot=[],
        findings=[
            {
                "finding_id": "F1",
                "text": "已有项目采用只读接口与边缘采集建立质量履历。",
                "boundary": "historical_fact",
            }
        ],
        evidence_snapshot={
            "experiences": [
                {
                    "source_id": "source-1",
                    "data": {
                        "source_quote": "通过只读接口获取工单与批次信息。",
                        "source_anchor": "实施范围",
                    },
                    "source_snapshot": {
                        "title": "质量追溯项目复盘",
                        "url": "https://example.test/doc/1",
                        "author": "项目经理",
                        "source_version": "3",
                    },
                }
            ],
            "capabilities": [],
        },
        research_plan={},
        routes=[],
        audit={"passed": True},
        knowledge_gaps=[],
        expert_questions=[],
        report={"executive_summary": "建议从两条产线启动 90 天试点。"},
        retry_count=0,
    )


def test_presentation_model_accepts_exactly_one_research_upstream() -> None:
    columns = inspect(PresentationRun).columns
    assert columns.solution_run_id.nullable is True
    assert columns.research_task_id.nullable is True
    assert columns.upstream_fingerprint.nullable is True
    constraints = {item.name for item in inspect(PresentationRun).local_table.constraints}
    assert "ck_presentation_exactly_one_upstream" in constraints


def test_research_snapshot_uses_findings_and_source_evidence() -> None:
    task = _research_task()

    claims = PresentationService._research_claims(task)
    evidence = PresentationService._research_evidence(task)

    assert claims == [
        {
            "claim_id": "F1",
            "text": "已有项目采用只读接口与边缘采集建立质量履历。",
            "boundary": "historical_fact",
            "risk_level": "medium",
        }
    ]
    assert evidence[0]["source_title"] == "质量追溯项目复盘"
    assert evidence[0]["source_version"] == "3"
    assert evidence[0]["quote"] == "通过只读接口获取工单与批次信息。"
    assert len(PresentationService._research_fingerprint(task)) == 64


def test_frontend_restores_quick_result_and_routes_html_from_research() -> None:
    html = open("static/index.html", encoding="utf-8").read()

    assert "session.result=await solutionResult(run,session.query)" in html
    assert "/research-tasks/${taskId}/interactive-presentations" in html
    assert "/research-tasks/${taskId}/interactive-presentations/latest" in html
    assert "open-research-presentation" in html
    assert "可用于演示的研究任务" in html
    assert 'existing?.status==="ready"?"preview":"generate"' in html
    assert "CONFIG.apiBase" not in html
    assert "`${API_BASE}/presentations/${encodeURIComponent(run.id)}/artifact`" in html
    conversation = html.split("function renderConversation", 1)[1].split("function", 1)[0]
    assert "renderPresentationEntry" not in conversation
