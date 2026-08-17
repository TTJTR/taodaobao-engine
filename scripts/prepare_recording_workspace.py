"""Prepare the live automotive demo workspace without mocking business functions.

The script prefers the configured real Feishu documents. If a document cannot be
read, it imports the matching bundled automotive text as source data through the
same SourceService and AI extraction pipeline used by the HTTP API.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.api.deps import get_ai_engine, get_embedding_provider, get_feishu_adapter
from app.db.database import close_database, get_session_factory
from app.db.models import Capability, Experience, ReviewStatus, Source, SourcePurpose, User
from app.services.asset_service import CapabilityService, ExperienceService
from app.services.bitable_sync_service import BitableSyncService
from app.services.job_runner import run_profile_job, run_source_job
from app.services.profile_service import CustomerProfileService
from app.services.source_service import SourceService

DATA_ROOT = Path("/opt/taodaobao/recording-data")
DOCUMENTS = (
    (
        "https://larkcommunity.feishu.cn/docx/BaF2dKxv1o0vxAxFCQocFTUanPb",
        SourcePurpose.CUSTOMER_PROFILE,
        "01_客户画像素材_东岳智行.md",
    ),
    (
        "https://larkcommunity.feishu.cn/docx/T5IDdzkfHoWA8Qxy22ycb5Vqnnc",
        SourcePurpose.CUSTOMER_PROFILE,
        "02_客户磋商会议纪要_智能工厂质量提升.md",
    ),
    (
        "https://larkcommunity.feishu.cn/docx/QbnXddkQ6ojoWwxqEEQcEsAznqC",
        SourcePurpose.EXPERIENCE,
        "03_历史项目方案_焊装质量追溯平台.md",
    ),
    (
        "https://larkcommunity.feishu.cn/docx/BDlHdegElosR9oxZPH3cDaUYnbc",
        SourcePurpose.EXPERIENCE,
        "04_项目复盘_视觉质检试点.md",
    ),
    (
        "https://larkcommunity.feishu.cn/docx/YNgTdFOayoHji6x2pmjcUpPJnjg",
        SourcePurpose.CAPABILITY,
        "05_PRD原子能力清单_汽车制造.md",
    ),
    (
        "https://larkcommunity.feishu.cn/docx/RpIhdEzPjon3ApxQdSrcNbCjnug",
        SourcePurpose.CUSTOMER_PROFILE,
        "06_公开情报快照_汽车制造数字化.md",
    ),
    (
        "https://larkcommunity.feishu.cn/docx/OrAUdADonoKx9lxJQt6cNE9lnmf",
        SourcePurpose.EXPERIENCE,
        "08_历史项目方案_动力电池装配追溯.md",
    ),
    (
        "https://larkcommunity.feishu.cn/docx/Q3ZZd3xCioCloixMgAFcqaCznrc",
        SourcePurpose.EXPERIENCE,
        "11_历史项目方案_供应商质量问题闭环.md",
    ),
    (
        "https://larkcommunity.feishu.cn/docx/AgUwdZFzNo9xPzxxoEKcbLRqnGe",
        SourcePurpose.EXPERIENCE,
        "09_历史项目方案_总装扭矩质量闭环.md",
    ),
    (
        "https://larkcommunity.feishu.cn/docx/WM4rd9dWHo1pbLxQpiCcx1K1nfb",
        SourcePurpose.EXPERIENCE,
        "10_历史项目方案_冲压设备预测性维护.md",
    ),
)


async def _active_user(adapter) -> User:
    session_factory = get_session_factory()
    async with session_factory() as session:
        user = await session.scalar(
            select(User)
            .where(User.feishu_access_token.is_not(None), User.is_deleted.is_(False))
            .order_by(User.updated_at.desc())
        )
        if user is None:
            raise RuntimeError("No Feishu-authorized user is available")
        if (
            user.feishu_refresh_token
            and user.feishu_token_expires_at
            and user.feishu_token_expires_at <= datetime.now(UTC) + timedelta(seconds=60)
        ):
            token = await adapter.refresh_access_token(user.feishu_refresh_token)
            user.feishu_access_token = token.access_token
            user.feishu_refresh_token = token.refresh_token
            user.feishu_token_expires_at = token.expires_at
            await session.commit()
        return user


async def _import_one(user, profile_id, adapter, ai_engine, url, purpose, fallback_name):
    session_factory = get_session_factory()
    async with session_factory() as session:
        imported = await SourceService(session, user.workspace_id, user.id).import_link(
            url=url,
            purpose=purpose,
            customer_profile_id=profile_id if purpose == SourcePurpose.CUSTOMER_PROFILE else None,
        )
        source_id, job_id = imported.source.id, imported.job.id
    await run_source_job(
        job_id,
        source_id,
        user.workspace_id,
        adapter,
        ai_engine,
        user.feishu_access_token,
    )
    async with session_factory() as session:
        source = await session.get(Source, source_id)
        if source is not None and source.status.value != "failed":
            return source.id, "feishu", source.title

    fallback_path = DATA_ROOT / fallback_name
    content = fallback_path.read_text(encoding="utf-8")
    async with session_factory() as session:
        imported = await SourceService(session, user.workspace_id, user.id).import_text(
            title=fallback_path.stem,
            content=content,
            purpose=purpose,
            customer_profile_id=profile_id if purpose == SourcePurpose.CUSTOMER_PROFILE else None,
        )
        source_id, job_id = imported.source.id, imported.job.id
    await run_source_job(job_id, source_id, user.workspace_id, adapter, ai_engine)
    return source_id, "automotive-fallback", fallback_path.stem


async def main() -> None:
    adapter = get_feishu_adapter()
    ai_engine = get_ai_engine()
    embedding_provider = get_embedding_provider()
    user = await _active_user(adapter)
    session_factory = get_session_factory()
    async with session_factory() as session:
        profile, _ = await CustomerProfileService(session, user.workspace_id).create("东岳智行")
        profile_id = profile.id

    imported = []
    profile_source_ids = []
    all_source_ids = []
    for url, purpose, fallback_name in DOCUMENTS:
        source_id, route, title = await _import_one(
            user, profile_id, adapter, ai_engine, url, purpose, fallback_name
        )
        all_source_ids.append(source_id)
        if purpose == SourcePurpose.CUSTOMER_PROFILE:
            profile_source_ids.append(source_id)
        imported.append({"title": title, "purpose": purpose.value, "route": route})

    async with session_factory() as session:
        job = await CustomerProfileService(session, user.workspace_id, user.id).generate(
            profile_id,
            source_ids=profile_source_ids,
            supplemental_text=None,
        )
        profile_job_id = job.id
    await run_profile_job(profile_job_id, profile_id, user.workspace_id, ai_engine)

    approved_experiences = 0
    approved_capabilities = 0
    async with session_factory() as session:
        experiences = list(
            (
                await session.scalars(
                    select(Experience).where(
                        Experience.workspace_id == user.workspace_id,
                        Experience.source_id.in_(all_source_ids),
                        Experience.review_status == ReviewStatus.PENDING_REVIEW,
                    )
                )
            ).all()
        )
        for item in experiences:
            await ExperienceService(
                session, user.workspace_id, user.id, embedding_provider
            ).review(item.id, "approve", "汽车制造录制验收")
            approved_experiences += 1
        capabilities = list(
            (
                await session.scalars(
                    select(Capability).where(
                        Capability.workspace_id == user.workspace_id,
                        Capability.source_id.in_(all_source_ids),
                        Capability.review_status == ReviewStatus.PENDING_REVIEW,
                    )
                )
            ).all()
        )
        for item in capabilities:
            await CapabilityService(
                session, user.workspace_id, user.id, embedding_provider
            ).review(item.id, "approve", "汽车制造录制验收")
            approved_capabilities += 1

    async with session_factory() as session:
        await CustomerProfileService(session, user.workspace_id, user.id).confirm(profile_id)

    bitable = None
    bitable_error = None
    try:
        async with session_factory() as session:
            bitable = await BitableSyncService(
                session,
                user.workspace_id,
                adapter,
                user.feishu_access_token,
            ).sync_daily()
    except Exception as exc:  # keep data preparation useful if re-authorization is needed
        bitable_error = str(exc)[:500]

    print(
        json.dumps(
            {
                "workspace_id": str(user.workspace_id),
                "profile_id": str(profile_id),
                "imported": imported,
                "approved_experiences": approved_experiences,
                "approved_capabilities": approved_capabilities,
                "bitable": bitable,
                "bitable_error": bitable_error,
            },
            ensure_ascii=False,
        )
    )
    await close_database()


if __name__ == "__main__":
    asyncio.run(main())
