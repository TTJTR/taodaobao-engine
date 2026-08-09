import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ai import MockAIEngine
from app.db.models import (
    AIRunRecord,
    ClaimEvidenceLink,
    ClaimRecord,
    CustomerProfile,
    EvidenceRecord,
    ExportArtifact,
    ExportStatus,
    HtmlArtifact,
    HumanReviewRecord,
    Message,
    MessageRole,
    PresentationRun,
    PresentationStatus,
    ProfileStatus,
    Session,
    SolutionRun,
    Source,
    SourceFreshness,
    SourcePurpose,
    SourceStatus,
    SourceType,
    StyleProfile,
    StyleProfileStatus,
    TrustAction,
    TrustDecisionRecord,
    User,
    VerificationLabel,
    WorkflowTask,
    WorkflowTaskStatus,
)
from app.integrations.presentation import MockPresentationProvider
from app.schemas.presentations import (
    CreatePresentationRequest,
    CreateReferenceDeckRequest,
    ExportPresentationRequest,
    GenerateStyleProfileRequest,
    UpdateStyleProfileRequest,
)
from app.schemas.trust import TrustReviewRequest
from app.services import durable_worker as durable_worker_module
from app.services import presentation_worker as presentation_worker_module
from app.services import solution_pipeline as solution_pipeline_module
from app.services.presentation_service import PresentationService
from app.services.presentation_worker import run_presentation_task
from app.services.session_service import SessionService
from app.services.solution_pipeline import run_solution_pipeline
from app.services.solution_trust_service import SolutionTrustService

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured")
@pytest.mark.asyncio
async def test_twenty_solution_runs_keep_trace_isolation_and_recover_expired_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(solution_pipeline_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(durable_worker_module, "get_session_factory", lambda: factory)
    workspace_id = uuid.uuid4()

    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE workflow_tasks, users CASCADE"))
    async with factory() as session:
        user = User(workspace_id=workspace_id, feishu_user_id="trace-user", name="并发测试员")
        session.add(user)
        await session.flush()
        profile = CustomerProfile(
            workspace_id=workspace_id,
            customer_name="并发测试客户",
            profile={
                "customer_name": "并发测试客户",
                "profile_status": "confirmed",
                "profile_summary": "用于验证 trace 隔离。",
                "source_ids": ["profile-source"],
            },
            status=ProfileStatus.CONFIRMED,
        )
        session.add(profile)
        await session.flush()
        chat = Session(
            workspace_id=workspace_id,
            customer_profile_id=profile.id,
            created_by_id=user.id,
            title="并发可信方案",
        )
        session.add(chat)
        await session.commit()
        service = SessionService(session, workspace_id, user.id)
        runs = []
        for index in range(20):
            _, run = await service.create_turn(chat.id, f"并发可信方案问题 {index}")
            task = await session.scalar(
                select(WorkflowTask).where(
                    WorkflowTask.kind == "solution_run", WorkflowTask.target_id == run.id
                )
            )
            runs.append((run.id, task.id))

    shared_engine = MockAIEngine()
    await asyncio.gather(
        *(
            run_solution_pipeline(
                run_id,
                workspace_id,
                shared_engine,
                task_id=task_id,
            )
            for run_id, task_id in runs
        )
    )

    async with factory() as session:
        completed = list(
            (
                await session.scalars(
                    select(SolutionRun).where(SolutionRun.workspace_id == workspace_id)
                )
            ).all()
        )
        decisions = list(
            (
                await session.scalars(
                    select(TrustDecisionRecord).where(
                        TrustDecisionRecord.workspace_id == workspace_id
                    )
                )
            ).all()
        )
        ai_runs = list(
            (
                await session.scalars(
                    select(AIRunRecord).where(AIRunRecord.workspace_id == workspace_id)
                )
            ).all()
        )
        assistant_count = int(
            await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.workspace_id == workspace_id,
                    Message.role == MessageRole.ASSISTANT,
                )
            )
            or 0
        )
        assert len(completed) == 20
        assert all(item.status.value == "completed" for item in completed)
        assert len({item.trace_id for item in completed}) == 20
        assert len(decisions) == 20
        assert all(item.action == TrustAction.RELEASE for item in decisions)
        assert {item.trace_id for item in ai_runs} == {item.trace_id for item in completed}
        assert assistant_count == 20

        stale_task = WorkflowTask(
            workspace_id=workspace_id,
            kind="lease-recovery-test",
            target_id=uuid.uuid4(),
            status=WorkflowTaskStatus.RUNNING,
            stage="verifying",
            trace_id="lease-recovery-trace",
            payload={},
            attempt_count=1,
            max_attempts=3,
            available_at=datetime.now(UTC) - timedelta(minutes=2),
            deadline_at=datetime.now(UTC) + timedelta(minutes=2),
            lease_owner="dead-worker",
            lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        session.add(stale_task)
        await session.commit()
        stale_task_id = stale_task.id

    claimed = await durable_worker_module.claim_next_task("replacement-worker")
    assert claimed is not None and claimed[0] == stale_task_id
    async with factory() as session:
        recovered = await session.get(WorkflowTask, stale_task_id)
        assert recovered.status == WorkflowTaskStatus.RUNNING
        assert recovered.lease_owner == "replacement-worker"
        assert recovered.attempt_count == 2
    await engine.dispose()


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured")
@pytest.mark.asyncio
async def test_human_block_propagates_to_existing_presentation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(solution_pipeline_module, "get_session_factory", lambda: factory)
    workspace_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE workflow_tasks, users CASCADE"))
    async with factory() as session:
        user = User(workspace_id=workspace_id, feishu_user_id="review-user", name="审核员")
        session.add(user)
        await session.flush()
        profile = CustomerProfile(
            workspace_id=workspace_id,
            customer_name="审核测试客户",
            profile={
                "customer_name": "审核测试客户",
                "profile_status": "confirmed",
                "profile_summary": "审核联动测试。",
                "source_ids": ["profile-source"],
            },
            status=ProfileStatus.CONFIRMED,
        )
        session.add(profile)
        await session.flush()
        chat = Session(
            workspace_id=workspace_id,
            customer_profile_id=profile.id,
            created_by_id=user.id,
            title="审核联动",
        )
        session.add(chat)
        await session.commit()
        _, run = await SessionService(session, workspace_id, user.id).create_turn(
            chat.id, "生成可追溯方案"
        )
        task = await session.scalar(
            select(WorkflowTask).where(WorkflowTask.target_id == run.id)
        )
        user_id = user.id
        run_id = run.id
        task_id = task.id
    await run_solution_pipeline(run_id, workspace_id, MockAIEngine(), task_id=task_id)

    async with factory() as session:
        style = StyleProfile(
            workspace_id=workspace_id,
            created_by_id=user_id,
            name="审核联动测试风格",
            version=1,
            reference_versions=[],
            status=StyleProfileStatus.CONFIRMED,
            visual_json={"palette": {}},
            narrative_json={"language": "zh-CN"},
            conflict_notes=[],
            confirmed_by_id=user_id,
            confirmed_at=datetime.now(UTC),
        )
        session.add(style)
        await session.flush()
        presentation = PresentationRun(
            workspace_id=workspace_id,
            solution_run_id=run_id,
            solution_version=1,
            style_profile_id=style.id,
            style_version=1,
            created_by_id=user_id,
            status=PresentationStatus.READY,
            trace_id=str(uuid.uuid4()),
            mode="balanced",
            audience="customer_executive",
            language="zh-CN",
            requested_outputs=["html"],
            locked_block_ids=[],
            version=1,
            upstream_trust_version=1,
        )
        session.add(presentation)
        await session.commit()
        presentation_id = presentation.id
        decision = await SolutionTrustService(session, workspace_id, user_id).review(
            run_id,
            TrustReviewRequest(
                expected_decision_version=1,
                decision="reject",
                reason="演示前人工复核发现该方案暂不应对外发布。",
            ),
        )
        assert decision["action"] == "block"

    async with factory() as session:
        blocked = await session.get(PresentationRun, presentation_id)
        reviews = list(
            (
                await session.scalars(
                    select(HumanReviewRecord).where(
                        HumanReviewRecord.solution_run_id == run_id
                    )
                )
            ).all()
        )
        assert blocked.status == PresentationStatus.BLOCKED
        assert len(reviews) == 1
        assert reviews[0].reason
    await engine.dispose()


class PassingPresentationProvider(MockPresentationProvider):
    mode = "test-live"

    def __init__(self) -> None:
        self.render_calls = 0
        self.export_calls = 0
        self.fail_next_export = True

    async def render(self, input_snapshot, style_profile):
        self.render_calls += 1
        result = await super().render(input_snapshot, style_profile)
        result["provider_mode"] = self.mode
        result["render_report"] = {
            "browser_rendered": True,
            "print_checked": True,
            "passed": True,
            "overlaps": 0,
            "overflows": 0,
        }
        result["status"] = "ready"
        return result

    async def export(self, artifact, export_type):
        self.export_calls += 1
        if self.fail_next_export:
            self.fail_next_export = False
            raise RuntimeError("temporary export failure")
        return {
            "provider_mode": self.mode,
            "object_key": f"test://exports/{artifact['artifact_id']}.{export_type}",
        }


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured")
@pytest.mark.asyncio
async def test_presentation_lifecycle_and_export_retry_reuse_ready_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(solution_pipeline_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(presentation_worker_module, "get_session_factory", lambda: factory)
    workspace_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE workflow_tasks, users CASCADE"))
    async with factory() as session:
        user = User(workspace_id=workspace_id, feishu_user_id="present-user", name="演示测试员")
        session.add(user)
        await session.flush()
        profile = CustomerProfile(
            workspace_id=workspace_id,
            customer_name="演示测试客户",
            profile={
                "customer_name": "演示测试客户",
                "profile_status": "confirmed",
                "profile_summary": "演示生命周期测试。",
                "source_ids": ["profile-source"],
            },
            status=ProfileStatus.CONFIRMED,
        )
        session.add(profile)
        await session.flush()
        chat = Session(
            workspace_id=workspace_id,
            customer_profile_id=profile.id,
            created_by_id=user.id,
            title="演示稿闭环",
        )
        session.add(chat)
        await session.commit()
        _, solution_run = await SessionService(session, workspace_id, user.id).create_turn(
            chat.id, "生成可导出的可信演示稿"
        )
        solution_task = await session.scalar(
            select(WorkflowTask).where(WorkflowTask.target_id == solution_run.id)
        )
        user_id = user.id
        run_id = solution_run.id
        solution_task_id = solution_task.id
    await run_solution_pipeline(run_id, workspace_id, MockAIEngine(), task_id=solution_task_id)

    provider = PassingPresentationProvider()
    async with factory() as session:
        service = PresentationService(session, workspace_id, user_id)
        deck = await service.create_reference(
            CreateReferenceDeckRequest(
                file_id="demo-reference",
                title="合成演示参考稿",
                storage_key="demo://reference.pptx",
                file_hash="a" * 64,
                mime_type=(
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                ),
                size_bytes=1024,
            )
        )
        parse_task = await session.scalar(
            select(WorkflowTask).where(
                WorkflowTask.kind == "reference_parse", WorkflowTask.target_id == deck.id
            )
        )
        parse_task_id = parse_task.id
    await run_presentation_task(
        parse_task_id, workspace_id, "reference_parse", deck.id, provider=provider
    )

    async with factory() as session:
        service = PresentationService(session, workspace_id, user_id)
        style = await service.generate_style(
            GenerateStyleProfileRequest(name="测试企业风格", reference_deck_ids=[deck.id])
        )
        style_task = await session.scalar(
            select(WorkflowTask).where(
                WorkflowTask.kind == "style_profile", WorkflowTask.target_id == style.id
            )
        )
        style_task_id = style_task.id
    await run_presentation_task(
        style_task_id, workspace_id, "style_profile", style.id, provider=provider
    )

    async with factory() as session:
        service = PresentationService(session, workspace_id, user_id)
        style = await service.get_style(style.id)
        style = await service.update_style(
            style.id,
            UpdateStyleProfileRequest(
                expected_version=style.version,
                conflict_resolutions={"mock_profile": "仅用于自动化测试"},
            ),
        )
        style = await service.confirm_style(style.id, style.version)
        presentation = await service.create_presentation(
            run_id,
            CreatePresentationRequest(
                style_profile_id=style.id,
                audience="customer_executive",
                output=["html"],
            ),
        )
        render_task = await session.scalar(
            select(WorkflowTask).where(
                WorkflowTask.kind == "presentation_render",
                WorkflowTask.target_id == presentation.id,
            )
        )
        presentation_id = presentation.id
        render_task_id = render_task.id
    await run_presentation_task(
        render_task_id,
        workspace_id,
        "presentation_render",
        presentation_id,
        provider=provider,
    )

    async with factory() as session:
        service = PresentationService(session, workspace_id, user_id)
        source = Source(
            workspace_id=workspace_id,
            imported_by_id=user_id,
            type=SourceType.PASTED_TEXT,
            purpose=SourcePurpose.EXPERIENCE,
            title="演示权限复核来源",
            content="已审核事实",
            status=SourceStatus.COMPLETED,
            freshness_status=SourceFreshness.CURRENT,
            content_version=1,
            permission_checked_at=datetime.now(UTC),
        )
        session.add(source)
        await session.flush()
        claim = ClaimRecord(
            workspace_id=workspace_id,
            solution_run_id=run_id,
            claim_key="historical_evidence:permission-check",
            section="historical_evidence",
            claim_text="已审核事实",
            claim_type="historical_fact",
            boundary="historical_fact",
            risk_level="high",
            verification_status=VerificationLabel.ENTAILED,
            released=True,
            candidate_version=1,
        )
        evidence = EvidenceRecord(
            workspace_id=workspace_id,
            solution_run_id=run_id,
            evidence_key="permission-check",
            candidate_version=1,
            asset_id=uuid.uuid4(),
            source_id=source.id,
            source_version=1,
            reviewed_source_version=1,
            quote="已审核事实",
            location={"kind": "json_path", "path": "$.summary"},
            permission_status="granted",
            permission_checked_at=datetime.now(UTC),
        )
        session.add_all([claim, evidence])
        await session.flush()
        session.add(
            ClaimEvidenceLink(
                workspace_id=workspace_id,
                solution_run_id=run_id,
                claim_id=claim.id,
                evidence_id=evidence.id,
                label=VerificationLabel.ENTAILED,
                score=1.0,
                verifier_version="quality-zh-v1",
            )
        )
        await session.commit()
        ready = await service.get_presentation(presentation_id)
        assert ready["status"] == "ready"
        export = await service.export(
            presentation_id,
            ExportPresentationRequest(
                expected_version=ready["version"], export_type="html"
            ),
        )
        export_task = await session.scalar(
            select(WorkflowTask).where(
                WorkflowTask.kind == "presentation_export",
                WorkflowTask.target_id == export.id,
            )
        )
        export_id = export.id
        export_task_id = export_task.id
    await run_presentation_task(
        export_task_id,
        workspace_id,
        "presentation_export",
        export_id,
        provider=provider,
    )

    async with factory() as session:
        failed = await session.get(ExportArtifact, export_id)
        assert failed.status == ExportStatus.FAILED
        service = PresentationService(session, workspace_id, user_id)
        retried = await service.export(
            presentation_id,
            ExportPresentationRequest(expected_version=1, export_type="html"),
        )
        assert retried.id == export_id
    await run_presentation_task(
        export_task_id,
        workspace_id,
        "presentation_export",
        export_id,
        provider=provider,
    )

    async with factory() as session:
        completed_export = await session.get(ExportArtifact, export_id)
        html_count = int(
            await session.scalar(
                select(func.count())
                .select_from(HtmlArtifact)
                .where(HtmlArtifact.presentation_id == presentation_id)
            )
            or 0
        )
        assert completed_export.status == ExportStatus.READY
        assert completed_export.object_key.startswith("test://exports/")
        assert html_count == 1
        assert provider.render_calls == 1
        assert provider.export_calls == 2
        source = await session.scalar(
            select(Source).where(Source.title == "演示权限复核来源")
        )
        source.freshness_status = SourceFreshness.PERMISSION_DENIED
        await session.commit()
        hidden = await PresentationService(
            session, workspace_id, user_id
        ).get_presentation(presentation_id)
        assert hidden["status"] == "blocked"
        assert hidden["artifact"] is None
    await engine.dispose()
