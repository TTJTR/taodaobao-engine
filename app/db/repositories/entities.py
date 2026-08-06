from sqlalchemy import select

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
