"""MessageRepository 单测：租户 JOIN 强制条件 + 幂等追加。"""

from __future__ import annotations

import asyncio

from app.chat.infrastructure.repository.message_repository import MessageRepository
from sqlalchemy.exc import IntegrityError


class FakeMessage:
    def __init__(self, *, conversation_id: int, sender: str, content: str, turn_event_id=None):
        self.conversation_id = conversation_id
        self.sender = sender
        self.content = content
        self.turn_event_id = turn_event_id
        self.created_at = None


class FakeResult:
    def __init__(self, rows=None) -> None:
        self._rows = rows or []

    def scalars(self):
        return self

    def all(self):
        return self._rows


class FakeSession:
    def __init__(self, result_rows=None) -> None:
        self.committed = False
        self.added_all: list = []
        self.rolled_back = False
        self.execute_calls: list[str] = []
        self._rows = result_rows or []

    async def execute(self, stmt, params=None):
        self.execute_calls.append(str(stmt))
        return FakeResult(self._rows)

    def add_all(self, objects) -> None:
        self.added_all.extend(objects)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


def _run(awaitable):
    return asyncio.run(awaitable)


def test_add_turn_writes_user_and_assistant_messages() -> None:
    session = FakeSession()
    repo = MessageRepository(session)

    _run(repo.add_turn("t_1", 11, "问", "答"))

    assert session.committed is True
    assert [m.sender for m in session.added_all] == ["user", "assistant"]
    assert all(m.conversation_id == 11 for m in session.added_all)


def test_add_turn_skips_when_turn_already_persisted() -> None:
    session = FakeSession(result_rows=["user", "assistant"])
    repo = MessageRepository(session)

    _run(repo.add_turn("t_1", 11, "问", "答", turn_event_id="turn-1"))

    assert session.added_all == []
    assert session.committed is False
    assert "conversations.tenant_id" in session.execute_calls[0]


def test_add_turn_swallows_integrity_error_on_race() -> None:
    class RacingSession(FakeSession):
        def __init__(self) -> None:
            super().__init__()
            self._turns_seen = False

        async def execute(self, stmt, params=None):
            # 首次查询：回合不存在 → 走插入；commit 竞态失败后的复查：已存在
            rows = [] if not self._turns_seen else ["user", "assistant"]
            self._turns_seen = True
            return FakeResult(rows)

        async def commit(self) -> None:
            self.committed = True
            raise IntegrityError("stmt", {}, Exception("dup"))

        async def rollback(self) -> None:
            self.rolled_back = True

    session = RacingSession()
    repo = MessageRepository(session)

    _run(repo.add_turn("t_1", 11, "问", "答", turn_event_id="turn-2"))

    assert session.rolled_back is True
    assert session.committed is True


def test_list_by_conversation_joins_tenant_condition() -> None:
    rows = [
        FakeMessage(conversation_id=11, sender="user", content="问"),
        FakeMessage(conversation_id=11, sender="assistant", content="答"),
    ]
    session = FakeSession(result_rows=rows)
    repo = MessageRepository(session)

    result = _run(repo.list_by_conversation("t_1", 11))

    assert len(result) == 2
    assert result[0]["role"] == "user"
    # 强制 JOIN conversations 过滤租户（即使调用方漏做归属校验也不泄漏）
    assert "conversations.tenant_id" in session.execute_calls[0]
    assert "messages.conversation_id" in session.execute_calls[0]


def test_list_by_conversation_returns_empty() -> None:
    session = FakeSession(result_rows=[])
    repo = MessageRepository(session)

    assert _run(repo.list_by_conversation("t_9", 999)) == []
