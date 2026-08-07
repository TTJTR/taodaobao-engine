from sqlalchemy import func, or_, select

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


class ExperienceRepository(BaseRepository[Experience]):
    model = Experience


class CapabilityRepository(BaseRepository[Capability]):
    model = Capability


class SessionRepository(BaseRepository[Session]):
    model = Session


class MessageRepository(BaseRepository[Message]):
    model = Message


class SolutionRunRepository(BaseRepository[SolutionRun]):
    model = SolutionRun


class JobRepository(BaseRepository[Job]):
    model = Job
