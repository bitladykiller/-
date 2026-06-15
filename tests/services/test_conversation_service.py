import asyncio
from datetime import datetime

import pytest

import app.chat.application.conversation_service as conversation_service
from app.chat.application.conversation_service import (
    create_conversation_record,
    delete_conversation_record,
    fetch_user_conversations,
    rename_conversation_record,
    run_db_operation,
)
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
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, exc_type, exc, tb) -> bool:
                return False

        return _SessionContext()


def test_create_conversation_delegates_to_run_db_operation(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run_db_operation(
        session_factory,
        logger,
        action_name,
        operation,
        *operation_args,
        **context,
    ):
        captured["session_factory"] = session_factory
        captured["logger"] = logger
        captured["action_name"] = action_name
        captured["operation"] = operation
        captured["operation_args"] = operation_args
        captured["context"] = context
        return 123

    monkeypatch.setattr(conversation_service, "run_db_operation", fake_run_db_operation)

    result = _run(conversation_service.create_conversation(5))

    assert result == 123
    assert captured == {
        "session_factory": conversation_service.AsyncSessionLocal,
        "logger": conversation_service.logger,
        "action_name": "create_conversation",
        "operation": conversation_service.create_conversation_record,
        "operation_args": (5,),
        "context": {"user_id": 5},
    }


def test_get_user_conversations_delegates_to_fetch_operation(monkeypatch) -> None:
    async def fake_run_db_operation(
        session_factory,
        logger,
        action_name,
        operation,
        *operation_args,
        **context,
    ):
        assert session_factory is conversation_service.AsyncSessionLocal
        assert logger is conversation_service.logger
        assert action_name == "get_user_conversations"
        assert operation is conversation_service.fetch_user_conversations
        assert operation_args == (8,)
        assert context == {"user_id": 8}
        return [{"id": 1}]

    monkeypatch.setattr(conversation_service, "run_db_operation", fake_run_db_operation)

    result = _run(conversation_service.get_user_conversations(8))

    assert result == [{"id": 1}]


def test_delete_and_rename_delegate_to_expected_record_operations(monkeypatch) -> None:
    calls: list[tuple[str, object, tuple[object, ...], dict[str, object]]] = []

    async def fake_run_db_operation(
        session_factory,
        logger,
        action_name,
        operation,
        *operation_args,
        **context,
    ):
        assert session_factory is conversation_service.AsyncSessionLocal
        assert logger is conversation_service.logger
        calls.append((action_name, operation, operation_args, context))
        return None

    monkeypatch.setattr(conversation_service, "run_db_operation", fake_run_db_operation)

    _run(conversation_service.delete_conversation(9))
    _run(conversation_service.update_conversation_name(9, "售后跟进"))

    assert calls == [
        (
            "delete_conversation",
            conversation_service.delete_conversation_record,
            (9,),
            {"conversation_id": 9},
        ),
        (
            "update_conversation_name",
            conversation_service.rename_conversation_record,
            (9, "售后跟进"),
            {"conversation_id": 9, "name": "售后跟进"},
        ),
    ]


def test_run_db_operation_logs_and_reraises_when_operation_fails() -> None:
    session = FakeSession()
    session_factory = FakeSessionFactory(session)
    logger = FakeLogger()

    async def failing_operation(db, user_id):
        assert db is session
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _run(
            run_db_operation(
                session_factory,
                logger,
                "load_conversation",
                failing_operation,
                7,
                user_id=7,
            )
        )

    assert logger.errors == [("load_conversation 异常 | user_id=7 | boom", True)]


def test_create_conversation_record_builds_default_conversation_and_returns_id() -> None:
    session = FakeSession()

    conversation_id = _run(create_conversation_record(session, 12))

    assert conversation_id == 101
    assert session.committed is True
    assert len(session.added) == 1
    assert session.added[0].user_id == 12
    assert session.added[0].title == "新会话"
    assert session.added[0].dialogue_type == DialogueType.NORMAL


def test_fetch_user_conversations_filters_default_title_and_serializes() -> None:
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

    result = _run(fetch_user_conversations(session, 99))

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


def test_delete_conversation_record_deletes_loaded_conversation() -> None:
    conversation = _build_conversation(
        conversation_id=9,
        user_id=3,
        title="旧标题",
        created_at=datetime(2024, 1, 2, 3, 4, 5),
    )
    session = FakeSession(FakeConversationResult(conversation))

    _run(delete_conversation_record(session, 9))

    assert session.deleted == [conversation]
    assert session.committed is True


def test_delete_conversation_record_raises_for_missing_record() -> None:
    session = FakeSession(FakeConversationResult(None))

    with pytest.raises(ValueError, match="Conversation 77 not found"):
        _run(delete_conversation_record(session, 77))


def test_rename_conversation_record_updates_title() -> None:
    conversation = _build_conversation(
        conversation_id=9,
        user_id=3,
        title="旧标题",
        created_at=datetime(2024, 1, 2, 3, 4, 5),
    )
    session = FakeSession(FakeConversationResult(conversation))

    _run(rename_conversation_record(session, 9, "新标题"))

    assert conversation.title == "新标题"
    assert session.committed is True


def test_rename_conversation_record_raises_for_missing_record() -> None:
    session = FakeSession(FakeConversationResult(None))

    with pytest.raises(ValueError, match="Conversation 77 not found"):
        _run(rename_conversation_record(session, 77, "新标题"))
