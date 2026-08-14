"""Persistent, concurrency-safe invitation redemption ledger."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import and_, exists, select, update
from sqlalchemy.dialects.postgresql import insert

from app.db.database import get_session_factory
from app.db.models import InvitationRedemption, InvitationRedemptionUse


class InvitationRedemptionStore(Protocol):
    async def redeem(
        self, token_id_hash: str, expires_at: datetime, max_uses: int
    ) -> int | None: ...
    async def consume(self, token_id_hash: str, redemption_number: int) -> bool: ...


class PostgresInvitationRedemptionStore:
    async def redeem(
        self, token_id_hash: str, expires_at: datetime, max_uses: int
    ) -> int | None:
        factory = get_session_factory()
        async with factory() as session:
            now = datetime.now(UTC)
            statement = (
                insert(InvitationRedemption)
                .values(
                    token_id_hash=token_id_hash,
                    expires_at=expires_at,
                    max_uses=max_uses,
                    redeemed_count=1,
                )
                .on_conflict_do_update(
                    index_elements=["token_id_hash"],
                    set_={"redeemed_count": InvitationRedemption.redeemed_count + 1},
                    where=and_(
                        InvitationRedemption.expires_at >= now,
                        InvitationRedemption.max_uses == max_uses,
                        InvitationRedemption.redeemed_count
                        < InvitationRedemption.max_uses,
                    ),
                )
                .returning(InvitationRedemption.redeemed_count)
            )
            redemption_number = (await session.execute(statement)).scalar_one_or_none()
            if redemption_number is None:
                await session.rollback()
                return None
            session.add(
                InvitationRedemptionUse(
                    token_id_hash=token_id_hash,
                    redemption_number=redemption_number,
                )
            )
            await session.commit()
            return redemption_number

    async def consume(self, token_id_hash: str, redemption_number: int) -> bool:
        factory = get_session_factory()
        async with factory() as session:
            now = datetime.now(UTC)
            valid_token = exists(
                select(InvitationRedemption.id).where(
                    InvitationRedemption.token_id_hash == token_id_hash,
                    InvitationRedemption.expires_at >= now,
                )
            )
            statement = (
                update(InvitationRedemptionUse)
                .where(
                    InvitationRedemptionUse.token_id_hash == token_id_hash,
                    InvitationRedemptionUse.redemption_number == redemption_number,
                    InvitationRedemptionUse.consumed_at.is_(None),
                    valid_token,
                )
                .values(consumed_at=now)
                .returning(InvitationRedemptionUse.id)
            )
            consumed = (await session.execute(statement)).scalar_one_or_none()
            await session.commit()
            return consumed is not None
