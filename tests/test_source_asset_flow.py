import os
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ai import MockAIEngine
from app.db.models import (
    Capability,
    Experience,
    Job,
    JobType,
    ProcessStatus,
    ReviewStatus,
    Source,
    SourcePurpose,
    SourceStatus,
    SourceType,
    User,
)
from app.integrations import MockFeishuAdapter
from app.services import job_runner as runner_module

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured")
@pytest.mark.asyncio
async def test_experience_and_capability_sources_create_separate_review_drafts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(runner_module, "get_session_factory", lambda: factory)
    workspace_id = uuid.uuid4()
    user_id = uuid.uuid4()

    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE users CASCADE"))

    async with factory() as session:
        session.add(
            User(
                id=user_id,
                workspace_id=workspace_id,
                feishu_user_id="source-flow-user",
                name="资料贡献者",
            )
        )
        await session.flush()
        sources = [
            Source(
                workspace_id=workspace_id,
                imported_by_id=user_id,
                type=SourceType.PASTED_TEXT,
                purpose=SourcePurpose.EXPERIENCE,
                title="项目复盘",
                content="曾采用旁路接入并由人工复核，试点已经完成验收。",
                tags=[],
                status=SourceStatus.PENDING,
            ),
            Source(
                workspace_id=workspace_id,
                imported_by_id=user_id,
                type=SourceType.PASTED_TEXT,
                purpose=SourcePurpose.CAPABILITY,
                title="产品 PRD",
                content="系统支持旁路接入，输入为业务事件，输出为分析结果。",
                tags=[],
                status=SourceStatus.PENDING,
            ),
        ]
        session.add_all(sources)
        await session.flush()
        jobs = [
            Job(
                workspace_id=workspace_id,
                type=JobType.SOURCE_PROCESSING,
                target_id=source.id,
                status=ProcessStatus.PENDING,
                stage="pending",
                retry_count=0,
            )
            for source in sources
        ]
        session.add_all(jobs)
        await session.commit()

    adapter = MockFeishuAdapter("http://127.0.0.1:8000")
    ai_engine = MockAIEngine()
    for source, job in zip(sources, jobs, strict=True):
        await runner_module.run_source_job(
            job.id, source.id, workspace_id, adapter, ai_engine
        )

    async with factory() as session:
        experience = await session.scalar(
            select(Experience).where(Experience.workspace_id == workspace_id)
        )
        capability = await session.scalar(
            select(Capability).where(Capability.workspace_id == workspace_id)
        )
        refreshed_sources = list(
            await session.scalars(
                select(Source)
                .where(Source.workspace_id == workspace_id)
                .order_by(Source.created_at)
            )
        )

        assert experience is not None
        assert capability is not None
        assert experience.source_id == sources[0].id
        assert capability.source_id == sources[1].id
        assert experience.review_status == ReviewStatus.PENDING_REVIEW
        assert capability.review_status == ReviewStatus.PENDING_REVIEW
        assert experience.embedding_ready is False
        assert capability.embedding_ready is False
        assert all(source.status == SourceStatus.PENDING_REVIEW for source in refreshed_sources)

    await engine.dispose()
