import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.dialects import postgresql

from app.services.retrieval_service import RetrievalService


def compile_statement(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


@pytest.mark.asyncio
async def test_retrieval_enforces_verified_active_workspace_and_top_k() -> None:
    session = AsyncMock()
    experience_result = Mock()
    capability_result = Mock()
    asset = SimpleNamespace(
        id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        data={"name": "旁路试点"},
        review_status=SimpleNamespace(value="verified"),
        updated_at=datetime.now(UTC),
    )
    experience_result.all.return_value = [asset]
    capability_result.all.return_value = [asset]
    session.scalars.side_effect = [experience_result, capability_result]
    workspace_id = uuid.uuid4()

    snapshot = await RetrievalService(session, workspace_id).retrieve("视觉 质检")

    experience_sql = compile_statement(session.scalars.await_args_list[0].args[0])
    capability_sql = compile_statement(session.scalars.await_args_list[1].args[0])
    for sql in (experience_sql, capability_sql):
        assert str(workspace_id) in sql
        assert "review_status = 'verified'" in sql
        assert "is_deleted IS false" in sql
        assert "ILIKE" in sql
    assert "LIMIT 3" in experience_sql
    assert "LIMIT 5" in capability_sql
    assert len(snapshot["experiences"]) == 1
    assert len(snapshot["capabilities"]) == 1
