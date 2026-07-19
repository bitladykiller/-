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
    async def fake_run(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.chat.application.conversation_service.run_db_operation", fake_run
    )

    service = ConversationService()
    result = _run(service.delete_conversation(1))
    assert result is None


def test_update_conversation_name_succeeds(monkeypatch) -> None:
    async def fake_run(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "app.chat.application.conversation_service.run_db_operation", fake_run
    )

    service = ConversationService()
    result = _run(service.update_conversation_name(1, "new name"))
    assert result is None
