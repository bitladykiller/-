"""EventInbox 单测：租户级幂等键 + 认领/完成/失败状态机。"""

from __future__ import annotations

import asyncio

from app.platform.event_inbox import (
    EVENT_STATUS_COMPLETED,
    EVENT_STATUS_PROCESSING,
    EventInbox,
    InboxClaimAction,
    stable_payload_hash,
)
from sqlalchemy.exc import IntegrityError


class FakeProcessedEvent:
    def __init__(
        self,
        *,
        tenant_id: str = "default",
        event_type: str = "",
        event_id: str = "",
        payload_hash: str = "",
        status: str = EVENT_STATUS_PROCESSING,
        attempts: int = 1,
        lease_owner: str = "",
        lease_expires_at=None,
        last_error: str = "",
        stream_name: str = "",
        stream_entry_id: str = "",
    ) -> None:
        self.tenant_id = tenant_id
        self.event_type = event_type
        self.event_id = event_id
        self.payload_hash = payload_hash
        self.status = status
        self.attempts = attempts
        self.lease_owner = lease_owner
        self.lease_expires_at = lease_expires_at
        self.last_error = last_error
        self.stream_name = stream_name
        self.stream_entry_id = stream_entry_id
        self.updated_at = None
        self.completed_at = None
        self.dead_lettered_at = None


class FakeResult:
    def __init__(self, row=None) -> None:
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class FakeSession:
    def __init__(self, existing: FakeProcessedEvent | None = None) -> None:
        self.existing = existing
        self.added: list = []
        self.committed = False
        self.rolled_back = False
        self.commit_error: Exception | None = None
        self.commit_count = 0
        self.query_calls: list[str] = []

    async def execute(self, stmt, params=None):
        self.query_calls.append(str(stmt))
        return FakeResult(self.existing)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commit_count += 1
        if self.commit_error is not None and self.commit_count == 1:
            self.committed = False
            raise self.commit_error
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None


class FakeSessionFactory:
    def __init__(self, *sessions: FakeSession) -> None:
        self._sessions = list(sessions)

    def __call__(self):
        if not self._sessions:
            raise AssertionError("会话工厂被多次调用，超出预制会话数量")
        return self._sessions.pop(0)


def _run(awaitable):
    return asyncio.run(awaitable)


def test_claim_same_event_in_different_tenants_both_process() -> None:
    """幂等键按租户隔离：两个租户的同 event_id 互不干扰。"""
    session_a = FakeSession(existing=None)
    session_b = FakeSession(existing=None)
    inbox = EventInbox(
        session_factory=FakeSessionFactory(session_a, session_b)
    )

    claim_a = _run(
        inbox.claim(
            event_type="turn_completed",
            event_id="turn-1",
            tenant_id="t_a",
            payload_hash="h",
            stream="agent:events",
            entry_id="1-0",
        )
    )
    claim_b = _run(
        inbox.claim(
            event_type="turn_completed",
            event_id="turn-1",
            tenant_id="t_b",
            payload_hash="h",
            stream="agent:events",
            entry_id="1-0",
        )
    )

    assert claim_a.action is InboxClaimAction.PROCESS
    assert claim_b.action is InboxClaimAction.PROCESS
    assert session_a.added[0].tenant_id == "t_a"
    assert session_b.added[0].tenant_id == "t_b"


def test_claim_conflict_marks_failed_with_tenant_scope() -> None:
    """同一租户同 event_id 不同 payload → PAYLOAD_CONFLICT，且按租户定位。"""
    existing = FakeProcessedEvent(
        tenant_id="t_a",
        event_type="turn_completed",
        event_id="turn-1",
        payload_hash="hash-old",
        status=EVENT_STATUS_PROCESSING,
    )
    session = FakeSession(existing=existing)
    session.commit_error = IntegrityError("stmt", {}, Exception("dup"))
    inbox = EventInbox(session_factory=FakeSessionFactory(session))

    claim = _run(
        inbox.claim(
            event_type="turn_completed",
            event_id="turn-1",
            tenant_id="t_a",
            payload_hash="hash-new",
            stream="agent:events",
            entry_id="1-0",
        )
    )

    assert claim.action is InboxClaimAction.PAYLOAD_CONFLICT
    assert existing.status == "failed"
    assert existing.last_error == "payload_hash_conflict"
    # 查询按租户 + 事件类型 + 事件 ID 定位
    assert "tenant_id" in session.query_calls[0]


def test_claim_skips_completed_in_same_tenant() -> None:
    existing = FakeProcessedEvent(
        tenant_id="t_a",
        event_type="turn_completed",
        event_id="turn-1",
        payload_hash="h",
        status=EVENT_STATUS_COMPLETED,
    )
    session = FakeSession(existing=existing)
    session.commit_error = IntegrityError("stmt", {}, Exception("dup"))
    inbox = EventInbox(session_factory=FakeSessionFactory(session))

    claim = _run(
        inbox.claim(
            event_type="turn_completed",
            event_id="turn-1",
            tenant_id="t_a",
            payload_hash="h",
            stream="agent:events",
            entry_id="1-0",
        )
    )

    assert claim.action is InboxClaimAction.SKIP_COMPLETED


def test_mark_completed_and_failed_respect_tenant() -> None:
    existing = FakeProcessedEvent(
        tenant_id="t_a",
        event_type="turn_completed",
        event_id="turn-1",
        status=EVENT_STATUS_PROCESSING,
        lease_owner="inbox-1",
    )
    session = FakeSession(existing=existing)
    inbox = EventInbox(session_factory=FakeSessionFactory(session))

    _run(
        inbox.mark_completed(
            event_type="turn_completed",
            event_id="turn-1",
            tenant_id="t_a",
            owner="inbox-1",
        )
    )

    assert existing.status == EVENT_STATUS_COMPLETED
    assert existing.completed_at is not None
    assert "tenant_id" in session.query_calls[0]

    session2 = FakeSession(existing=existing)
    inbox2 = EventInbox(session_factory=FakeSessionFactory(session2))
    _run(
        inbox2.mark_failed(
            event_type="turn_completed",
            event_id="turn-1",
            tenant_id="t_a",
            owner="inbox-1",
            error="boom",
        )
    )
    assert existing.status == "failed"
    assert existing.last_error == "boom"


def test_stable_payload_hash_is_tenant_sensitive() -> None:
    base = {"user_id": "7", "session_id": "1", "user_message": "问"}
    with_tenant = {**base, "tenant_id": "t_a"}
    assert stable_payload_hash(base) != stable_payload_hash(with_tenant)
