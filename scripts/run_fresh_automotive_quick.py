"""Create and wait for one fresh automotive quick-solution run."""

import asyncio
import json

from sqlalchemy import select

from app.db.database import close_database, get_session_factory
from app.db.models import CustomerProfile, ProcessStatus, Session, SolutionRun, User
from app.services.session_service import SessionService

PROFILE_NAME = "东岳智行汽车制造有限公司"
SESSION_TITLE = "东岳智行 · 智能工厂质量提升会前方案"
QUESTION = (
    "客户希望在不替换 MES、QMS 和现有工业相机平台的前提下，"
    "先用 90 天完成一条焊装线和一条总装线的质量追溯试点。"
    "请结合已校验的历史经验与原子能力，给出有来源的试点路径、能力组合、风险、可信边界和待确认问题。"
)


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
        chat = await session.scalar(
            select(Session)
            .where(
                Session.workspace_id == user.workspace_id,
                Session.title == SESSION_TITLE,
                Session.is_deleted.is_(False),
            )
            .order_by(Session.updated_at.desc())
        )
        service = SessionService(session, user.workspace_id, user.id)
        if chat is None:
            chat = await service.create(profile.id, SESSION_TITLE)
        _, run = await service.create_turn(chat.id, QUESTION)
        run_id = run.id

    for _ in range(90):
        async with session_factory() as session:
            run = await session.get(SolutionRun, run_id)
            if run is not None and run.status in {ProcessStatus.COMPLETED, ProcessStatus.FAILED}:
                elapsed = None
                if run.completed_at is not None:
                    elapsed = round((run.completed_at - run.created_at).total_seconds(), 1)
                print(
                    json.dumps(
                        {
                            "run_id": str(run.id),
                            "status": run.status.value,
                            "stage": run.stage,
                            "error_code": run.error_code,
                            "elapsed_seconds": elapsed,
                            "has_result": bool(run.result),
                            "trust_action": (run.result or {}).get("trust_action"),
                        },
                        ensure_ascii=False,
                    )
                )
                await close_database()
                return
        await asyncio.sleep(1)
    raise TimeoutError("Quick solution did not finish in 90 seconds")


if __name__ == "__main__":
    asyncio.run(main())
