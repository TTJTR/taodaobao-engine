"""Run and report a real Feishu Bitable synchronization for the active workspace."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.api.deps import get_feishu_adapter
from app.db.database import close_database, get_session_factory
from app.db.models import User
from app.services.bitable_sync_service import BitableSyncService


async def main() -> None:
    adapter = get_feishu_adapter()
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
        try:
            result = await BitableSyncService(
                session,
                user.workspace_id,
                adapter,
                user.feishu_access_token,
            ).sync_daily()
            print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        except Exception as exc:
            response = getattr(exc, "response", None)
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "response": response.text[:1000] if response is not None else None,
                    },
                    ensure_ascii=False,
                )
            )
    await close_database()


if __name__ == "__main__":
    asyncio.run(main())
