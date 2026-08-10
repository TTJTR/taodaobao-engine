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
    source = SimpleNamespace(
        content_version=1,
        permission_checked_at=datetime.now(UTC),
        freshness_status=SimpleNamespace(value="current"),
        title="旁路试点来源",
        source_url="https://example.test/source",
        author="测试作者",
        source_updated_at=datetime.now(UTC),
        synced_at=datetime.now(UTC),
        content_fingerprint="f" * 64,
    )
    asset = SimpleNamespace(
        id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        data={"name": "旁路试点"},
        review_status=SimpleNamespace(value="verified"),
        updated_at=datetime.now(UTC),
        source_version_at_review=1,
        embedding_version="test-v1",
        source=source,
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
    assert snapshot["experiences"][0]["source_version"] == 1
    assert snapshot["experiences"][0]["permission_status"] == "granted"
    source_snapshot = snapshot["experiences"][0]["source_snapshot"]
    assert source_snapshot["source_version"] == "1"
    assert source_snapshot["reviewed_version"] == "1"
    assert source_snapshot["permission_valid"] is True
    assert source_snapshot["available"] is True
    assert source_snapshot["title"] == "旁路试点来源"
    assert source_snapshot["author"] == "测试作者"
    assert snapshot["experiences"][0]["evidence_location"]["kind"] == "reviewed_asset_json"
