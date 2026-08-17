"""Finish the automotive recording workspace after source ingestion."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.api.deps import get_ai_engine, get_feishu_adapter
from app.db.database import close_database, get_session_factory
from app.db.models import CustomerProfile, Job, ProcessStatus, Source, User
from app.services.bitable_sync_service import BitableSyncService
from app.services.job_runner import run_profile_job
from app.services.profile_service import CustomerProfileService


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


async def main() -> None:
    adapter = get_feishu_adapter()
    ai_engine = get_ai_engine()
    user = await _active_user(adapter)
    session_factory = get_session_factory()

    async with session_factory() as session:
        profile = await session.scalar(
            select(CustomerProfile)
            .where(
                CustomerProfile.workspace_id == user.workspace_id,
                CustomerProfile.customer_name == "东岳智行",
                CustomerProfile.is_deleted.is_(False),
            )
            .order_by(CustomerProfile.created_at.desc())
        )
        if profile is None:
            raise RuntimeError("Automotive profile was not found")
        profile_id = profile.id
        source_ids = list(
            (
                await session.scalars(
                    select(Source.id).where(
                        Source.workspace_id == user.workspace_id,
                        Source.customer_profile_id == profile_id,
                        Source.is_deleted.is_(False),
                    )
                )
            ).all()
        )
        job = await CustomerProfileService(session, user.workspace_id, user.id).generate(
            profile_id,
            source_ids=source_ids,
            supplemental_text=None,
        )
        job_id = job.id

    await run_profile_job(job_id, profile_id, user.workspace_id, ai_engine)

    async with session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status != ProcessStatus.COMPLETED:
            detail = job.error_summary if job is not None else "job missing"
            raise RuntimeError(f"Profile generation failed: {detail}")
        profile = await CustomerProfileService(session, user.workspace_id, user.id).confirm(
            profile_id
        )

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
    except Exception as exc:
        bitable_error = str(exc)[:500]

    print(
        json.dumps(
            {
                "workspace_id": str(user.workspace_id),
                "profile_id": str(profile_id),
                "profile_status": profile.status.value,
                "profile_name": profile.customer_name,
                "bitable": bitable,
                "bitable_error": bitable_error,
            },
            ensure_ascii=False,
        )
    )
    await close_database()


if __name__ == "__main__":
    asyncio.run(main())
