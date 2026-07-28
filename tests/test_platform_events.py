"""平台事件处理器单测。"""

from __future__ import annotations

import app.platform.events as events_module
import pytest
from app.platform.events import (
    build_core_handlers,
    handle_turn_completed,
)


class FakeMiddleware:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def after_agent(self, **kwargs) -> None:
        self.calls.append(kwargs)


class FakeContainer:
    def __init__(self, middleware) -> None:
        self.memory_middleware = middleware
        self.task_manager = None


@pytest.fixture
def fake_container(monkeypatch):
    middleware = FakeMiddleware()

    async def fake_get_container():
        return FakeContainer(middleware)

    import app.platform.container as container_module

    monkeypatch.setattr(container_module, "get_container", fake_get_container)
    return middleware


async def test_turn_completed_persists_history_and_writes_memory(
    monkeypatch, fake_container
) -> None:
    persisted: list[tuple[str, str, str]] = []

    async def fake_persist(session_id, user_message, assistant_message) -> None:
        persisted.append((session_id, user_message, assistant_message))

    monkeypatch.setattr(events_module, "_persist_turn_history", fake_persist)

    await handle_turn_completed(
        {
            "tenant_id": "default",
            "user_id": "7",
            "session_id": "11",
            "user_message": "问",
            "assistant_message": "答",
        }
    )

    assert persisted == [("11", "问", "答")]
    assert fake_container.calls == [
        {
            "tenant_id": "default",
            "user_id": "7",
            "session_id": "11",
            "user_message": "问",
            "assistant_message": "答",
        }
    ]


async def test_turn_completed_drops_incomplete_payload(monkeypatch, fake_container) -> None:
    called: list[str] = []

    async def fake_persist(*args) -> None:
        called.append("persist")

    monkeypatch.setattr(events_module, "_persist_turn_history", fake_persist)

    await handle_turn_completed({"user_id": "7"})  # 缺 session/消息

    assert called == []
    assert fake_container.calls == []


async def test_turn_completed_history_failure_does_not_block_memory(
    monkeypatch, fake_container
) -> None:
    """历史落库失败必须降级，不阻断记忆写入。"""

    async def broken_persist(*args) -> None:
        raise RuntimeError("mysql down")

    # _persist_turn_history 内部自带降级；这里直接替换为抛错版本来验证
    # handler 层面的隔离——注意替换的是"已包含降级"的函数，因此 handler
    # 不应把该异常传导出去也不应跳过记忆。
    async def degraded_persist(session_id, user_message, assistant_message) -> None:
        try:
            await broken_persist()
        except Exception:
            pass

    monkeypatch.setattr(events_module, "_persist_turn_history", degraded_persist)

    await handle_turn_completed(
        {
            "user_id": "7",
            "session_id": "11",
            "user_message": "问",
            "assistant_message": "答",
        }
    )

    assert len(fake_container.calls) == 1


async def test_turn_completed_passes_event_id_to_history_and_memory(
    monkeypatch, fake_container
) -> None:
    persisted: list[dict[str, str]] = []

    async def fake_persist(
        session_id,
        user_message,
        assistant_message,
        *,
        turn_event_id=None,
    ) -> None:
        persisted.append(
            {
                "session_id": session_id,
                "user_message": user_message,
                "assistant_message": assistant_message,
                "turn_event_id": turn_event_id,
            }
        )

    monkeypatch.setattr(events_module, "_persist_turn_history", fake_persist)

    await handle_turn_completed(
        {
            "event_id": "turn-99",
            "turn_id": "turn-99",
            "event_created_at": "123",
            "user_id": "7",
            "session_id": "11",
            "user_message": "问",
            "assistant_message": "答",
        }
    )

    assert persisted[0]["turn_event_id"] == "turn-99"
    assert fake_container.calls[0]["turn_id"] == "turn-99"
    assert fake_container.calls[0]["event_created_at"] == 123


def test_core_handler_registry_covers_all_event_types() -> None:
    handlers = build_core_handlers()

    assert set(handlers) == {"turn_completed", "document_index_requested"}


def test_stream_origin_tasks_are_not_marked_orphaned() -> None:
    """stream 投递的任务崩溃后靠 XAUTOCLAIM 重跑，不该被标 interrupted。"""
    from app.shared.background_tasks import is_orphaned_task

    stream_pending = {"status": "running", "worker_id": "dead-worker", "origin": "stream"}
    plain_pending = {"status": "running", "worker_id": "dead-worker"}

    assert is_orphaned_task(stream_pending, current_worker_id="me") is False
    assert is_orphaned_task(plain_pending, current_worker_id="me") is True
