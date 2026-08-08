import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ai import MockAIEngine
from app.ai.embedding import MockEmbeddingProvider
from app.db.models import (
    CollaborationStatus,
    ContributionRole,
    CustomerProfile,
    Experience,
    ExpertContribution,
    ProfileStatus,
    ResearchTaskStatus,
    ReviewStatus,
    Source,
    SourceFreshness,
    SourcePurpose,
    SourceStatus,
    SourceType,
    User,
)
from app.db.repositories import ResearchTaskRepository
from app.integrations import MockFeishuAdapter
from app.services import research_pipeline as pipeline_module
from app.services.expert_collaboration_service import ExpertCollaborationService
from app.services.research_pipeline import run_research_pipeline
from app.services.research_service import ResearchTaskService

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured")
@pytest.mark.asyncio
async def test_deep_research_expert_reply_and_adoption_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(pipeline_module, "get_session_factory", lambda: factory)
    workspace_id = uuid.uuid4()
    ai_engine = MockAIEngine()
    embedding_provider = MockEmbeddingProvider(dimension=1024)
    feishu = MockFeishuAdapter("http://127.0.0.1:8000")

    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE users CASCADE"))

    async with factory() as session:
        user = User(
            workspace_id=workspace_id,
            feishu_user_id="expert-open-id",
            name="王专家",
        )
        session.add(user)
        await session.flush()
        profile = CustomerProfile(
            workspace_id=workspace_id,
            customer_name="测试客户",
            profile={
                "customer_name": "测试客户",
                "industry": "零售",
                "goals": ["完成门店质检试点"],
                "constraints": ["不得影响生产系统"],
                "information_gaps": ["接口权限由谁审批？"],
                "profile_summary": "零售客户，希望完成门店质检试点。",
                "source_ids": ["profile-source"],
            },
            status=ProfileStatus.CONFIRMED,
        )
        session.add(profile)
        await session.flush()
        source = Source(
            workspace_id=workspace_id,
            imported_by_id=user.id,
            type=SourceType.PASTED_TEXT,
            purpose=SourcePurpose.EXPERIENCE,
            title="历史试点复盘",
            content="曾使用旁路方式完成门店质检试点。",
            author=user.name,
            tags=["质检"],
            status=SourceStatus.COMPLETED,
            freshness_status=SourceFreshness.CURRENT,
            content_version=1,
        )
        session.add(source)
        await session.flush()
        embedded = await embedding_provider.embed("旁路质检试点", "experience")
        experience = Experience(
            workspace_id=workspace_id,
            source_id=source.id,
            data={
                "name": "旁路质检试点",
                "applicable_problem": "不影响生产系统的质检验证",
                "solution": "旁路接入后人工复核",
                "source_id": str(source.id),
                "tags": ["质检"],
            },
            review_status=ReviewStatus.VERIFIED,
            reviewed_by_id=user.id,
            source_version_at_review=1,
            embedding=embedded.vector,
            embedding_text="旁路质检试点",
            embedding_version="test:1024:v1",
            embedding_fingerprint="f" * 64,
            embedding_ready=True,
        )
        session.add(experience)
        await session.flush()
        session.add(
            ExpertContribution(
                workspace_id=workspace_id,
                user_id=user.id,
                source_id=source.id,
                role=ContributionRole.SOURCE_AUTHOR,
                asset_type="experience",
                asset_id=experience.id,
                title=source.title,
                tags=source.tags,
            )
        )
        await session.commit()

        task = await ResearchTaskService(session, workspace_id, user.id).create(
            customer_profile_id=profile.id,
            session_id=None,
            title="门店质检研究",
            question="如何在不影响生产系统的前提下完成门店质检试点？",
            completion_conditions=["给出有依据的实施路线"],
        )

    await run_research_pipeline(task.id, workspace_id, user.id, ai_engine, embedding_provider)

    async with factory() as session:
        task = await ResearchTaskRepository(session, workspace_id).get(task.id)
        assert task is not None
        assert task.status == ResearchTaskStatus.WAITING_EXPERT
        service = ExpertCollaborationService(session, workspace_id, user.id, ai_engine, feishu)
        collaboration = await service.create(task.id)
        assert collaboration.status == CollaborationStatus.AWAITING_CONFIRMATION
        assert [item["candidate_id"] for item in collaboration.candidate_records] == [str(user.id)]
        collaboration = await service.update_draft(
            collaboration.id,
            group_name="门店质检专家协作",
            selected_expert_ids=[user.id],
            questions=collaboration.questions,
        )
        collaboration = await service.confirm(collaboration.id)
        assert collaboration.status == CollaborationStatus.SENT
        assert collaboration.feishu_group_id
        reply, task = await service.record_reply(
            collaboration_id=collaboration.id,
            question_id=collaboration.questions[0]["question_id"],
            author_id=user.feishu_user_id,
            author_name=user.name,
            answer_text="接口权限需要由安全团队审批，并先采用只读访问。",
            feishu_message_id="message-001",
            message_url="https://example.test/message-001",
        )
        assert task.status == ResearchTaskStatus.RESEARCHING

    await run_research_pipeline(task.id, workspace_id, user.id, ai_engine, embedding_provider)

    async with factory() as session:
        task = await ResearchTaskRepository(session, workspace_id).get(task.id)
        assert task is not None
        assert task.status == ResearchTaskStatus.COMPLETED
        service = ExpertCollaborationService(session, workspace_id, user.id, ai_engine, feishu)
        reply = await service.adopt_reply(
            reply.id,
            {
                "name": "接口权限审批经验",
                "applicable_problem": "接口权限不明确",
                "solution": "由安全团队审批并先只读接入",
                "prerequisites": [],
                "risks": [],
                "applicable_conditions": [],
                "tags": ["接口权限"],
            },
        )
        assert reply.adopted_experience_id is not None
        adopted = await session.get(Experience, reply.adopted_experience_id)
        assert adopted is not None
        assert adopted.review_status == ReviewStatus.PENDING_REVIEW
        assert adopted.embedding_ready is False

    await engine.dispose()
