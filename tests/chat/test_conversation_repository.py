"""ConversationRepository 测试。

测试 Repository 层的 CRUD 操作，使用 FakeSession 模拟数据库。
"""

import asyncio
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app.chat.infrastructure.repository.conversation_repository import (
    ConversationRepository,
)
from app.chat.infrastructure.models.conversation import Conversation, DialogueType


class FakeConversation:
    """模拟 Conversation ORM 模型。"""

    def __init__(
        self,
        *,
        id: int = None,
        user_id: int = 0,
        title: str = "新会话",
        created_at: datetime = None,
        status: str = "ongoing",
        dialogue_type: DialogueType = DialogueType.NORMAL,
    ):
        self.id = id
        self.user_id = user_id
        self.title = title
        self.created_at = created_at or datetime.now()
        self.status = status
        self.dialogue_type = dialogue_type


class FakeResult:
    """模拟 SQLAlchemy Result。"""

    def __init__(self, scalars_data=None, scalar_one_or_none_data=None):
        self._scalars_data = scalars_data or []
        self._scalar_one_or_none_data = scalar_one_or_none_data

    def scalars(self):
        return self

    def all(self):
        return self._scalars_data

    def scalar_one_or_none(self):
        return self._scalar_one_or_none_data


class FakeSession:
    """模拟 AsyncSession，记录调用。"""

    def __init__(self):
        self.committed = False
        self.added = []
        self.deleted = []
        self.refreshed = []
        self._execute_results: list = []
        self._execute_results_iter = 0

    def set_execute_results(self, results: list):
        self._execute_results = results
        self._execute_results_iter = 0

    async def commit(self):
        self.committed = True

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def refresh(self, obj):
        obj.id = 101
        self.refreshed.append(obj)

    async def execute(self, stmt):
        if self._execute_results_iter < len(self._execute_results):
            result = self._execute_results[self._execute_results_iter]
            self._execute_results_iter += 1
            return result
        return FakeResult()


def _run(awaitable):
    return asyncio.run(awaitable)


def test_create_adds_and_commits_conversation() -> None:
    session = FakeSession()
    repo = ConversationRepository(session)

    result = _run(repo.create(user_id=5))

    assert result == 101
    assert session.committed is True
    assert len(session.added) == 1
    assert session.added[0].user_id == 5
    assert session.added[0].title == "新会话"
    assert session.added[0].dialogue_type == DialogueType.NORMAL
    assert len(session.refreshed) == 1


def test_list_by_user_returns_formatted_list() -> None:
    session = FakeSession()
    conv1 = FakeConversation(
        id=1,
        user_id=5,
        title="测试会话",
        status="ongoing",
        dialogue_type=DialogueType.NORMAL,
    )
    session.set_execute_results([
        FakeResult(scalars_data=[conv1]),
    ])
    repo = ConversationRepository(session)

    result = _run(repo.list_by_user(user_id=5))

    assert len(result) == 1
    assert result[0]["id"] == 1
    assert result[0]["title"] == "测试会话"
    assert result[0]["status"] == "ongoing"
    assert result[0]["dialogue_type"] == "普通对话"
    assert "created_at" in result[0]


def test_list_by_user_returns_empty_when_no_conversations() -> None:
    session = FakeSession()
    session.set_execute_results([
        FakeResult(scalars_data=[]),
    ])
    repo = ConversationRepository(session)

    result = _run(repo.list_by_user(user_id=99))

    assert result == []


def test_delete_removes_and_commits() -> None:
    session = FakeSession()
    conv = FakeConversation(id=10, user_id=5, title="测试")
    session.set_execute_results([
        FakeResult(scalar_one_or_none_data=conv),
    ])
    repo = ConversationRepository(session)

    _run(repo.delete(conversation_id=10))

    assert session.committed is True
    assert len(session.deleted) == 1


def test_delete_raises_value_error_when_not_found() -> None:
    session = FakeSession()
    session.set_execute_results([
        FakeResult(scalar_one_or_none_data=None),
    ])
    repo = ConversationRepository(session)

    with pytest.raises(ValueError, match="Conversation 999 not found"):
        _run(repo.delete(conversation_id=999))


def test_rename_updates_title_and_commits() -> None:
    session = FakeSession()
    conv = FakeConversation(id=5, user_id=3, title="旧标题")
    session.set_execute_results([
        FakeResult(scalar_one_or_none_data=conv),
    ])
    repo = ConversationRepository(session)

    _run(repo.rename(conversation_id=5, name="新标题"))

    assert conv.title == "新标题"
    assert session.committed is True


def test_rename_raises_value_error_when_not_found() -> None:
    session = FakeSession()
    session.set_execute_results([
        FakeResult(scalar_one_or_none_data=None),
    ])
    repo = ConversationRepository(session)

    with pytest.raises(ValueError, match="Conversation 888 not found"):
        _run(repo.rename(conversation_id=888, name="新标题"))