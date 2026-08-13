"""Seed a repeatable V2 intelligence and tender workspace for local UI review."""

from __future__ import annotations

import asyncio
import hashlib
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.db.database import create_db_engine, create_session_factory  # noqa: E402
from app.db.models import (  # noqa: E402
    ArtifactRelationType,
    Capability,
    CustomerProfile,
    IntelligenceFreshness,
    IntelligenceItem,
    IntelligenceItemArtifactLink,
    IntelligenceReviewStatus,
    IntelligenceSnapshot,
    ProfileIntelligenceProposal,
    ProfileStatus,
    RawArtifact,
    RawArtifactKind,
    RawArtifactStatus,
    ResponseMatrix,
    ResponseMatrixItem,
    ResponseMatrixItemVersion,
    ReviewStatus,
    SearchRun,
    SearchRunStatus,
    Source,
    SourceFreshness,
    SourcePurpose,
    SourceStatus,
    SourceType,
    TenderDocument,
    TenderParseStatus,
    TenderParseVersion,
    TenderRequirement,
    TenderRequirementStatus,
    User,
)
from app.services.intelligence_service import IntelligenceService  # noqa: E402
from app.services.tender_service import TenderService  # noqa: E402

SEED = "v2-ui-seed"
WORKSPACE_ID = settings.demo_workspace_id


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def location(sequence: int, section: str) -> dict:
    text = REQUIREMENTS[sequence - 1][0]
    return {
        "schema_version": "document-location-v1",
        "kind": "docx_paragraph",
        "section_path": [section],
        "paragraph_index": sequence,
        "quote_hash": digest(text),
    }


REQUIREMENTS = [
    (
        "★系统必须支持 10 万并发用户，P95 响应时间不超过 200ms。",
        "技术",
        True,
        [
            {"type": "concurrency", "value": 100000, "unit": "users"},
            {"type": "latency", "value": 200, "unit": "ms"},
        ],
    ),
    (
        "投标人须提供近三年内同类项目成功案例不少于 3 个。",
        "案例",
        True,
        [
            {"type": "count", "value": 3, "unit": "projects"},
            {"type": "period", "value": 3, "unit": "years"},
        ],
    ),
    (
        "项目应在合同签订后 90 日内完成上线并通过验收。",
        "交付",
        False,
        [{"type": "duration", "value": 90, "unit": "days"}],
    ),
    ("系统必须符合等保三级及数据出境合规要求。", "合规", True, []),
    (
        "提供 7×24 小时运维支持，故障 30 分钟内响应。",
        "服务",
        False,
        [
            {"type": "service_window", "value": "7x24"},
            {"type": "response_time", "value": 30, "unit": "minutes"},
        ],
    ),
    ("平台须提供宇宙飞船自动对接与轨道校准接口。", "技术", True, []),
]


async def cleanup(session) -> None:
    tender_ids = select(TenderDocument.id).where(
        TenderDocument.workspace_id == WORKSPACE_ID,
        TenderDocument.source_fingerprint == digest(SEED),
    )
    matrix_ids = select(ResponseMatrix.id).where(
        ResponseMatrix.workspace_id == WORKSPACE_ID,
        ResponseMatrix.tender_id.in_(tender_ids),
    )
    item_ids = select(ResponseMatrixItem.id).where(
        ResponseMatrixItem.workspace_id == WORKSPACE_ID,
        ResponseMatrixItem.matrix_id.in_(matrix_ids),
    )
    profile_ids = select(CustomerProfile.id).where(
        CustomerProfile.workspace_id == WORKSPACE_ID,
        CustomerProfile.customer_name == "华北云智科技集团（V2 演示）",
    )
    snapshot_ids = list(
        await session.scalars(
            select(ProfileIntelligenceProposal.snapshot_id).where(
                ProfileIntelligenceProposal.workspace_id == WORKSPACE_ID,
                ProfileIntelligenceProposal.profile_id.in_(profile_ids),
            )
        )
    )
    run_ids = select(SearchRun.id).where(
        SearchRun.workspace_id == WORKSPACE_ID, SearchRun.trace_id == SEED
    )
    intelligence_ids = select(IntelligenceItem.id).where(
        IntelligenceItem.workspace_id == WORKSPACE_ID,
        IntelligenceItem.search_run_id.in_(run_ids),
    )
    source_ids = select(Source.id).where(
        Source.workspace_id == WORKSPACE_ID,
        Source.source_url == f"seed://{SEED}/capability",
    )
    statements = [
        delete(ResponseMatrixItemVersion).where(
            ResponseMatrixItemVersion.workspace_id == WORKSPACE_ID,
            ResponseMatrixItemVersion.response_item_id.in_(item_ids),
        ),
        delete(ResponseMatrixItem).where(ResponseMatrixItem.id.in_(item_ids)),
        delete(ResponseMatrix).where(ResponseMatrix.id.in_(matrix_ids)),
        delete(TenderRequirement).where(TenderRequirement.tender_id.in_(tender_ids)),
        delete(TenderParseVersion).where(TenderParseVersion.tender_id.in_(tender_ids)),
        delete(TenderDocument).where(TenderDocument.id.in_(tender_ids)),
        delete(ProfileIntelligenceProposal).where(
            ProfileIntelligenceProposal.workspace_id == WORKSPACE_ID,
            ProfileIntelligenceProposal.snapshot_id.in_(snapshot_ids),
        ),
        delete(IntelligenceSnapshot).where(IntelligenceSnapshot.id.in_(snapshot_ids)),
        delete(IntelligenceItemArtifactLink).where(
            IntelligenceItemArtifactLink.intelligence_item_id.in_(intelligence_ids)
        ),
        delete(IntelligenceItem).where(IntelligenceItem.id.in_(intelligence_ids)),
        delete(RawArtifact).where(
            RawArtifact.workspace_id == WORKSPACE_ID,
            RawArtifact.metadata_snapshot["seed"].astext == SEED,
        ),
        delete(SearchRun).where(SearchRun.id.in_(run_ids)),
        delete(Capability).where(Capability.source_id.in_(source_ids)),
        delete(Source).where(Source.id.in_(source_ids)),
        delete(CustomerProfile).where(
            CustomerProfile.workspace_id == WORKSPACE_ID,
            CustomerProfile.customer_name == "华北云智科技集团（V2 演示）",
        ),
    ]
    for statement in statements:
        await session.execute(statement)
    await session.commit()


async def seed() -> dict[str, str]:
    if not settings.database_url:
        raise RuntimeError("APP_DATABASE_URL is not configured")
    engine = create_db_engine(settings.database_url)
    factory = create_session_factory(engine)
    try:
        async with factory() as session:
            await session.execute(select(1))
            await cleanup(session)
            user = await session.scalar(
                select(User).where(
                    User.workspace_id == WORKSPACE_ID,
                    User.feishu_user_id == "v2-ui-seed-user",
                    User.is_deleted.is_(False),
                )
            )
            if user is None:
                user = User(
                    workspace_id=WORKSPACE_ID,
                    feishu_user_id="v2-ui-seed-user",
                    name="V2 业务验收员",
                )
                session.add(user)
                await session.flush()
            profile = CustomerProfile(
                workspace_id=WORKSPACE_ID,
                customer_name="华北云智科技集团（V2 演示）",
                profile={
                    "industry": "企业云服务",
                    "focus_technologies": ["数据中台"],
                    "region": "华北",
                },
                status=ProfileStatus.CONFIRMED,
                confirmed_by_id=user.id,
                confirmed_at=datetime.now(UTC),
            )
            session.add(profile)
            run = SearchRun(
                workspace_id=WORKSPACE_ID,
                created_by_id=user.id,
                query="华北云智科技集团 战略、招聘与技术栈",
                purpose="customer_intelligence",
                provider="open_enrich",
                status=SearchRunStatus.COMPLETED,
                trace_id=SEED,
                input_snapshot={"seed": SEED},
                result_summary={"facts": 4, "conflicts": 1},
                completed_at=datetime.now(UTC),
            )
            session.add(run)
            await session.flush()
            artifacts = []
            for index, domain in enumerate(
                ("news.example.com", "jobs.example.com", "invest.example.com"), 1
            ):
                content = f"{domain} 报道华北云智在 AI 质检、云原生和行业模型方向持续投入。"
                artifact = RawArtifact(
                    workspace_id=WORKSPACE_ID,
                    search_run_id=run.id,
                    artifact_key=digest(f"{SEED}-artifact-{index}"),
                    kind=RawArtifactKind.WEB_PAGE,
                    status=RawArtifactStatus.VALIDATED,
                    provider="ui-seed",
                    source_url=f"https://{domain}/reports/{index}",
                    normalized_url=f"https://{domain}/reports/{index}",
                    mime_type="text/html",
                    http_status=200,
                    content_sha256=digest(content),
                    byte_size=len(content.encode()),
                    text_content=content,
                    captured_at=datetime.now(UTC),
                    security_report={"ssrf_checked": True},
                    metadata_snapshot={"seed": SEED},
                )
                session.add(artifact)
                artifacts.append(artifact)
            await session.flush()
            conflict_id = uuid.uuid4()
            facts = [
                ("战略重点", "AI 质检与行业大模型", None, 0.91),
                ("员工规模", "约 3,200 人", conflict_id, 0.84),
                ("员工规模", "约 4,100 人", conflict_id, 0.78),
                ("招聘信号", "新增 46 个云原生岗位", None, 0.88),
            ]
            items = []
            for index, (field, value, group, confidence) in enumerate(facts):
                artifact = artifacts[index % len(artifacts)]
                item = IntelligenceItem(
                    workspace_id=WORKSPACE_ID,
                    search_run_id=run.id,
                    title=f"{field}：{value}",
                    source_url=artifact.source_url,
                    source_domain=artifact.source_url.split("/")[2],
                    captured_at=datetime.now(UTC),
                    content=artifact.text_content,
                    summary=f"公开来源显示{field}为{value}。",
                    facts=[{"field": field, "value": value, "quote": artifact.text_content}],
                    fingerprint=digest(f"{SEED}-{field}-{value}"),
                    conflict_group_id=group,
                    freshness=IntelligenceFreshness.CURRENT,
                    review_status=IntelligenceReviewStatus.PENDING,
                    metadata_snapshot={
                        "seed": SEED,
                        "confidence": confidence,
                        "latest_captured_at": datetime.now(UTC).isoformat(),
                    },
                )
                session.add(item)
                await session.flush()
                session.add(
                    IntelligenceItemArtifactLink(
                        workspace_id=WORKSPACE_ID,
                        intelligence_item_id=item.id,
                        raw_artifact_id=artifact.id,
                        relation_type=ArtifactRelationType.CONFLICTING
                        if group
                        else ArtifactRelationType.PRIMARY,
                        source_snapshot={
                            "url": artifact.source_url,
                            "quote": artifact.text_content,
                        },
                    )
                )
                items.append(item)
            await session.commit()
            intelligence = IntelligenceService(session, WORKSPACE_ID, user.id)
            snapshot = await intelligence.create_snapshot(
                "profile_update", [item.id for item in items]
            )
            proposal = await intelligence.create_profile_proposal(
                profile.id,
                snapshot.id,
                {
                    "focus_technologies": {
                        "operation": "replace",
                        "previous": ["数据中台"],
                        "proposed": ["数据中台", "AI质检"],
                    },
                    "employee_scale": {
                        "resolution_required": True,
                        "conflict_group_id": str(conflict_id),
                        "candidates": [
                            {
                                "intelligence_item_id": str(items[1].id),
                                "value": "约 3,200 人",
                                "confidence": 0.84,
                            },
                            {
                                "intelligence_item_id": str(items[2].id),
                                "value": "约 4,100 人",
                                "confidence": 0.78,
                            },
                        ],
                    },
                },
            )
            source = Source(
                workspace_id=WORKSPACE_ID,
                imported_by_id=user.id,
                type=SourceType.PASTED_TEXT,
                purpose=SourcePurpose.CAPABILITY,
                title="高并发平台能力证明",
                content="平台压测支持 10 万并发，P95 响应时间 180ms。",
                source_url=f"seed://{SEED}/capability",
                status=SourceStatus.COMPLETED,
                freshness_status=SourceFreshness.CURRENT,
                content_fingerprint=digest("seed-capability"),
                content_version=1,
                permission_checked_at=datetime.now(UTC),
            )
            session.add(source)
            await session.flush()
            capability = Capability(
                workspace_id=WORKSPACE_ID,
                source_id=source.id,
                data={"name": "十万级高并发处理", "description": "P95 180ms"},
                review_status=ReviewStatus.VERIFIED,
                reviewed_by_id=user.id,
                reviewed_at=datetime.now(UTC),
                source_version_at_review=1,
                embedding=[0.0] * 1024,
                embedding_text="支持 10 万并发 P95 180ms",
                embedding_version="ui-seed",
                embedding_fingerprint=digest("seed-vector"),
                embedding_ready=True,
            )
            session.add(capability)
            artifact = RawArtifact(
                workspace_id=WORKSPACE_ID,
                artifact_key=digest(f"{SEED}-tender"),
                kind=RawArtifactKind.TENDER_FILE,
                status=RawArtifactStatus.VALIDATED,
                provider="ui-seed",
                source_filename="华北云智数字平台招标书.docx",
                mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                content_sha256=digest("\n".join(row[0] for row in REQUIREMENTS)),
                byte_size=4096,
                storage_uri=f"seed://{SEED}/tender",
                captured_at=datetime.now(UTC),
                security_report={"seed": SEED},
                metadata_snapshot={"seed": SEED},
            )
            session.add(artifact)
            await session.flush()
            tender = TenderDocument(
                workspace_id=WORKSPACE_ID,
                created_by_id=user.id,
                title="华北云智数字平台建设项目",
                customer_profile_id=profile.id,
                raw_artifact_id=artifact.id,
                source_filename=artifact.source_filename,
                source_mime_type=artifact.mime_type,
                source_fingerprint=digest(SEED),
                content_text="\n".join(row[0] for row in REQUIREMENTS),
                status="parsed",
            )
            session.add(tender)
            await session.flush()
            nodes = [
                {"text": row[0], "node_type": "paragraph", "location": location(i, row[1])}
                for i, row in enumerate(REQUIREMENTS, 1)
            ]
            parsed = TenderParseVersion(
                workspace_id=WORKSPACE_ID,
                tender_id=tender.id,
                raw_artifact_id=artifact.id,
                version=1,
                parser_name="ui-seed-parser",
                parser_version="1.0",
                document_schema_version="v2.0",
                status=TenderParseStatus.PARSED,
                document_ir={"nodes": nodes},
                resource_usage={"mock": True},
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )
            session.add(parsed)
            await session.flush()
            for sequence, (text, category, mandatory, metrics) in enumerate(REQUIREMENTS, 1):
                session.add(
                    TenderRequirement(
                        workspace_id=WORKSPACE_ID,
                        tender_id=tender.id,
                        parse_version_id=parsed.id,
                        sequence=sequence,
                        requirement_text=text,
                        category=category,
                        mandatory=mandatory,
                        acceptance_condition="提供验收材料并通过采购方测试" if mandatory else None,
                        metrics=metrics,
                        ambiguities=["需确认验收环境"] if sequence == 3 else [],
                        recommended_action="准备证明材料" if mandatory else "纳入实施计划",
                        source_location=location(sequence, category),
                        status=TenderRequirementStatus.CONFIRMED,
                        confirmed_by_id=user.id,
                        confirmed_at=datetime.now(UTC),
                    )
                )
            await session.commit()
            matrix = await TenderService(session, WORKSPACE_ID, user.id).create_matrix(
                tender.id,
                experience_ids=[],
                capability_ids=[capability.id],
                intelligence_snapshot_id=snapshot.id,
            )
            return {
                "workspace_id": str(WORKSPACE_ID),
                "user_id": str(user.id),
                "profile_id": str(profile.id),
                "search_run_id": str(run.id),
                "proposal_id": str(proposal.id),
                "tender_id": str(tender.id),
                "matrix_id": str(matrix.id),
            }
    finally:
        await engine.dispose()


async def main() -> int:
    try:
        result = await seed()
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        return 1
    print("[PASS] V2 UI mock data is ready")
    for key, value in result.items():
        print(f"  {key}: {value}")
    print("Open /v2_intelligence.html and /v2_tender.html after signing in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
