import uuid
from types import SimpleNamespace

import pytest

from app.integrations.protocols import FeishuCollaborator
from app.services import job_runner


@pytest.mark.asyncio
async def test_restricted_collaborator_name_reuses_known_workspace_user(monkeypatch) -> None:
    class FakeUsers:
        def __init__(self, session, workspace_id) -> None:
            del session, workspace_id

        async def get_by_feishu_user_id(self, feishu_user_id: str):
            if feishu_user_id == "ou_known":
                return SimpleNamespace(name="王筱涵")
            return None

    monkeypatch.setattr(job_runner, "UserRepository", FakeUsers)

    result = await job_runner._resolve_collaborator_names(
        object(),
        uuid.uuid4(),
        (
            FeishuCollaborator(
                feishu_user_id="ou_known",
                name="文档协作者1（姓名受限）",
                permission="full_access",
                is_owner=True,
            ),
            FeishuCollaborator(
                feishu_user_id="ou_unknown",
                name="文档协作者2（姓名受限）",
                permission="edit",
            ),
        ),
    )

    assert result[0].name == "王筱涵"
    assert result[0].is_owner is True
    assert result[1].name == "文档协作者2（姓名受限）"
