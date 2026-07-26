"""ConversationService 测试。

验证 Service 层通过 Repository 层正确访问数据库。
"""

import asyncio

from app.chat.application.conversation_service import (
    ConversationService,
)


def _run(awaitable):
    return asyncio.run(awaitable)


class FakeSession:
    def __init__(self, conversations=None):
        self.conversations = conversations or []
        self.committed = False
        self.added = []
        self.deleted = []

    async def commit(self):
        self.committed = True

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def refresh(self, obj):
        obj.id = 101

    async def execute(self, stmt):
        return FakeResult(self.conversations)


class FakeResult:
    def __init__(self, conversations):
        self._conversations = conversations

    def scalars(self):
        return self

    def all(self):
        return self._conversations

    def scalar_one_or_none(self):
        return self._conversations[0] if self._conversations else None


class FakeSessionFactory:
    def __init__(self, session):
        self.session = session

    def __call__(self):
        class _Context:
            async def __aenter__(self):
                return self.session

            async def __aexit__(self, *args):
                return False

        return _Context()


def test_create_conversation_returns_id(monkeypatch) -> None:
    async def fake_run(*args, **kwargs):
        return 101

    monkeypatch.setattr(
        "app.chat.application.conversation_service.run_db_operation", fake_run
    )

    service = ConversationService()
    result = _run(service.create_conversation(5))
    assert result == 101


def test_get_user_conversations_returns_list(monkeypatch) -> None:
    expected = [
        {"id": 1, "title": "test", "created_at": "2024-01-01", "status": "active", "dialogue_type": "normal"}
    ]

    async def fake_run(*args, **kwargs):
        return expected

    monkeypatch.setattr(
        "app.chat.application.conversation_service.run_db_operation", fake_run
    )

    service = ConversationService()
    result = _run(service.get_user_conversations(1))
    assert result == expected


def test_delete_conversation_succeeds(monkeypatch) -> None:
    class _DeletedConversation:
        user_id = 7

    async def fake_run(*args, **kwargs):
        return _DeletedConversation()

    cleared: list[tuple[str, str]] = []

    async def fake_clear(*, user_id: str, session_id: str) -> None:
        cleared.append((user_id, session_id))

    monkeypatch.setattr(
        "app.chat.application.conversation_service.run_db_operation", fake_run
    )
    monkeypatch.setattr(
        "app.chat.application.conversation_service._clear_conversation_memories",
        fake_clear,
    )

    service = ConversationService()
    result = _run(service.delete_conversation(1, user_id=7))
    assert result is None
    assert cleared == [("7", "1")]


def test_update_conversation_name_succeeds(monkeypatch) -> None:
    async def fake_run(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.chat.application.conversation_service.run_db_operation", fake_run
    )

    service = ConversationService()
    result = _run(service.update_conversation_name(1, 7, "new name"))
    assert result is None


def test_ensure_conversation_creates_when_missing(monkeypatch) -> None:
    created: list[int] = []

    async def fake_create(user_id: int) -> int:
        created.append(user_id)
        return 42

    service = ConversationService()
    monkeypatch.setattr(service, "create_conversation", fake_create)

    assert _run(service.ensure_conversation(7, None)) == 42
    assert created == [7]


def test_ensure_conversation_validates_ownership(monkeypatch) -> None:
    """给定 conversation_id 时必须校验归属，不符 → ResourceNotFoundError。"""
    from app.shared.core.errors import ResourceNotFoundError

    checked: list[tuple] = []

    async def fake_run(factory, log, action, op, *args, **ctx):
        checked.append(args)
        raise ResourceNotFoundError("会话不存在或不属于当前用户")

    monkeypatch.setattr(
        "app.chat.application.conversation_service.run_db_operation", fake_run
    )

    service = ConversationService()
    import pytest as _pytest

    with _pytest.raises(ResourceNotFoundError):
        _run(service.ensure_conversation(7, 999))
    assert checked == [(999, 7)]


def test_ensure_conversation_returns_given_id_when_owned(monkeypatch) -> None:
    async def fake_run(factory, log, action, op, *args, **ctx):
        return object()  # 归属校验通过

    monkeypatch.setattr(
        "app.chat.application.conversation_service.run_db_operation", fake_run
    )

    service = ConversationService()
    assert _run(service.ensure_conversation(7, 11)) == 11
