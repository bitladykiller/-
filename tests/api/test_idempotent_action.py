"""run_idempotent_action 包装器测试。"""

from __future__ import annotations

import asyncio
import json

import pytest
from app.api.common import run_idempotent_action
from app.shared.core.errors import ResourceNotFoundError
from fastapi import HTTPException


class FakeDecision:
    def __init__(self, is_new, *, cached_status=0, cached_body=""):
        self.is_new = is_new
        self.cached_status = cached_status
        self.cached_body = cached_body


class FakeIdempotencyService:
    def __init__(self, decisions=None) -> None:
        self._decisions = list(decisions or [])
        self.begin_calls: list[tuple] = []
        self.complete_calls: list[tuple] = []
        self.failed_calls: list[tuple] = []
        self.begin_error: Exception | None = None

    async def begin(self, *, user_id, request_id, endpoint):
        self.begin_calls.append((user_id, request_id, endpoint))
        if self.begin_error is not None:
            raise self.begin_error
        if not self._decisions:
            return FakeDecision(None)
        return self._decisions.pop(0)

    async def complete(self, **kwargs):
        self.complete_calls.append(kwargs)

    async def mark_failed(self, **kwargs):
        self.failed_calls.append(kwargs)


class _Logger:
    def error(self, *args, **kwargs):
        pass


def _run(awaitable):
    return asyncio.run(awaitable)


def _patch_service(monkeypatch, service: FakeIdempotencyService) -> None:
    # common.py 在函数体内运行时导入单例，patch 单例对象的方法即可生效
    from app.platform.idempotency import idempotency_service as real_service

    monkeypatch.setattr(real_service, "begin", service.begin)
    monkeypatch.setattr(real_service, "complete", service.complete)
    monkeypatch.setattr(real_service, "mark_failed", service.mark_failed)


async def test_no_request_id_passes_through(monkeypatch) -> None:
    service = FakeIdempotencyService([FakeDecision(None)])
    _patch_service(monkeypatch, service)

    result = await run_idempotent_action(
        "test",
        _ok("hello"),
        logger=_Logger(),
        user_id=7,
        request_id="",
        endpoint="test",
    )

    assert result == "hello"
    assert service.complete_calls == []


async def test_first_request_executes_and_records_completion(monkeypatch) -> None:
    service = FakeIdempotencyService([FakeDecision(True)])
    _patch_service(monkeypatch, service)

    result = await run_idempotent_action(
        "test",
        _ok({"conversation_id": 42}),
        logger=_Logger(),
        user_id=7,
        request_id="req-1",
        endpoint="create_conversation",
    )

    assert result == {"conversation_id": 42}
    assert service.begin_calls == [(7, "req-1", "create_conversation")]
    assert service.complete_calls[0]["response_status"] == 200
    assert json.loads(service.complete_calls[0]["response_body"]) == {"conversation_id": 42}


async def test_duplicate_returns_cached_response(monkeypatch) -> None:
    service = FakeIdempotencyService(
        [FakeDecision(False, cached_status=200, cached_body='{"conversation_id": 42}')]
    )
    _patch_service(monkeypatch, service)

    result = await run_idempotent_action(
        "test",
        _ok({"conversation_id": 999}),  # 不应被执行
        logger=_Logger(),
        user_id=7,
        request_id="req-1",
        endpoint="create_conversation",
    )

    assert result == {"conversation_id": 42}


async def test_duplicate_without_snapshot_raises_409(monkeypatch) -> None:
    service = FakeIdempotencyService([FakeDecision(False, cached_status=200, cached_body="")])
    _patch_service(monkeypatch, service)

    with pytest.raises(HTTPException) as exc:
        await run_idempotent_action(
            "test",
            _ok({"x": 1}),
            logger=_Logger(),
            user_id=7,
            request_id="req-1",
            endpoint="test",
        )

    assert exc.value.status_code == 409


async def test_business_failure_marks_failed_and_reraises(monkeypatch) -> None:
    from app.platform.idempotency import RequestIdempotencyError

    service = FakeIdempotencyService([FakeDecision(True)])
    service.begin_error = RequestIdempotencyError("该请求已提交且仍在处理中")

    from app.platform.idempotency import idempotency_service as real_service

    monkeypatch.setattr(real_service, "begin", service.begin)
    monkeypatch.setattr(real_service, "complete", service.complete)
    monkeypatch.setattr(real_service, "mark_failed", service.mark_failed)

    with pytest.raises(HTTPException) as exc:
        await run_idempotent_action(
            "test",
            _ok(1),
            logger=_Logger(),
            user_id=7,
            request_id="req-1",
            endpoint="test",
        )

    assert exc.value.status_code == 409


async def test_http_exception_marks_failed(monkeypatch) -> None:
    service = FakeIdempotencyService([FakeDecision(True)])
    _patch_service(monkeypatch, service)

    async def broken():
        raise HTTPException(status_code=400, detail="bad input")

    with pytest.raises(HTTPException) as exc:
        await run_idempotent_action(
            "test",
            broken(),
            logger=_Logger(),
            user_id=7,
            request_id="req-1",
            endpoint="test",
        )

    assert exc.value.status_code == 400
    assert service.failed_calls[0]["error"] == "bad input"


async def test_resource_not_found_marks_failed(monkeypatch) -> None:
    service = FakeIdempotencyService([FakeDecision(True)])
    _patch_service(monkeypatch, service)

    async def not_found():
        raise ResourceNotFoundError("不存在")

    with pytest.raises(HTTPException) as exc:
        await run_idempotent_action(
            "test",
            not_found(),
            logger=_Logger(),
            user_id=7,
            request_id="req-1",
            endpoint="test",
        )

    assert exc.value.status_code == 404
    assert service.failed_calls


async def _ok(value):
    return value
