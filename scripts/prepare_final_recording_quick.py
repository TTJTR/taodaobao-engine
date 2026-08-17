"""Prepare one clean, evidence-bearing quick-solution session for recording."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.database import close_database, get_session_factory
from app.db.models import CustomerProfile, ProcessStatus, Session, SolutionRun, User
from app.services.session_service import SessionService

PROFILE_NAME = "东岳智行汽车制造有限公司"
SESSION_TITLE = "东岳智行 · 90天质量追溯会前方案（录制版）"
QUESTION = (
    "客户希望在不替换 MES、QMS 和现有工业相机平台的前提下，"
    "用 90 天完成焊装一线与总装三线的质量追溯试点。"
    "请结合已校验历史经验与原子能力，给出有来源的试点路径、能力组合、风险、"
    "可信边界和待确认问题。"
)


async def _create_attempt(user: User, profile: CustomerProfile):
    session_factory = get_session_factory()
    async with session_factory() as session:
        service = SessionService(session, user.workspace_id, user.id)
        chat = await service.create(profile.id, SESSION_TITLE)
        _, run = await service.create_turn(chat.id, QUESTION)
        return chat.id, run.id


async def _wait_for_run(run_id) -> tuple[SolutionRun, bool]:
    session_factory = get_session_factory()
    for _ in range(70):
        async with session_factory() as session:
            run = await session.get(SolutionRun, run_id)
            if run is None:
                raise RuntimeError("Quick run disappeared")
            result = run.result or {}
            evidence_ready = bool(result.get("historical_evidence") and result.get("sources"))
            if run.status == ProcessStatus.COMPLETED and evidence_ready:
                return run, True
            if datetime.now(UTC) > run.deadline_at + timedelta(seconds=3):
                return run, False
        await asyncio.sleep(1)
    raise TimeoutError("Quick run did not settle")


async def _hide_failed_session(session_id) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        chat = await session.get(Session, session_id)
        if chat is not None:
            chat.is_deleted = True
            await session.commit()


async def main() -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        user = await session.scalar(
            select(User)
            .where(User.feishu_access_token.is_not(None), User.is_deleted.is_(False))
            .order_by(User.updated_at.desc())
        )
        if user is None:
            raise RuntimeError("No active Feishu user")
        profile = await session.scalar(
            select(CustomerProfile).where(
                CustomerProfile.workspace_id == user.workspace_id,
                CustomerProfile.customer_name == PROFILE_NAME,
                CustomerProfile.is_deleted.is_(False),
            )
        )
        if profile is None:
            raise RuntimeError("Automotive profile not found")

    outcomes = []
    for attempt in range(1, 4):
        session_id, run_id = await _create_attempt(user, profile)
        run, ready = await _wait_for_run(run_id)
        elapsed = (
            round((run.completed_at - run.created_at).total_seconds(), 1)
            if run.completed_at
            else None
        )
        outcomes.append(
            {
                "attempt": attempt,
                "session_id": str(session_id),
                "run_id": str(run.id),
                "status": run.status.value,
                "elapsed_seconds": elapsed,
                "evidence_ready": ready,
            }
        )
        if ready:
            print(json.dumps({"ready": True, "outcomes": outcomes}, ensure_ascii=False))
            await close_database()
            return
        await _hide_failed_session(session_id)
    print(json.dumps({"ready": False, "outcomes": outcomes}, ensure_ascii=False))
    await close_database()
    raise RuntimeError("No evidence-bearing quick run completed within three attempts")


if __name__ == "__main__":
    asyncio.run(main())
