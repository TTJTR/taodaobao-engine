"""Create database-backed short invitation codes for judges."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy.dialects.postgresql import insert

from app.core.config import settings
from app.core.invitations import (
    generate_short_invitation_code,
    hash_short_invitation_code,
    normalize_short_invitation_code,
)
from app.db.database import close_database, get_session_factory
from app.db.models import InvitationRedemption


async def create_codes(
    *,
    days: int,
    max_uses: int,
    count: int,
    requested_code: str | None = None,
) -> list[str]:
    secret = settings.invitation_signing_secret or ""
    if len(secret) < 32:
        raise RuntimeError("APP_INVITATION_SIGNING_SECRET is not configured")
    if requested_code is not None and count != 1:
        raise ValueError("--code can only be used with --count 1")

    expires_at = datetime.now(UTC) + timedelta(days=days)
    factory = get_session_factory()
    created: list[str] = []
    async with factory() as session:
        for _ in range(count):
            for attempt in range(20):
                code = requested_code or generate_short_invitation_code()
                normalized = normalize_short_invitation_code(code)
                if normalized is None:
                    raise ValueError("--code must match tdb-xxxxx using unambiguous letters/digits")
                code_hash = hash_short_invitation_code(normalized, secret)
                assert code_hash is not None
                statement = (
                    insert(InvitationRedemption)
                    .values(
                        token_id_hash=code_hash,
                        expires_at=expires_at,
                        max_uses=max_uses,
                        redeemed_count=0,
                    )
                    .on_conflict_do_nothing(index_elements=["token_id_hash"])
                    .returning(InvitationRedemption.id)
                )
                inserted = (await session.execute(statement)).scalar_one_or_none()
                if inserted is not None:
                    created.append(normalized)
                    break
                if requested_code is not None:
                    raise RuntimeError("the requested short invitation code already exists")
                if attempt == 19:
                    raise RuntimeError("could not allocate a unique short invitation code")
        await session.commit()
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument("--max-uses", type=int, default=100)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--code", help="optional explicit tdb-xxxxx code")
    args = parser.parse_args()
    if not 1 <= args.days <= 30:
        parser.error("--days must be between 1 and 30")
    if not 1 <= args.max_uses <= 100:
        parser.error("--max-uses must be between 1 and 100")
    if not 1 <= args.count <= 100:
        parser.error("--count must be between 1 and 100")

    async def run() -> list[str]:
        try:
            return await create_codes(
                days=args.days,
                max_uses=args.max_uses,
                count=args.count,
                requested_code=args.code,
            )
        finally:
            await close_database()

    for code in asyncio.run(run()):
        print(code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
