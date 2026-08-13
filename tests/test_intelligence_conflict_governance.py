import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import AppError
from app.db.models import (
    Base,
    CustomerProfile,
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


def hashlib_sha256(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode()).hexdigest()
