"""Select a faster Bailian model for the workspace and retry the automotive quick run."""

import asyncio
import json
import os

from sqlalchemy import select

from app.db.database import close_database, get_session_factory
from app.db.models import ProcessStatus, Session, SolutionRun, User
from app.services.model_connection_service import configure_workspace_connection
from app.services.solution_trust_service import SolutionTrustService

QUICK_TITLE = "东岳智行 · 智能工厂质量提升会前方案"


async def main() -> None:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not configured")
    session_factory = get_session_factory()
    async with session_factory() as session:
        user = await session.scalar(
            select(User)
            .where(User.feishu_access_token.is_not(None), User.is_deleted.is_(False))
            .order_by(User.updated_at.desc())
        )
        if user is None:
            raise RuntimeError("No active user was found")
        connection = await configure_workspace_connection(
            session,
            user.workspace_id,
            user.id,
            "ai",
            "dashscope",
            "qwen-flash",
            api_key,
        )
        run = await session.scalar(
            select(SolutionRun)
            .join(Session, Session.id == SolutionRun.session_id)
            .where(
                SolutionRun.workspace_id == user.workspace_id,
                Session.title == QUICK_TITLE,
                SolutionRun.is_deleted.is_(False),
            )
            .order_by(SolutionRun.created_at.desc())
        )
        if run is None:
            raise RuntimeError("Automotive quick solution run was not found")
        run = await SolutionTrustService(session, user.workspace_id, user.id).retry(run.id)
        run_id = run.id

    for _ in range(90):
        async with session_factory() as session:
            run = await session.get(SolutionRun, run_id)
            if run is not None and run.status in {ProcessStatus.COMPLETED, ProcessStatus.FAILED}:
                print(
                    json.dumps(
                        {
                            "provider": connection.provider,
                            "model": connection.model,
                            "run_id": str(run.id),
                            "status": run.status.value,
                            "stage": run.stage,
                            "error_code": run.error_code,
                            "result_version": run.result_version,
                            "has_result": bool(run.result),
                            "created_at": run.created_at.isoformat(),
                            "completed_at": run.completed_at.isoformat()
                            if run.completed_at
                            else None,
                        },
                        ensure_ascii=False,
                    )
                )
                await close_database()
                return
        await asyncio.sleep(1)
    raise TimeoutError("Retried quick run did not finish in 90 seconds")


if __name__ == "__main__":
    asyncio.run(main())
