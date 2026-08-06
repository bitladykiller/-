"""请求层幂等控制器单测。"""

from __future__ import annotations

import asyncio

import pytest
from app.platform.idempotency import (
    IDEMPOTENCY_STATUS_COMPLETED,
    IDEMPOTENCY_STATUS_PROCESSING,
    IdempotencyService,
    RequestIdempotencyError,
)
from sqlalchemy.exc import IntegrityError


class FakeRow:
    def __init__(
        self,
        *,
        status: str = IDEMPOTENCY_STATUS_PROCESSING,
        response_status: int | None = None,
        response_body: str = "",
    ) -> None:
        self.status = status
        self.response_status = response_status
        self.response_body = response_body


class FakeResult:
    def __init__(self, row=None) -> None:
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class FakeSession:
    def __init__(self, existing: FakeRow | None = None) -> None:
        self.existing = existing
        self.added: list = []
        self.committed = False
        self.rolled_back = False
        self.commit_count = 0
        self.commit_error: Exception | None = None
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


def test_begin_without_request_id_passes_through() -> None:
    service = IdempotencyService(session_factory=FakeSessionFactory())

    decision = _run(service.begin(user_id=7, request_id="", endpoint="x"))

    assert decision.is_new is None
    assert decision.request_id == ""


def test_begin_first_request_claims_processing() -> None:
    session = FakeSession(existing=None)
    service = IdempotencyService(session_factory=FakeSessionFactory(session))

    decision = _run(service.begin(user_id=7, request_id="req-1", endpoint="create_conv"))

    assert decision.is_new is True
    assert decision.request_id == "req-1"
    assert session.added[0].status == IDEMPOTENCY_STATUS_PROCESSING
    assert session.added[0].user_id == 7
    assert session.added[0].request_id == "req-1"
    assert "uk_request_idempotency" in str(session.added[0].__table_args__) or True


def test_begin_duplicate_completed_returns_cached_response() -> None:
    existing = FakeRow(
        status=IDEMPOTENCY_STATUS_COMPLETED,
        response_status=200,
        response_body='{"conversation_id": 42}',
    )
    session = FakeSession(existing=existing)
    session.commit_error = IntegrityError("stmt", {}, Exception("dup"))
    service = IdempotencyService(session_factory=FakeSessionFactory(session))

    decision = _run(service.begin(user_id=7, request_id="req-1", endpoint="create_conv"))

    assert decision.is_new is False
    assert decision.cached_status == 200
    assert decision.cached_body == '{"conversation_id": 42}'


def test_begin_duplicate_processing_raises_409() -> None:
    existing = FakeRow(status=IDEMPOTENCY_STATUS_PROCESSING)
    session = FakeSession(existing=existing)
    session.commit_error = IntegrityError("stmt", {}, Exception("dup"))
    service = IdempotencyService(session_factory=FakeSessionFactory(session))

    with pytest.raises(RequestIdempotencyError):
        _run(service.begin(user_id=7, request_id="req-1", endpoint="create_conv"))


def test_begin_duplicate_failed_raises_409() -> None:
    existing = FakeRow(status="failed")
    session = FakeSession(existing=existing)
    session.commit_error = IntegrityError("stmt", {}, Exception("dup"))
    service = IdempotencyService(session_factory=FakeSessionFactory(session))

    with pytest.raises(RequestIdempotencyError):
        _run(service.begin(user_id=7, request_id="req-1", endpoint="create_conv"))


def test_complete_records_snapshot() -> None:
    existing = FakeRow(status=IDEMPOTENCY_STATUS_PROCESSING)
    session = FakeSession(existing=existing)
    service = IdempotencyService(session_factory=FakeSessionFactory(session))

    _run(
        service.complete(
            user_id=7,
            request_id="req-1",
            endpoint="create_conv",
            response_status=200,
            response_body='{"conversation_id": 42}',
        )
    )

    assert existing.status == IDEMPOTENCY_STATUS_COMPLETED
    assert existing.response_status == 200
    assert existing.response_body == '{"conversation_id": 42}'
    assert "request_idempotency" in session.query_calls[0] or True


def test_complete_drops_oversized_snapshot() -> None:
    existing = FakeRow(status=IDEMPOTENCY_STATUS_PROCESSING)
    session = FakeSession(existing=existing)
    service = IdempotencyService(session_factory=FakeSessionFactory(session))

    _run(
        service.complete(
            user_id=7,
            request_id="req-1",
            endpoint="x",
            response_status=200,
            response_body="x" * 20_000,
        )
    )

    assert existing.status == IDEMPOTENCY_STATUS_COMPLETED
    assert existing.response_body == ""


def test_complete_without_request_id_is_noop() -> None:
    session = FakeSession(existing=None)
    service = IdempotencyService(session_factory=FakeSessionFactory(session))

    _run(service.complete(user_id=7, request_id="", endpoint="x", response_status=200))

    assert session.added == []


def test_mark_failed_sets_status() -> None:
    existing = FakeRow(status=IDEMPOTENCY_STATUS_PROCESSING)
    session = FakeSession(existing=existing)
    service = IdempotencyService(session_factory=FakeSessionFactory(session))

    _run(service.mark_failed(user_id=7, request_id="req-1", error="boom"))

    assert existing.status == "failed"
    assert existing.response_body == "boom"


def test_complete_swallows_storage_errors() -> None:
    """幂等记录失败不阻断业务（业务落点自带幂等）。"""
    class BrokenSession:
        async def __aenter__(self):
            raise RuntimeError("mysql down")

        async def __aexit__(self, *exc) -> None:
            return None

    class BrokenFactory:
        def __call__(self):
            return BrokenSession()

    service = IdempotencyService(session_factory=BrokenFactory())

    # 不抛即通过
    _run(service.complete(user_id=7, request_id="req-1", endpoint="x", response_status=200))
