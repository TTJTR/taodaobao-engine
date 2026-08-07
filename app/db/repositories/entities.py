from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import selectinload

from app.db.models import (
    Capability,
    CustomerProfile,
    Experience,
    Job,
    Message,
    Session,
    SolutionRun,
    Source,
    User,
)
from app.db.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_feishu_user_id(self, feishu_user_id: str) -> User | None:
        statement = select(User).where(
            User.feishu_user_id == feishu_user_id,
            *self._active_filters(),
        )
        return await self.session.scalar(statement)


class SourceRepository(BaseRepository[Source]):
    model = Source

    async def get_by_source_url(self, source_url: str) -> Source | None:
        statement = select(Source).where(
            Source.source_url == source_url,
            *self._active_filters(),
        )
        return await self.session.scalar(statement)

    async def list_filtered(
        self,
        *,
        offset: int,
        limit: int,
        keyword: str | None = None,
        status: str | None = None,
    ) -> list[Source]:
        filters = self._source_filters(keyword=keyword, status=status)
        statement = (
            select(Source)
            .where(*filters)
            .order_by(Source.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def count_filtered(
        self, *, keyword: str | None = None, status: str | None = None
    ) -> int:
        statement = (
            select(func.count())
            .select_from(Source)
            .where(*self._source_filters(keyword=keyword, status=status))
        )
        return int(await self.session.scalar(statement) or 0)

    def _source_filters(self, *, keyword: str | None, status: str | None) -> tuple:
        filters = list(self._active_filters())
        if keyword:
            pattern = f"%{keyword}%"
            filters.append(or_(Source.title.ilike(pattern), Source.content.ilike(pattern)))
        if status:
            filters.append(Source.status == status)
        return tuple(filters)


class CustomerProfileRepository(BaseRepository[CustomerProfile]):
    model = CustomerProfile

    async def get(self, entity_id):
        statement = (
            select(CustomerProfile)
            .options(selectinload(CustomerProfile.sources))
            .where(CustomerProfile.id == entity_id, *self._active_filters())
        )
        return await self.session.scalar(statement)

    async def get_by_customer_name(self, customer_name: str) -> CustomerProfile | None:
        statement = (
            select(CustomerProfile)
            .options(selectinload(CustomerProfile.sources))
            .where(CustomerProfile.customer_name == customer_name, *self._active_filters())
        )
        return await self.session.scalar(statement)

    async def list_filtered(
        self, *, offset: int, limit: int, keyword: str | None = None
    ) -> list[CustomerProfile]:
        filters = list(self._active_filters())
        if keyword:
            filters.append(CustomerProfile.customer_name.ilike(f"%{keyword}%"))
        statement = (
            select(CustomerProfile)
            .options(selectinload(CustomerProfile.sources))
            .where(*filters)
            .order_by(CustomerProfile.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def count_filtered(self, *, keyword: str | None = None) -> int:
        filters = list(self._active_filters())
        if keyword:
            filters.append(CustomerProfile.customer_name.ilike(f"%{keyword}%"))
        statement = select(func.count()).select_from(CustomerProfile).where(*filters)
        return int(await self.session.scalar(statement) or 0)


class ExperienceRepository(BaseRepository[Experience]):
    model = Experience

    async def list_filtered(
        self,
        *,
        offset: int,
        limit: int,
        keyword: str | None = None,
        review_status: str | None = None,
    ) -> list[Experience]:
        statement = (
            select(Experience)
            .where(*self._asset_filters(keyword, review_status))
            .order_by(Experience.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def count_filtered(
        self, *, keyword: str | None = None, review_status: str | None = None
    ) -> int:
        statement = (
            select(func.count())
            .select_from(Experience)
            .where(*self._asset_filters(keyword, review_status))
        )
        return int(await self.session.scalar(statement) or 0)

    def _asset_filters(self, keyword: str | None, review_status: str | None) -> tuple:
        filters = list(self._active_filters())
        if keyword:
            filters.append(cast(Experience.data, String).ilike(f"%{keyword}%"))
        if review_status:
            filters.append(Experience.review_status == review_status)
        return tuple(filters)


class CapabilityRepository(BaseRepository[Capability]):
    model = Capability

    async def list_filtered(
        self,
        *,
        offset: int,
        limit: int,
        keyword: str | None = None,
        review_status: str | None = None,
    ) -> list[Capability]:
        statement = (
            select(Capability)
            .where(*self._asset_filters(keyword, review_status))
            .order_by(Capability.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list((await self.session.scalars(statement)).all())

    async def count_filtered(
        self, *, keyword: str | None = None, review_status: str | None = None
    ) -> int:
        statement = (
            select(func.count())
            .select_from(Capability)
            .where(*self._asset_filters(keyword, review_status))
        )
        return int(await self.session.scalar(statement) or 0)

    def _asset_filters(self, keyword: str | None, review_status: str | None) -> tuple:
        filters = list(self._active_filters())
        if keyword:
            filters.append(cast(Capability.data, String).ilike(f"%{keyword}%"))
        if review_status:
            filters.append(Capability.review_status == review_status)
        return tuple(filters)


class SessionRepository(BaseRepository[Session]):
    model = Session


class MessageRepository(BaseRepository[Message]):
    model = Message


class SolutionRunRepository(BaseRepository[SolutionRun]):
    model = SolutionRun


class JobRepository(BaseRepository[Job]):
    model = Job
