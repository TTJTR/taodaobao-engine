import io
import os
import uuid
import zipfile

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import (
    CustomerProfile,
    ProfileStatus,
    ResearchTask,
    ResearchTaskStatus,
    ResponseEvidenceStatus,
    User,
)
from app.schemas.v2 import ManualIntelligenceSource
from app.services.intelligence_service import IntelligenceService, validate_public_source_url
from app.services.rehearsal_service import RehearsalService, evaluate_answer
from app.services.runtime_service import RuntimeService
from app.services.tender_service import TenderService, extract_tender_text, split_requirements

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


def test_public_source_url_blocks_local_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.intelligence_service.socket.getaddrinfo",
        lambda *_: [(None, None, None, None, ("127.0.0.1", 0))],
    )
    with pytest.raises(Exception, match="内网"):
        validate_public_source_url("https://example.test/internal")


def test_tender_docx_extraction_and_requirement_split() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="urn:test"><w:p><w:t>系统必须支持私有化部署。</w:t></w:p>'
            "<w:p><w:t>须提供三年成功案例。</w:t></w:p></w:document>",
        )
    import base64

    content = extract_tender_text(
        pasted_text=None,
        content_base64=base64.b64encode(buffer.getvalue()).decode(),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert "私有化部署" in content
    assert len(split_requirements(content)) == 2


def test_rehearsal_evaluation_flags_overcommitment() -> None:
    result = evaluate_answer("我们保证百分之百成功，完全没有风险。", {"knowledge_gaps": ["预算"]})
    assert result["score"] < 60
    assert {item["type"] for item in result["issues"]} >= {
        "overcommitment",
        "evidence_boundary",
        "pending_confirmation",
    }


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured")
@pytest.mark.asyncio
async def test_v2_intelligence_tender_rehearsal_and_runtime_end_to_end() -> None:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    workspace_id = uuid.uuid4()

    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE users CASCADE"))

    async with factory() as session:
        user = User(
            workspace_id=workspace_id,
            feishu_user_id="v2-e2e-user",
            name="V2 验收用户",
        )
        session.add(user)
        await session.flush()
        profile = CustomerProfile(
            workspace_id=workspace_id,
            customer_name="示例客户",
            profile={"industry": "零售", "goals": ["私有化部署"]},
            status=ProfileStatus.CONFIRMED,
            confirmed_by_id=user.id,
        )
        session.add(profile)
        await session.flush()
        research = ResearchTask(
            workspace_id=workspace_id,
            customer_profile_id=profile.id,
            created_by_id=user.id,
            title="私有化方案研究",
            question="如何完成私有化部署？",
            completion_conditions=[],
            status=ResearchTaskStatus.COMPLETED,
            stage="completed",
            progress=100,
            trace_id=str(uuid.uuid4()),
            profile_snapshot=profile.profile,
            conversation_snapshot=[],
            evidence_snapshot={"items": []},
            findings=[],
            routes=[],
            knowledge_gaps=["三年案例仍待确认"],
            expert_questions=[],
            report={"summary": "需要补充案例依据"},
        )
        session.add(research)
        await session.commit()

        intelligence = IntelligenceService(session, workspace_id, user.id)
        search_run = await intelligence.create_run(
            query="示例客户公开招聘信息",
            purpose="customer_profile",
            provider="manual",
            sources=[
                ManualIntelligenceSource(
                    title="示例客户招聘页",
                    source_url="https://example.com/jobs",
                    content="公开页面显示该公司正在招聘云平台工程师。",
                )
            ],
        )
        items, total = await intelligence.list_items(1, 20, search_run.id)
        assert total == 1
        snapshot = await intelligence.create_snapshot("customer_profile", [items[0].id])
        proposal = await intelligence.create_profile_proposal(
            profile.id,
            snapshot.id,
            {"hiring_signals": ["云平台工程师"]},
        )
        proposal = await intelligence.decide_proposal(
            profile.id, proposal.id, accept=True, note="人工核对公开页面"
        )
        assert proposal.status.value == "accepted"
        await session.refresh(profile)
        assert profile.status == ProfileStatus.PENDING_CONFIRMATION
        assert profile.profile["external_intelligence"]["hiring_signals"] == ["云平台工程师"]

        tenders = TenderService(session, workspace_id, user.id)
        tender = await tenders.create_tender(
            title="私有化项目招标要求",
            customer_profile_id=profile.id,
            pasted_text="系统必须支持私有化部署。\n供应商必须提供三年成功案例。",
            content_base64=None,
            filename=None,
            mime_type=None,
        )
        requirements = await tenders.list_requirements(tender.id)
        assert len(requirements) == 2
        matrix = await tenders.create_matrix(tender.id, [], [], snapshot.id)
        _, matrix_items = await tenders.get_matrix(matrix.id)
        assert {item.evidence_status for item in matrix_items} == {
            ResponseEvidenceStatus.MISSING_EVIDENCE
        }
        assert all("暂不作能力承诺" in item.response_text for item in matrix_items)

        rehearsals = RehearsalService(session, workspace_id, user.id)
        rehearsal = await rehearsals.create(
            customer_profile_id=profile.id,
            title="客户异议演练",
            solution_run_id=None,
            research_task_id=research.id,
            intelligence_snapshot_id=snapshot.id,
            response_matrix_id=matrix.id,
            role="challenger",
            difficulty="hard",
            focus_areas=["成功案例"],
            max_turns=3,
        )
        await rehearsals.start(rehearsal.id)
        answered, next_turn = await rehearsals.submit_turn(
            rehearsal.id,
            "根据当前研究，私有化方向可讨论，但三年案例尚无企业依据，需要补充确认。",
        )
        assert answered.evaluation["score"] >= 70
        assert next_turn is not None
        report = await rehearsals.complete(rehearsal.id)
        assert report.report_data["turn_count"] == 1

        runtime_rows, total = await RuntimeService(session, workspace_id).list_tasks(
            page=1, page_size=50, task_type=None, status=None
        )
        assert total >= 3
        assert {row["task_type"] for row in runtime_rows} >= {
            "deep_research",
            "intelligence",
            "rehearsal",
        }

    await engine.dispose()
