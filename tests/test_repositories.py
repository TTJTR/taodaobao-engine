import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.dialects import postgresql

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
from app.db.repositories import (
    CapabilityRepository,
    CustomerProfileRepository,
    ExperienceRepository,
    JobRepository,
    MessageRepository,
    SessionRepository,
    SolutionRunRepository,
    SourceRepository,
    UserRepository,
)


def compile_statement(statement: object) -> str:
    return str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_all_entity_repositories_bind_the_expected_model() -> None:
    bindings = {
        UserRepository: User,
        SourceRepository: Source,
        CustomerProfileRepository: CustomerProfile,
        ExperienceRepository: Experience,
        CapabilityRepository: Capability,
        SessionRepository: Session,
        MessageRepository: Message,
        SolutionRunRepository: SolutionRun,
        JobRepository: Job,
    }

    assert all(repository.model is model for repository, model in bindings.items())


@pytest.mark.asyncio
async def test_get_filters_workspace_and_soft_deleted_rows() -> None:
    session = AsyncMock()
    session.scalar.return_value = None
    workspace_id = uuid.uuid4()
    repository = SourceRepository(session, workspace_id)

    assert await repository.get(uuid.uuid4()) is None

    sql = compile_statement(session.scalar.await_args.args[0])
    assert "sources.workspace_id" in sql
    assert "sources.is_deleted IS false" in sql


@pytest.mark.asyncio
async def test_source_url_lookup_filters_workspace_and_soft_deleted_rows() -> None:
    session = AsyncMock()
    repository = SourceRepository(session, uuid.uuid4())

    await repository.get_by_source_url("https://example.feishu.cn/docx/demo")

    sql = compile_statement(session.scalar.await_args.args[0])
    assert "sources.source_url" in sql
    assert "sources.workspace_id" in sql
    assert "sources.is_deleted IS false" in sql


@pytest.mark.asyncio
async def test_source_filtered_list_applies_keyword_and_status() -> None:
    session = AsyncMock()
    result = Mock()
    result.all.return_value = []
    session.scalars.return_value = result
    repository = SourceRepository(session, uuid.uuid4())

    await repository.list_filtered(
        offset=0,
        limit=20,
        keyword="零售",
        status="pending_review",
    )

    sql = compile_statement(session.scalars.await_args.args[0])
    assert "sources.title ILIKE" in sql
    assert "sources.status = 'pending_review'" in sql


@pytest.mark.asyncio
async def test_list_filters_soft_deleted_rows_and_applies_pagination() -> None:
    session = AsyncMock()
    scalar_result = Mock()
    scalar_result.all.return_value = []
    session.scalars.return_value = scalar_result
    repository = SourceRepository(session, uuid.uuid4())

    assert await repository.list(offset=20, limit=10) == []

    sql = compile_statement(session.scalars.await_args.args[0])
    assert "sources.is_deleted IS false" in sql
    assert "LIMIT 10 OFFSET 20" in sql


@pytest.mark.asyncio
async def test_create_forces_repository_workspace() -> None:
    session = AsyncMock()
    session.add = Mock()
    workspace_id = uuid.uuid4()
    repository = SourceRepository(session, workspace_id)

    source = await repository.create(
        imported_by_id=uuid.uuid4(),
        type="pasted_text",
        purpose="experience",
        title="Demo source",
        workspace_id=uuid.uuid4(),
    )

    assert isinstance(source, Source)
    assert source.workspace_id == workspace_id
    session.add.assert_called_once_with(source)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_rejects_protected_and_unknown_fields() -> None:
    repository = SourceRepository(AsyncMock(), uuid.uuid4())
    source = Source()

    with pytest.raises(ValueError, match="protected fields"):
        await repository.update(source, workspace_id=uuid.uuid4())
    with pytest.raises(ValueError, match="unknown fields"):
        await repository.update(source, not_a_column="value")


@pytest.mark.asyncio
async def test_soft_delete_marks_entity_and_flushes() -> None:
    session = AsyncMock()
    repository = SourceRepository(session, uuid.uuid4())
    source = Source()
    source.is_deleted = False

    await repository.soft_delete(source)

    assert source.is_deleted is True
    session.flush.assert_awaited_once()
