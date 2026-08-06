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

