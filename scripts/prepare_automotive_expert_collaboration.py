"""Prepare the automotive expert-collaboration draft without creating the group."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.api.deps import get_ai_engine, get_feishu_adapter
from app.db.database import close_database, get_session_factory
from app.db.models import ResearchTask, User
from app.services.expert_collaboration_service import ExpertCollaborationService

RESEARCH_TITLE = "东岳智行多工厂质量知识闭环与推广路线"


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
    feishu = get_feishu_adapter()
    user = await _active_user(feishu)
    session_factory = get_session_factory()
    async with session_factory() as session:
        task = await session.scalar(
            select(ResearchTask)
            .where(
                ResearchTask.workspace_id == user.workspace_id,
                ResearchTask.title == RESEARCH_TITLE,
                ResearchTask.is_deleted.is_(False),
            )
            .order_by(ResearchTask.created_at.desc())
        )
        if task is None:
            raise RuntimeError("Automotive Deep Research task was not found")
        item = await ExpertCollaborationService(
            session,
            user.workspace_id,
            user.id,
            get_ai_engine(),
            feishu,
        ).create(task.id)
        print(
            json.dumps(
                {
                    "collaboration_id": str(item.id),
                    "status": item.status.value,
                    "group_name": item.group_name,
                    "selected_expert_ids": item.selected_expert_ids,
                    "candidate_records": item.candidate_records,
                    "questions": item.questions,
                },
                ensure_ascii=False,
            )
        )
    await close_database()


if __name__ == "__main__":
    asyncio.run(main())
