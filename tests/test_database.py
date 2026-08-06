from unittest.mock import AsyncMock, Mock

import pytest

from app.db import database


class FakeSessionContext:
    def __init__(self, session: AsyncMock) -> None:
        self.session = session

    async def __aenter__(self) -> AsyncMock:
        return self.session

    async def __aexit__(self, *_: object) -> None:
        return None


@pytest.mark.asyncio
async def test_get_db_yields_configured_session(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    session_factory = Mock(return_value=FakeSessionContext(session))
    monkeypatch.setattr(database, "_session_factory", session_factory)

    dependency = database.get_db()
    yielded = await anext(dependency)
    await dependency.aclose()

    assert yielded is session
    session_factory.assert_called_once_with()


@pytest.mark.asyncio
async def test_get_db_rolls_back_when_route_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    session = AsyncMock()
    session_factory = Mock(return_value=FakeSessionContext(session))
    monkeypatch.setattr(database, "_session_factory", session_factory)

    dependency = database.get_db()
    await anext(dependency)
    with pytest.raises(RuntimeError, match="route failed"):
        await dependency.athrow(RuntimeError("route failed"))

    session.rollback.assert_awaited_once_with()

