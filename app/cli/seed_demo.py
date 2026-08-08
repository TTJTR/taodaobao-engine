import argparse
import asyncio

from sqlalchemy import delete

from app.ai.embedding import AssetType, MockEmbeddingProvider
from app.core.config import settings
from app.db.database import configure_database
from app.db.models import (
    AIRunRecord,
    Capability,
    CustomerProfile,
    Experience,
    ExpertCollaboration,
    ExpertContribution,
    ExpertReply,
    IdempotencyRecord,
    Job,
    Message,
    ProfileStatus,
    ResearchStep,
    ResearchTask,
    ReviewRecord,
    ReviewStatus,
    Session,
    SolutionRun,
    Source,
    SourceFreshness,
    SourcePurpose,
    SourceStatus,
    SourceType,
    User,
)

DELETE_ORDER = (
    ExpertReply,
    ExpertCollaboration,
    ResearchStep,
    ResearchTask,
    ReviewRecord,
    AIRunRecord,
    ExpertContribution,
    SolutionRun,
    Message,
    Session,
    Capability,
    Experience,
    Job,
    Source,
    CustomerProfile,
    User,
    IdempotencyRecord,
)


async def seed_demo(*, reset: bool) -> None:
    _, session_factory = configure_database()
    workspace_id = settings.demo_workspace_id
    embedding = MockEmbeddingProvider(dimension=1024)
    async with session_factory() as session:
        if reset:
            for model in DELETE_ORDER:
                await session.execute(delete(model).where(model.workspace_id == workspace_id))
            await session.commit()
        existing = await session.scalar(
            User.__table__.select().where(
                User.workspace_id == workspace_id,
                User.feishu_user_id == "demo_user_001",
            )
        )
        if existing is not None:
            return
        user = User(
            workspace_id=workspace_id,
            feishu_user_id="demo_user_001",
            name="演示售前顾问",
        )
        session.add(user)
        await session.flush()
        profile = CustomerProfile(
            workspace_id=workspace_id,
            customer_name="A客户",
            profile={
                "customer_name": "A客户",
                "industry": "快消零售",
                "background": "希望复用现有摄像头开展门店质检。",
                "goals": ["两周内完成小范围试点"],
                "constraints": ["不采购专用硬件", "结果需要人工复核"],
                "information_gaps": ["试点门店范围待确认"],
                "profile_summary": "快消零售客户，希望低成本验证门店质检。",
                "source_ids": ["demo-profile-source"],
            },
            status=ProfileStatus.CONFIRMED,
        )
        session.add(profile)
        await session.flush()
        experience_source = Source(
            workspace_id=workspace_id,
            customer_profile_id=profile.id,
            imported_by_id=user.id,
            type=SourceType.PASTED_TEXT,
            purpose=SourcePurpose.EXPERIENCE,
            title="门店质检试点复盘",
            content="历史项目采用旁路接入和人工复核，在两周内完成试点。",
            author=user.name,
            tags=["门店质检", "旁路试点"],
            status=SourceStatus.COMPLETED,
            freshness_status=SourceFreshness.CURRENT,
            content_version=1,
            is_demo=True,
        )
        capability_source = Source(
            workspace_id=workspace_id,
            imported_by_id=user.id,
            type=SourceType.PASTED_TEXT,
            purpose=SourcePurpose.CAPABILITY,
            title="视觉质检能力 PRD",
            content="支持视频帧输入、缺陷识别结果输出，限制是必须人工复核。",
            author=user.name,
            tags=["视觉质检"],
            status=SourceStatus.COMPLETED,
            freshness_status=SourceFreshness.CURRENT,
            content_version=1,
            is_demo=True,
        )
        session.add_all([experience_source, capability_source])
        await session.flush()
        experience_vector = await embedding.embed("门店质检旁路试点", AssetType.EXPERIENCE)
        capability_vector = await embedding.embed("视觉质检能力", AssetType.CAPABILITY)
        session.add(
            Experience(
                workspace_id=workspace_id,
                source_id=experience_source.id,
                data={
                    "name": "门店质检旁路试点",
                    "applicable_problem": "不能影响生产系统的快速验证",
                    "solution": "旁路接入现有摄像头，识别结果由人工复核",
                    "tags": ["门店质检", "旁路试点"],
                    "source_id": str(experience_source.id),
                },
                review_status=ReviewStatus.VERIFIED,
                reviewed_by_id=user.id,
                source_version_at_review=1,
                embedding=experience_vector.vector,
                embedding_text="门店质检旁路试点",
                embedding_version=experience_vector.embedding_version,
                embedding_fingerprint=experience_vector.content_fingerprint,
                embedding_ready=True,
            )
        )
        session.add(
            Capability(
                workspace_id=workspace_id,
                source_id=capability_source.id,
                data={
                    "name": "视觉质检",
                    "description": "从视频帧识别疑似缺陷",
                    "inputs": ["视频帧"],
                    "outputs": ["疑似缺陷及置信度"],
                    "limitations": ["必须人工复核"],
                    "tags": ["视觉质检"],
                    "source_id": str(capability_source.id),
                },
                review_status=ReviewStatus.VERIFIED,
                reviewed_by_id=user.id,
                source_version_at_review=1,
                embedding=capability_vector.vector,
                embedding_text="视觉质检能力",
                embedding_version=capability_vector.embedding_version,
                embedding_fingerprint=capability_vector.content_fingerprint,
                embedding_ready=True,
            )
        )
        await session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize the V1 demo workspace")
    parser.add_argument("--reset", action="store_true", help="reset only the demo workspace")
    args = parser.parse_args()
    asyncio.run(seed_demo(reset=args.reset))


if __name__ == "__main__":
    main()
