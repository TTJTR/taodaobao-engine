"""Prepare real quick-solution and Deep Research runs for the automotive demo."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.api.deps import get_ai_engine, get_embedding_provider, get_feishu_adapter
from app.db.database import close_database, get_session_factory
from app.db.models import (
    CustomerProfile,
    ProcessStatus,
    ResearchTask,
    ResearchTaskStatus,
    Session,
    SolutionRun,
    User,
)
from app.services.research_pipeline import run_research_pipeline
from app.services.research_service import ResearchTaskService
from app.services.session_service import SessionService

QUICK_TITLE = "东岳智行 · 智能工厂质量提升会前方案"
QUICK_QUESTION = (
    "客户希望在不替换 MES、QMS 和现有工业相机平台的前提下，先用 90 天完成一条焊装线与"
    "一条总装线的质量追溯试点。请给出有来源的试点路径、能力组合、风险和待确认问题。"
)
RESEARCH_TITLE = "东岳智行多工厂质量知识闭环与推广路线"
RESEARCH_QUESTION = (
    "如何基于东岳智行现有 MES、QMS、PLC、工业相机与飞书协作体系，设计从单线试点到多工厂"
    "复制的质量知识闭环？请比较实施路线，核对历史案例与企业能力，识别证据缺口，并给出"
    "需要专家确认的问题。"
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


async def _profile(session, workspace_id):
    profile = await session.scalar(
        select(CustomerProfile)
        .where(
            CustomerProfile.workspace_id == workspace_id,
            CustomerProfile.customer_name.like("东岳智行%"),
            CustomerProfile.is_deleted.is_(False),
        )
        .order_by(CustomerProfile.updated_at.desc())
    )
    if profile is None:
        raise RuntimeError("Confirmed automotive profile was not found")
    return profile


async def _prepare_quick(user) -> tuple[Session, SolutionRun]:
    session_factory = get_session_factory()
    async with session_factory() as db:
        profile = await _profile(db, user.workspace_id)
        chat = await db.scalar(
            select(Session)
            .where(
                Session.workspace_id == user.workspace_id,
                Session.title == QUICK_TITLE,
                Session.is_deleted.is_(False),
            )
            .order_by(Session.created_at.desc())
        )
        service = SessionService(db, user.workspace_id, user.id)
        if chat is None:
            chat = await service.create(profile.id, QUICK_TITLE)
        run = await db.scalar(
            select(SolutionRun)
            .where(
                SolutionRun.workspace_id == user.workspace_id,
                SolutionRun.session_id == chat.id,
                SolutionRun.status == ProcessStatus.COMPLETED,
            )
            .order_by(SolutionRun.created_at.desc())
        )
        if run is None:
            _, run = await service.create_turn(chat.id, QUICK_QUESTION)
        run_id = run.id
        chat_id = chat.id

    for _ in range(150):
        async with session_factory() as db:
            run = await db.get(SolutionRun, run_id)
            if run is not None and run.status in {ProcessStatus.COMPLETED, ProcessStatus.FAILED}:
                chat = await db.get(Session, chat_id)
                return chat, run
        await asyncio.sleep(1)
    raise TimeoutError("Quick solution did not reach a terminal state in 150 seconds")


async def _prepare_research(user, chat: Session) -> ResearchTask:
    session_factory = get_session_factory()
    async with session_factory() as db:
        profile = await _profile(db, user.workspace_id)
        task = await db.scalar(
            select(ResearchTask)
            .where(
                ResearchTask.workspace_id == user.workspace_id,
                ResearchTask.title == RESEARCH_TITLE,
                ResearchTask.is_deleted.is_(False),
            )
            .order_by(ResearchTask.created_at.desc())
        )
        service = ResearchTaskService(db, user.workspace_id, user.id)
        if task is None:
            task = await service.create(
                customer_profile_id=profile.id,
                session_id=chat.id,
                title=RESEARCH_TITLE,
                question=RESEARCH_QUESTION,
                completion_conditions=[
                    "区分历史事实、企业能力、AI推断与待确认信息",
                    "给出试点、扩围和多工厂推广路线比较",
                    "保留原始证据快照与作者信息",
                    "列出需要专家确认的知识缺口",
                ],
            )
        task_id = task.id
        if task.status == ResearchTaskStatus.FAILED:
            task = await service.retry(task.id)

    if task.status not in {
        ResearchTaskStatus.COMPLETED,
        ResearchTaskStatus.WAITING_EXPERT,
        ResearchTaskStatus.CANCELLED,
    }:
        await run_research_pipeline(
            task_id,
            user.workspace_id,
            user.id,
            get_ai_engine(),
            get_embedding_provider(),
        )
    async with session_factory() as db:
        task = await db.get(ResearchTask, task_id)
        if task is None:
            raise RuntimeError("Deep Research task disappeared")
        return task


async def main() -> None:
    user = await _active_user(get_feishu_adapter())
    chat, quick = await _prepare_quick(user)
    research = await _prepare_research(user, chat)
    print(
        json.dumps(
            {
                "profile": "东岳智行汽车制造有限公司",
                "quick_session_id": str(chat.id),
                "quick_run_id": str(quick.id),
                "quick_status": quick.status.value,
                "quick_stage": quick.stage,
                "quick_error": quick.error_code,
                "research_task_id": str(research.id),
                "research_status": research.status.value,
                "research_stage": research.stage,
                "research_progress": research.progress,
                "research_error": research.error_summary,
            },
            ensure_ascii=False,
        )
    )
    await close_database()


if __name__ == "__main__":
    asyncio.run(main())
