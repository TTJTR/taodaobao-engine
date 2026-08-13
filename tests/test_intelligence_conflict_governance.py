import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import AppError
from app.db.models import (
    Base,
    CustomerProfile,
    IntelligenceFreshness,
    IntelligenceItem,
    IntelligenceItemArtifactLink,
    ProfileStatus,
    RawArtifact,
    RawArtifactKind,
    RawArtifactStatus,
    SearchRun,
    SearchRunStatus,
    User,
)
from app.schemas.intelligence_provider import (
    EnrichmentFact,
    EnrichmentJobRequest,
    EnrichmentJobResult,
    ProviderCitation,
)
from app.schemas.v2 import ManualIntelligenceSource
from app.services.intelligence_service import IntelligenceService

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


def _result(job_id: str, url: str, quote: str, value: str) -> EnrichmentJobResult:
    return EnrichmentJobResult(
        provider_job_id=job_id,
        status="completed",
        facts=[
            EnrichmentFact(
                field="focus_technology",
                value=value,
                category="technology_signal",
                provider_confidence=0.8,
                citations=[ProviderCitation(url=url, quote=quote)],
            )
        ],
    )


class _FixedProfileAI:
    def __init__(self, value: str) -> None:
        self.value = value

    async def extract_profile(self, raw_text: str, source_ids: list[str]) -> dict:
        return {"focus_technology": self.value, "source_ids": source_ids}


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured")
@pytest.mark.asyncio
@pytest.mark.parametrize("second_value,expected_items", [("AI 质检", 1), ("量子计算", 2)])
async def test_exact_deduplication_and_conflict_governance(
    second_value: str,
    expected_items: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = f"intelligence_governance_{uuid.uuid4().hex}"
    admin = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        TEST_DATABASE_URL,
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": f"{schema}, public"}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all, checkfirst=False)
        monkeypatch.setattr(
            "app.services.intelligence_service.socket.getaddrinfo",
            lambda *_: [(None, None, None, None, ("93.184.216.34", 0))],
        )
        async with factory() as session:
            workspace_id = uuid.uuid4()
            user = User(workspace_id=workspace_id, feishu_user_id=uuid.uuid4().hex, name="Reviewer")
            session.add(user)
            await session.flush()
            profile = CustomerProfile(
                workspace_id=workspace_id,
                customer_name=f"Customer-{uuid.uuid4().hex}",
                profile={"external_intelligence": {}},
                status=ProfileStatus.CONFIRMED,
                confirmed_by_id=user.id,
                confirmed_at=datetime.now(UTC),
            )
            session.add(profile)
            run = SearchRun(
                workspace_id=workspace_id,
                created_by_id=user.id,
                query="technology signals",
                purpose="customer_profile",
                provider="open_enrich",
                status=SearchRunStatus.RUNNING,
                trace_id=uuid.uuid4().hex,
                input_snapshot={},
                result_summary={},
            )
            session.add(run)
            await session.flush()
            artifacts = [
                _artifact(workspace_id, run.id, "https://example.com/one", "Source one quote"),
                _artifact(workspace_id, run.id, "https://example.com/two", "Source two quote"),
            ]
            session.add_all(artifacts)
            await session.commit()
            service = IntelligenceService(session, workspace_id, user.id)
            request = EnrichmentJobRequest(
                client_job_id=run.id,
                company_name="Example",
                website_url="https://example.com",
                allowed_fields=["focus_technology"],
            )
            await service.accept_provider_result(
                run_id=run.id,
                profile_id=profile.id,
                request=request,
                result=_result("job-one", artifacts[0].source_url, "Source one quote", "AI 质检"),
            )
            _, _, proposal = await service.accept_provider_result(
                run_id=run.id,
                profile_id=profile.id,
                request=request,
                result=_result(
                    "job-two", artifacts[1].source_url, "Source two quote", second_value
                ),
            )

            items = list(
                await session.scalars(
                    select(IntelligenceItem).where(
                        IntelligenceItem.workspace_id == workspace_id,
                        IntelligenceItem.is_deleted.is_(False),
                    )
                )
            )
            assert len(items) == expected_items
            if expected_items == 1:
                links = await session.scalar(
                    select(func.count())
                    .select_from(IntelligenceItemArtifactLink)
                    .where(
                        IntelligenceItemArtifactLink.workspace_id == workspace_id,
                        IntelligenceItemArtifactLink.intelligence_item_id == items[0].id,
                        IntelligenceItemArtifactLink.is_deleted.is_(False),
                    )
                )
                assert links == 2
                assert items[0].metadata_snapshot["source_count"] == 2
                assert items[0].metadata_snapshot["confidence"] > 0.8
                assert items[0].conflict_group_id is None
            else:
                assert items[0].conflict_group_id is not None
                assert items[0].conflict_group_id == items[1].conflict_group_id
                assert proposal is not None
                change = proposal.proposed_patch["focus_technology"]
                assert change["resolution_required"] is True
                assert len(change["candidates"]) == 2
                with pytest.raises(AppError, match="显式选择"):
                    await service.decide_proposal(
                        profile.id, proposal.id, accept=True, note="must choose candidate"
                    )
                await session.refresh(profile)
                assert profile.profile == {"external_intelligence": {}}
                with pytest.raises(AppError, match="不属于当前冲突字段"):
                    await service.decide_proposal(
                        profile.id,
                        proposal.id,
                        accept=True,
                        note="forged candidate",
                        selected_candidates={"focus_technology": uuid.uuid4()},
                    )
                selected = uuid.UUID(change["candidates"][1]["intelligence_item_id"])
                await service.decide_proposal(
                    profile.id,
                    proposal.id,
                    accept=True,
                    note="人工选择冲突候选",
                    selected_candidates={"focus_technology": selected},
                )
                await session.refresh(profile)
                await session.refresh(proposal)
                assert profile.profile["external_intelligence"]["focus_technology"] == second_value
                decided = proposal.proposed_patch["focus_technology"]
                assert decided["selected_candidate_id"] == str(selected)
                assert decided["resolution_required"] is False
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured")
@pytest.mark.asyncio
async def test_shared_ai_enrichment_uses_conflict_governance() -> None:
    schema = f"intelligence_shared_ai_{uuid.uuid4().hex}"
    admin = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        TEST_DATABASE_URL,
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": f"{schema}, public"}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all, checkfirst=False)
        async with factory() as session:
            workspace_id = uuid.uuid4()
            user = User(workspace_id=workspace_id, feishu_user_id=uuid.uuid4().hex, name="Reviewer")
            session.add(user)
            await session.flush()
            profile = CustomerProfile(
                workspace_id=workspace_id,
                customer_name="Example",
                profile={"external_intelligence": {}},
                status=ProfileStatus.CONFIRMED,
                confirmed_by_id=user.id,
                confirmed_at=datetime.now(UTC),
            )
            run = SearchRun(
                workspace_id=workspace_id,
                created_by_id=user.id,
                query="technology signals",
                purpose="customer_profile",
                provider="web_scraper",
                status=SearchRunStatus.COMPLETED,
                trace_id=uuid.uuid4().hex,
                input_snapshot={},
                result_summary={},
            )
            session.add_all([profile, run])
            await session.flush()
            artifacts = [
                _artifact(workspace_id, run.id, "https://example.com/one", "Source one quote"),
                _artifact(workspace_id, run.id, "https://example.com/two", "Source two quote"),
            ]
            session.add_all(artifacts)
            await session.commit()
            service = IntelligenceService(session, workspace_id, user.id)
            await service.enrich_artifact_for_profile(
                artifacts[0].id, profile.id, _FixedProfileAI("AI quality inspection")
            )
            _, _, proposal = await service.enrich_artifact_for_profile(
                artifacts[1].id, profile.id, _FixedProfileAI("Quantum computing")
            )
            items = list(
                await session.scalars(
                    select(IntelligenceItem).where(
                        IntelligenceItem.workspace_id == workspace_id,
                        IntelligenceItem.is_deleted.is_(False),
                    )
                )
            )
            assert len(items) == 2
            assert items[0].conflict_group_id == items[1].conflict_group_id
            assert proposal is not None
            assert proposal.proposed_patch["focus_technology"]["resolution_required"] is True
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured")
@pytest.mark.asyncio
async def test_reassess_freshness_marks_only_old_current_workspace_items() -> None:
    schema = f"intelligence_freshness_{uuid.uuid4().hex}"
    admin = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        TEST_DATABASE_URL,
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": f"{schema}, public"}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all, checkfirst=False)
        async with factory() as session:
            workspace_id = uuid.uuid4()
            user = User(workspace_id=workspace_id, feishu_user_id=uuid.uuid4().hex, name="Reviewer")
            session.add(user)
            await session.flush()
            run = SearchRun(
                workspace_id=workspace_id,
                created_by_id=user.id,
                query="freshness",
                purpose="customer_profile",
                provider="manual",
                status=SearchRunStatus.COMPLETED,
                trace_id=uuid.uuid4().hex,
                input_snapshot={},
                result_summary={},
            )
            session.add(run)
            await session.flush()
            now = datetime.now(UTC)
            old = _item(workspace_id, run.id, "old", now - timedelta(days=91))
            recent = _item(workspace_id, run.id, "recent", now - timedelta(days=89))
            unavailable = _item(
                workspace_id,
                run.id,
                "unavailable",
                now - timedelta(days=120),
                IntelligenceFreshness.UNAVAILABLE,
            )
            session.add_all([old, recent, unavailable])
            await session.commit()
            result = await IntelligenceService(session, workspace_id, user.id).reassess_freshness(
                stale_after_days=90
            )
            assert result["scanned"] == 2
            assert result["marked_stale"] == 1
            await session.refresh(old)
            await session.refresh(recent)
            await session.refresh(unavailable)
            assert old.freshness == IntelligenceFreshness.STALE
            assert (
                old.metadata_snapshot["freshness_reason"]
                == "captured_at_exceeded_stale_threshold"
            )
            assert recent.freshness == IntelligenceFreshness.CURRENT
            assert unavailable.freshness == IntelligenceFreshness.UNAVAILABLE
            with pytest.raises(AppError):
                await IntelligenceService(session, workspace_id, user.id).create_snapshot(
                    "customer_profile", [old.id]
                )
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not configured")
@pytest.mark.asyncio
async def test_manual_source_creates_and_reuses_raw_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = f"intelligence_manual_artifact_{uuid.uuid4().hex}"
    admin = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        TEST_DATABASE_URL,
        pool_pre_ping=True,
        connect_args={"server_settings": {"search_path": f"{schema}, public"}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all, checkfirst=False)
        monkeypatch.setattr(
            "app.services.intelligence_service.socket.getaddrinfo",
            lambda *_: [(None, None, None, None, ("93.184.216.34", 0))],
        )
        async with factory() as session:
            workspace_id = uuid.uuid4()
            user = User(workspace_id=workspace_id, feishu_user_id=uuid.uuid4().hex, name="Reviewer")
            session.add(user)
            await session.flush()
            service = IntelligenceService(session, workspace_id, user.id)
            source = ManualIntelligenceSource(
                title="Public notice",
                source_url="https://example.com/notice",
                content="Public procurement notice.",
            )
            first = await service.create_run(
                query="notice", purpose="customer_profile", provider="manual", sources=[source]
            )
            second = await service.create_run(
                query="notice", purpose="customer_profile", provider="manual", sources=[source]
            )
            artifacts = list(
                await session.scalars(
                    select(RawArtifact).where(
                        RawArtifact.workspace_id == workspace_id,
                        RawArtifact.is_deleted.is_(False),
                    )
                )
            )
            items = list(
                await session.scalars(
                    select(IntelligenceItem).where(
                        IntelligenceItem.workspace_id == workspace_id,
                        IntelligenceItem.is_deleted.is_(False),
                    )
                )
            )
            links = list(
                await session.scalars(
                    select(IntelligenceItemArtifactLink).where(
                        IntelligenceItemArtifactLink.workspace_id == workspace_id,
                        IntelligenceItemArtifactLink.is_deleted.is_(False),
                    )
                )
            )
            assert first.status == SearchRunStatus.COMPLETED
            assert second.status == SearchRunStatus.PARTIAL
            assert len(artifacts) == 1
            assert artifacts[0].kind == RawArtifactKind.PASTED_TEXT
            assert len(items) == 1
            assert len(links) == 1
            assert links[0].raw_artifact_id == artifacts[0].id
            assert second.result_summary["artifacts_reused"] == 1
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


def _artifact(workspace_id, run_id, url: str, quote: str) -> RawArtifact:
    return RawArtifact(
        workspace_id=workspace_id,
        search_run_id=run_id,
        artifact_key=hashlib_sha256(url),
        kind=RawArtifactKind.WEB_PAGE,
        status=RawArtifactStatus.CAPTURED,
        provider="test",
        source_url=url,
        normalized_url=url,
        mime_type="text/html",
        http_status=200,
        content_sha256=hashlib_sha256(quote),
        byte_size=len(quote.encode()),
        text_content=quote,
        captured_at=datetime.now(UTC),
        security_report={},
        metadata_snapshot={},
    )


def _item(
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    marker: str,
    captured_at: datetime,
    freshness: IntelligenceFreshness = IntelligenceFreshness.CURRENT,
) -> IntelligenceItem:
    return IntelligenceItem(
        workspace_id=workspace_id,
        search_run_id=run_id,
        title=marker,
        source_url=f"https://example.com/{marker}",
        source_domain="example.com",
        captured_at=captured_at,
        content=marker,
        summary=marker,
        facts=[],
        fingerprint=hashlib_sha256(marker),
        freshness=freshness,
        review_status="pending",
        metadata_snapshot={},
    )


def hashlib_sha256(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()
