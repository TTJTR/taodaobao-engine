import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.intelligence_service import IntelligenceService


class _TemplateSession:
    def __init__(self) -> None:
        self.added = []
        self.committed = 0
        self.statement = None

    def add(self, item) -> None:
        self.added.append(item)

    async def commit(self) -> None:
        self.committed += 1

    async def refresh(self, _item) -> None:
        return None

    async def scalars(self, statement):
        self.statement = statement
        return []

    async def scalar(self, statement):
        self.statement = statement
        return 0


@pytest.mark.asyncio
async def test_search_template_is_normalized_and_workspace_owned() -> None:
    session = _TemplateSession()
    workspace_id = uuid.uuid4()
    service = IntelligenceService(session, workspace_id, uuid.uuid4())

    template = await service.create_search_template(
        name="  customer signals  ",
        purpose="customer_profile",
        query_template="  {company} hiring  ",
        keywords=[" AI ", "AI", ""],
        allowed_fields=[" hiring ", "hiring"],
    )

    assert template.workspace_id == workspace_id
    assert template.name == "customer signals"
    assert template.query_template == "{company} hiring"
    assert template.keywords == ["AI"]
    assert template.allowed_fields == ["hiring"]
    assert session.committed == 1


@pytest.mark.asyncio
async def test_list_search_templates_applies_workspace_soft_delete_and_purpose_filters() -> None:
    session = _TemplateSession()
    service = IntelligenceService(session, uuid.uuid4(), uuid.uuid4())

    rows, total = await service.list_search_templates(1, 20, "solution")

    assert rows == []
    assert total == 0
    compiled = str(session.statement)
    assert "intelligence_search_templates.workspace_id" in compiled
    assert "intelligence_search_templates.is_deleted IS false" in compiled
    assert "intelligence_search_templates.purpose" in compiled


@pytest.mark.asyncio
async def test_delete_search_template_is_soft_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _TemplateSession()
    service = IntelligenceService(session, uuid.uuid4(), uuid.uuid4())
    template = SimpleNamespace(is_deleted=False)
    monkeypatch.setattr(service, "_get", AsyncMock(return_value=template))

    await service.delete_search_template(uuid.uuid4())

    assert template.is_deleted is True
    assert session.committed == 1
