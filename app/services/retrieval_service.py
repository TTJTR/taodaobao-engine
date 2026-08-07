import uuid
from datetime import UTC, datetime

from sqlalchemy import String, cast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Capability, Experience, ReviewStatus


class RetrievalService:
    def __init__(self, session: AsyncSession, workspace_id: uuid.UUID) -> None:
        self.session = session
        self.workspace_id = workspace_id

    async def retrieve(self, query: str) -> dict:
        experience_statement = self._statement(Experience, query, limit=3)
        capability_statement = self._statement(Capability, query, limit=5)
        experiences = list((await self.session.scalars(experience_statement)).all())
        capabilities = list((await self.session.scalars(capability_statement)).all())
        return {
            "experiences": [self._snapshot(item) for item in experiences],
            "capabilities": [self._snapshot(item) for item in capabilities],
            "created_at": datetime.now(UTC).isoformat(),
        }

    def _statement(self, model, query: str, *, limit: int):
        filters = [
            model.workspace_id == self.workspace_id,
            model.review_status == ReviewStatus.VERIFIED,
            model.is_deleted.is_(False),
        ]
        terms = [term for term in query.strip().split() if term][:5]
        order_by = [model.updated_at.desc()]
        if terms:
            match = or_(
                *(cast(model.data, String).ilike(f"%{term}%") for term in terms)
            )
            order_by.insert(0, match.desc())
        return select(model).where(*filters).order_by(*order_by).limit(limit)

    @staticmethod
    def _snapshot(asset) -> dict:
        return {
            "id": str(asset.id),
            "source_id": str(asset.source_id),
            "data": asset.data,
            "review_status": asset.review_status.value,
            "updated_at": asset.updated_at.isoformat(),
        }
