"""Confirm the prepared automotive collaboration and create the real Feishu group."""

import asyncio
import json
import uuid

from sqlalchemy import select

from app.api.deps import get_ai_engine, get_feishu_adapter
from app.db.database import close_database, get_session_factory
from app.db.models import ExpertCollaboration, User
from app.services.expert_collaboration_service import ExpertCollaborationService

COLLABORATION_ID = uuid.UUID("7fed89bb-9696-491f-a39d-5d4a9df1319e")


async def main() -> None:
    feishu = get_feishu_adapter()
    session_factory = get_session_factory()
    async with session_factory() as session:
        user = await session.scalar(
            select(User)
            .where(User.feishu_access_token.is_not(None), User.is_deleted.is_(False))
            .order_by(User.updated_at.desc())
        )
        if user is None:
            raise RuntimeError("No active Feishu user")
        if user.feishu_refresh_token:
            token = await feishu.refresh_access_token(user.feishu_refresh_token)
            user.feishu_access_token = token.access_token
            user.feishu_refresh_token = token.refresh_token
            user.feishu_token_expires_at = token.expires_at
            await session.commit()
        collaboration = await session.get(ExpertCollaboration, COLLABORATION_ID)
        if collaboration is None:
            raise RuntimeError("Prepared collaboration not found")
        item = await ExpertCollaborationService(
            session,
            user.workspace_id,
            user.id,
            get_ai_engine(),
            feishu,
        ).confirm(collaboration.id)
        print(
            json.dumps(
                {
                    "collaboration_id": str(item.id),
                    "status": item.status.value,
                    "group_name": item.group_name,
                    "feishu_group_id": item.feishu_group_id,
                    "feishu_document_url": item.feishu_document_url,
                    "error_code": item.error_code,
                },
                ensure_ascii=False,
            )
        )
    await close_database()


if __name__ == "__main__":
    asyncio.run(main())
