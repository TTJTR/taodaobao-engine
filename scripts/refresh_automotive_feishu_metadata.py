"""Refresh real Feishu author/collaborator metadata for the recording sources.

This deliberately does not rerun AI extraction or change review status. It only
updates source metadata and the expert-contributor index after OAuth scopes have
been re-authorized.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.api.deps import get_feishu_adapter
from app.db.database import close_database, get_session_factory
from app.db.models import Source, User
from app.services.job_runner import _record_source_contributors, _resolve_collaborator_names

DOCUMENT_TOKENS = {
    "BaF2dKxv1o0vxAxFCQocFTUanPb",
    "T5IDdzkfHoWA8Qxy22ycb5Vqnnc",
    "QbnXddkQ6ojoWwxqEEQcEsAznqC",
    "BDlHdegElosR9oxZPH3cDaUYnbc",
    "YNgTdFOayoHji6x2pmjcUpPJnjg",
    "RpIhdEzPjon3ApxQdSrcNbCjnug",
    "OrAUdADonoKx9lxJQt6cNE9lnmf",
    "Q3ZZd3xCioCloixMgAFcqaCznrc",
    "AgUwdZFzNo9xPzxxoEKcbLRqnGe",
    "WM4rd9dWHo1pbLxQpiCcx1K1nfb",
}


async def main() -> None:
    adapter = get_feishu_adapter()
    session_factory = get_session_factory()
    refreshed: list[dict[str, object]] = []
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

        sources = list(
            (
                await session.scalars(
                    select(Source).where(
                        Source.workspace_id == user.workspace_id,
                        Source.source_url.is_not(None),
                        Source.is_deleted.is_(False),
                    )
                )
            ).all()
        )
        for source in sources:
            if not any(token in (source.source_url or "") for token in DOCUMENT_TOKENS):
                continue
            document = await adapter.fetch_document(
                source.source_url or "", user.feishu_access_token
            )
            collaborators = await _resolve_collaborator_names(
                session, user.workspace_id, document.collaborators
            )
            source.title = document.title
            visible_authors = [item.name for item in collaborators if item.is_owner]
            source.author = "、".join(dict.fromkeys(visible_authors)) or document.author
            source.permission_checked_at = datetime.now(UTC)
            await _record_source_contributors(
                session, user.workspace_id, source, collaborators
            )
            refreshed.append(
                {
                    "source_id": str(source.id),
                    "title": source.title,
                    "author": source.author,
                    "collaborator_count": len(collaborators),
                }
            )
        await session.commit()

    print(json.dumps({"refreshed": refreshed}, ensure_ascii=False))
    await close_database()


if __name__ == "__main__":
    asyncio.run(main())
