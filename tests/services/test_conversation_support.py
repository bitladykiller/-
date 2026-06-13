import asyncio
from datetime import datetime

import pytest

from app.models.conversation import Conversation, DialogueType
from app.services.conversation_support import (
    DEFAULT_CONVERSATION_TITLE,
    build_default_conversation,
    build_user_conversations_stmt,
    create_conversation_record,
    get_conversation_or_raise,
    rename_conversation_record,
    run_db_operation,
    serialize_conversation,
    serialize_conversations,
)


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


class FakeLogger:
    def __init__(self) -> None:
        self.errors: list[tuple[str, bool]] = []

    def error(self, msg: str, *args, **kwargs) -> None:
        self.errors.append((msg, kwargs.get("exc_info", False)))


class FakeSession:
    def __init__(self, conversation: Conversation | None = None) -> None:
        self.conversation = conversation
        self.added: list[Conversation] = []
        self.committed = False
        self.refreshed: list[Conversation] = []

    def add(self, conversation: Conversation) -> None:
        self.added.append(conversation)

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, conversation: Conversation) -> None:
        conversation.id = 101
        self.refreshed.append(conversation)

    async def execute(self, stmt):
        return FakeConversationResult(self.conversation)


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


def _run(awaitable):
    return asyncio.run(awaitable)


def test_serialize_conversation_and_list_return_api_friendly_shapes() -> None:
    created_at = datetime(2024, 1, 2, 3, 4, 5)
    conversation = _build_conversation(
        conversation_id=7,
        user_id=3,
        title="购买建议",
        created_at=created_at,
        dialogue_type=DialogueType.RAG,
        status="done",
    )

    serialized = serialize_conversation(conversation)

    assert serialized == {
        "id": 7,
        "title": "购买建议",
        "created_at": "2024-01-02T03:04:05",
        "status": "done",
        "dialogue_type": "RAG 问答",
    }
    assert serialize_conversations([conversation]) == [serialized]


def test_build_default_conversation_uses_default_title_and_normal_dialogue() -> None:
    conversation = build_default_conversation(12)

    assert conversation.user_id == 12
    assert conversation.title == DEFAULT_CONVERSATION_TITLE
    assert conversation.dialogue_type == DialogueType.NORMAL


def test_build_user_conversations_stmt_filters_default_title_and_orders_desc() -> None:
    stmt = build_user_conversations_stmt(99)
    compiled = str(stmt)

    assert "WHERE conversations.user_id = :user_id_1" in compiled
    assert "conversations.title != :title_1" in compiled
    assert "ORDER BY conversations.created_at DESC" in compiled


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
    assert session.added[0].title == DEFAULT_CONVERSATION_TITLE
    assert session.added[0].dialogue_type == DialogueType.NORMAL


def test_get_conversation_or_raise_and_rename_conversation_record_share_lookup() -> None:
    conversation = _build_conversation(
        conversation_id=9,
        user_id=3,
        title="旧标题",
        created_at=datetime(2024, 1, 2, 3, 4, 5),
    )
    session = FakeSession(conversation)

    loaded = _run(get_conversation_or_raise(session, 9))
    _run(rename_conversation_record(session, 9, "新标题"))

    assert loaded is conversation
    assert conversation.title == "新标题"
    assert session.committed is True


def test_get_conversation_or_raise_raises_for_missing_record() -> None:
    session = FakeSession()

    with pytest.raises(ValueError, match="Conversation 77 not found"):
        _run(get_conversation_or_raise(session, 77))
