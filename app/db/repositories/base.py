import uuid
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import EntityMixin, WorkspaceMixin

ModelT = TypeVar("ModelT", bound=EntityMixin)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: AsyncSession, workspace_id: uuid.UUID) -> None:
        if not issubclass(self.model, WorkspaceMixin):
            raise TypeError(f"{self.model.__name__} must include WorkspaceMixin")
        self.session = session
        self.workspace_id = workspace_id

    def _active_filters(self) -> tuple[Any, Any]:
        return (
            self.model.workspace_id == self.workspace_id,  # type: ignore[attr-defined]
            self.model.is_deleted.is_(False),
        )

    async def create(self, **values: Any) -> ModelT:
        values["workspace_id"] = self.workspace_id
        entity = self.model(**values)
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def get(self, entity_id: uuid.UUID) -> ModelT | None:
        statement = select(self.model).where(
            self.model.id == entity_id,
            *self._active_filters(),
        )
        return await self.session.scalar(statement)

    async def list(self, *, offset: int = 0, limit: int = 20) -> Sequence[ModelT]:
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        statement = (
            select(self.model)
            .where(*self._active_filters())
            .order_by(self.model.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.session.scalars(statement)
        return result.all()

    async def count(self) -> int:
        statement = (
            select(func.count())
            .select_from(self.model)
            .where(*self._active_filters())
        )
        return int(await self.session.scalar(statement) or 0)

    async def update(self, entity: ModelT, **values: Any) -> ModelT:
        protected_fields = {"id", "workspace_id", "created_at", "is_deleted"}
        invalid_fields = protected_fields.intersection(values)
        if invalid_fields:
            names = ", ".join(sorted(invalid_fields))
            raise ValueError(f"protected fields cannot be updated: {names}")

        mapped_columns = self.model.__table__.columns
        unknown_fields = set(values).difference(mapped_columns.keys())
        if unknown_fields:
            names = ", ".join(sorted(unknown_fields))
            raise ValueError(f"unknown fields: {names}")

        for field, value in values.items():
            setattr(entity, field, value)
        await self.session.flush()
        return entity

    async def soft_delete(self, entity: ModelT) -> None:
        entity.is_deleted = True
        await self.session.flush()

