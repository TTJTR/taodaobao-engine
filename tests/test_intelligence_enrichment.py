import os
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import AppError, ErrorCode
from app.db.models import (
    CustomerProfile,
    CustomerProfileVersion,
    ProfileStatus,
    RawArtifact,
    RawArtifactKind,
    RawArtifactStatus,
    SearchRun,
    SearchRunStatus,
    User,
)
from app.services.intelligence_service import (
    IntelligenceService,
    _apply_profile_patch,
    _normalize_ai_facts,
    _profile_diff,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


def artifact() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), content_sha256="a" * 64)


def test_ai_facts_are_always_bound_to_raw_artifact() -> None:
    raw = artifact()
    facts = _normalize_ai_facts(
        {"project_signals": ["AI 质检项目"], "source_ids": [str(raw.id)]},
        raw,
    )

    assert facts == [
        {
            "field": "project_signals",
            "value": ["AI 质检项目"],
            "classification": "external_public_information",
            "raw_artifact_id": str(raw.id),
            "content_sha256": "a" * 64,
        }
    ]


def test_pending_proposal_diff_does_not_mutate_profile() -> None:
    profile = {"external_intelligence": {"focus_technologies": ["数据中台"]}}
    before = {"external_intelligence": {"focus_technologies": ["数据中台"]}}

    patch = _profile_diff(profile, {"focus_technologies": ["数据中台", "AI质检"]})

    assert profile == before
    assert patch["focus_technologies"]["previous"] == ["数据中台"]
    assert patch["focus_technologies"]["proposed"] == ["数据中台", "AI质检"]


def test_confirm_applies_structured_patch_only_when_called() -> None:
    current = {"focus_technologies": ["数据中台"]}
    patch = _profile_diff(
        {"external_intelligence": current},
        {"focus_technologies": ["数据中台", "AI质检"]},
    )

    updated = _apply_profile_patch(current, patch)

    assert current == {"focus_technologies": ["数据中台"]}
    assert updated == {"focus_technologies": ["数据中台", "AI质检"]}


def test_empty_ai_output_is_rejected_instead_of_creating_unsourced_fact() -> None:
    with pytest.raises(AppError) as caught:
        _normalize_ai_facts({"source_ids": [str(uuid.uuid4())]}, artifact())

    assert caught.value.code == ErrorCode.AI_OUTPUT_INVALID


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured")
@pytest.mark.asyncio
async def test_mock_ai_enrichment_confirm_and_profile_version_closed_loop() -> None:
    class MockIntelligenceAI:
        async def extract_profile(self, raw_text: str, source_ids: list[str]) -> dict:
            assert "AI质检" in raw_text
            return {
                "title": "客户公开项目动态",
                "summary": "客户正在推进 AI 质检项目",
                "project_signals": ["AI质检"],
                "source_ids": source_ids,
            }

    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    workspace_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE users CASCADE"))
    async with factory() as session:
        user = User(workspace_id=workspace_id, feishu_user_id="enrichment-user", name="审批人")
        session.add(user)
        await session.flush()
        profile = CustomerProfile(
            workspace_id=workspace_id,
            customer_name="情报测试客户",
            profile={"external_intelligence": {"focus": ["数据中台"]}},
            status=ProfileStatus.CONFIRMED,
            confirmed_by_id=user.id,
            confirmed_at=datetime.now(UTC),
        )
        session.add(profile)
        run = SearchRun(
            workspace_id=workspace_id,
            created_by_id=user.id,
            query="客户 AI 项目",
            purpose="customer_profile",
            provider="web_scraper",
            status=SearchRunStatus.COMPLETED,
            trace_id=str(uuid.uuid4()),
            input_snapshot={},
            result_summary={},
        )
        session.add(run)
        await session.flush()
        artifact_row = RawArtifact(
            workspace_id=workspace_id,
            search_run_id=run.id,
            artifact_key="1" * 64,
            kind=RawArtifactKind.WEB_PAGE,
            status=RawArtifactStatus.CAPTURED,
            provider="web_scraper",
            source_url="https://example.com/ai-quality",
            normalized_url="https://example.com/ai-quality",
            mime_type="text/html",
            http_status=200,
            content_sha256="2" * 64,
            byte_size=32,
            text_content="客户正在推进AI质检项目。",
            captured_at=datetime.now(UTC),
            security_report={},
            metadata_snapshot={},
        )
        session.add(artifact_row)
        await session.commit()

        service = IntelligenceService(session, workspace_id, user.id)
        _, _, proposal = await service.enrich_artifact_for_profile(
            artifact_row.id, profile.id, MockIntelligenceAI()
        )
        assert proposal is not None
        await session.refresh(profile)
        assert profile.profile == {"external_intelligence": {"focus": ["数据中台"]}}

        await service.decide_proposal(profile.id, proposal.id, accept=True, note="人工核验通过")
        await session.refresh(profile)
        assert profile.profile["external_intelligence"]["project_signals"] == ["AI质检"]
        assert profile.version == 2
        history = await session.scalar(
            select(CustomerProfileVersion).where(
                CustomerProfileVersion.workspace_id == workspace_id,
                CustomerProfileVersion.profile_id == profile.id,
                CustomerProfileVersion.is_deleted.is_(False),
            )
        )
        assert history is not None
        assert history.profile_snapshot["profile"] == {
            "external_intelligence": {"focus": ["数据中台"]}
        }
    await engine.dispose()
