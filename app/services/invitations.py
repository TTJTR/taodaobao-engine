"""Persistent single-use invitation redemption ledger."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from app.db.database import get_session_factory
from app.db.models import InvitationRedemption


class InvitationRedemptionStore(Protocol):
    async def redeem(self, token_id_hash: str, expires_at: datetime) -> bool: ...
    async def consume(self, token_id_hash: str) -> bool: ...


class PostgresInvitationRedemptionStore:
    async def redeem(self, token_id_hash: str, expires_at: datetime) -> bool:
        factory = get_session_factory()
        async with factory() as session:
            statement = (
                insert(InvitationRedemption)
                .values(token_id_hash=token_id_hash, expires_at=expires_at)
                .on_conflict_do_nothing(index_elements=["token_id_hash"])
                .returning(InvitationRedemption.id)
            )
            created = (await session.execute(statement)).scalar_one_or_none()
            await session.commit()
            return created is not None

    async def consume(self, token_id_hash: str) -> bool:
        factory = get_session_factory()
        async with factory() as session:
            now = datetime.now(UTC)
            statement = (
                update(InvitationRedemption)
                .where(
                    InvitationRedemption.token_id_hash == token_id_hash,
                    InvitationRedemption.consumed_at.is_(None),
                    InvitationRedemption.expires_at >= now,
                )
                .values(consumed_at=now)
                .returning(InvitationRedemption.id)
            )
            consumed = (await session.execute(statement)).scalar_one_or_none()
            await session.commit()
            return consumed is not None
