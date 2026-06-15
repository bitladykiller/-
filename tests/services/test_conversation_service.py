import asyncio
from datetime import datetime

import pytest

import app.chat.application.conversation_service as conversation_service
from app.user.infrastructure.models.conversation import Conversation, DialogueType


def _run(awaitable):
    return asyncio.run(awaitable)


def _build_conversation(
    *,
    conversation_id: int,
    user_id: int,
    title: str,
    created_at: datetime,
    dialogue_type: DialogueType = DialogueType.NORMAL,
    status: str = "ongoing",
) -> Conversation:
    conversation = Conversation(
        user_id=user_id,
        title=title,
        dialogue_type=dialogue_type,
    )
    conversation.id = conversation_id
    conversation.created_at = created_at
    conversation.status = status
    return conversation


class FakeConversationResult:
    def __init__(self, conversation: Conversation | None) -> None:
        self._conversation = conversation

    def scalar_one_or_none(self) -> Conversation | None:
        return self._conversation


class FakeConversationListResult:
    def __init__(self, conversations: list[Conversation]) -> None:
        self._conversations = conversations

    def scalars(self):
        class _ScalarResult:
            def __init__(self, conversations: list[Conversation]) -> None:
                self._conversations = conversations

            def all(self) -> list[Conversation]:
                return self._conversations

        return _ScalarResult(self._conversations)


class FakeLogger:
    def __init__(self) -> None:
        self.errors: list[tuple[str, bool]] = []

    def error(self, msg: str, *args, **kwargs) -> None:
        self.errors.append((msg, kwargs.get("exc_info", False)))


class FakeSession:
    def __init__(self, execute_result: object | None = None) -> None:
        self.execute_result = execute_result
        self.executed_stmt = None
        self.added: list[Conversation] = []
        self.deleted: list[Conversation] = []
        self.committed = False
        self.refreshed: list[Conversation] = []

    def add(self, conversation: Conversation) -> None:
        self.added.append(conversation)

    async def delete(self, conversation: Conversation) -> None:
        self.deleted.append(conversation)

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, conversation: Conversation) -> None:
        conversation.id = 101
        self.refreshed.append(conversation)

    async def execute(self, stmt):
        self.executed_stmt = stmt
        return self.execute_result


class FakeSessionFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def __call__(self):
        session = self.session

        class _SessionContext:
            async def __aenter__(self):
                return session

            async def __aexit__(self, _exc_type, exc, _tb) -> bool:
                return False

        return _SessionContext()


def test_create_conversation_builds_default_conversation_and_returns_id(monkeypatch) -> None:
    session = FakeSession()
    monkeypatch.setattr(conversation_service, "AsyncSessionLocal", FakeSessionFactory(session))

    conversation_id = _run(conversation_service.create_conversation(12))

    assert conversation_id == 101
    assert session.committed is True
    assert len(session.added) == 1
    assert session.added[0].user_id == 12
    assert session.added[0].title == "新会话"
    assert session.added[0].dialogue_type == DialogueType.NORMAL


def test_create_conversation_logs_and_reraises_when_db_write_fails(monkeypatch) -> None:
    class BrokenSession(FakeSession):
        async def commit(self) -> None:
            raise RuntimeError("boom")

    fake_logger = FakeLogger()
    monkeypatch.setattr(conversation_service, "logger", fake_logger)
    monkeypatch.setattr(
        conversation_service,
        "AsyncSessionLocal",
        FakeSessionFactory(BrokenSession()),
    )

    with pytest.raises(RuntimeError, match="boom"):
        _run(conversation_service.create_conversation(7))

    assert fake_logger.errors == [("create_conversation 异常 | user_id=7 | boom", True)]


def test_get_user_conversations_filters_default_title_and_serializes(monkeypatch) -> None:
    created_at = datetime(2024, 1, 2, 3, 4, 5)
    conversation = _build_conversation(
        conversation_id=7,
        user_id=3,
        title="购买建议",
        created_at=created_at,
        dialogue_type=DialogueType.RAG,
        status="done",
    )
    session = FakeSession(FakeConversationListResult([conversation]))
    monkeypatch.setattr(conversation_service, "AsyncSessionLocal", FakeSessionFactory(session))

    result = _run(conversation_service.get_user_conversations(99))

    assert result == [
        {
            "id": 7,
            "title": "购买建议",
            "created_at": "2024-01-02T03:04:05",
            "status": "done",
            "dialogue_type": "RAG 问答",
        }
    ]
    compiled = str(session.executed_stmt)
    assert "WHERE conversations.user_id = :user_id_1" in compiled
    assert "conversations.title != :title_1" in compiled
    assert "ORDER BY conversations.created_at DESC" in compiled


def test_delete_conversation_deletes_loaded_conversation(monkeypatch) -> None:
    conversation = _build_conversation(
        conversation_id=9,
        user_id=3,
        title="旧标题",
        created_at=datetime(2024, 1, 2, 3, 4, 5),
    )
    session = FakeSession(FakeConversationResult(conversation))
    monkeypatch.setattr(conversation_service, "AsyncSessionLocal", FakeSessionFactory(session))

    _run(conversation_service.delete_conversation(9))

    assert session.deleted == [conversation]
    assert session.committed is True


def test_delete_conversation_raises_for_missing_record(monkeypatch) -> None:
    session = FakeSession(FakeConversationResult(None))
    monkeypatch.setattr(conversation_service, "AsyncSessionLocal", FakeSessionFactory(session))

    with pytest.raises(ValueError, match="Conversation 77 not found"):
        _run(conversation_service.delete_conversation(77))


def test_update_conversation_name_updates_title(monkeypatch) -> None:
    conversation = _build_conversation(
        conversation_id=9,
        user_id=3,
        title="旧标题",
        created_at=datetime(2024, 1, 2, 3, 4, 5),
    )
    session = FakeSession(FakeConversationResult(conversation))
    monkeypatch.setattr(conversation_service, "AsyncSessionLocal", FakeSessionFactory(session))

    _run(conversation_service.update_conversation_name(9, "新标题"))

    assert conversation.title == "新标题"
    assert session.committed is True


def test_update_conversation_name_raises_for_missing_record(monkeypatch) -> None:
    session = FakeSession(FakeConversationResult(None))
    monkeypatch.setattr(conversation_service, "AsyncSessionLocal", FakeSessionFactory(session))

    with pytest.raises(ValueError, match="Conversation 77 not found"):
        _run(conversation_service.update_conversation_name(77, "新标题"))
