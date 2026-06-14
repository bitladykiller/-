import asyncio

import app.chat.infrastructure.memory_bridge.context as memory_context
import app.chat.infrastructure.memory_bridge.runtime as lg_memory_runtime


class FakeMiddleware:
    def __init__(self) -> None:
        self.closed = False


def _run(awaitable):
    return asyncio.run(awaitable)


def test_configurable_scope_reads_values_and_defaults() -> None:
    assert memory_context.configurable_scope(
        {
            "configurable": {
                "tenant_id": "tenant-1",
                "user_id": "user-2",
                "thread_id": "thread-3",
            }
        }
    ) == ("tenant-1", "user-2", "thread-3")
    assert memory_context.configurable_scope({}) == (
        "default",
        "anonymous",
        "default",
    )


def test_get_memory_middleware_caches_created_instance(monkeypatch) -> None:
    middleware = FakeMiddleware()
    calls: list[str] = []
    monkeypatch.setattr(lg_memory_runtime, "_memory_middleware_instance", None)

    def fake_create_memory_middleware_instance():
        calls.append("create")
        return middleware

    monkeypatch.setattr(
        lg_memory_runtime,
        "create_memory_middleware_instance",
        fake_create_memory_middleware_instance,
    )

    first = _run(lg_memory_runtime.get_memory_middleware())
    second = _run(lg_memory_runtime.get_memory_middleware())

    assert first is middleware
    assert second is middleware
    assert calls == ["create"]


def test_get_memory_middleware_logs_and_returns_none_on_failure(monkeypatch) -> None:
    messages: list[tuple[str, bool]] = []
    monkeypatch.setattr(lg_memory_runtime, "_memory_middleware_instance", None)

    class FakeLogger:
        def error(self, message: str, *args, **kwargs) -> None:
            messages.append((message, kwargs.get("exc_info", False)))

    def failing_factory():
        raise RuntimeError("boom")

    monkeypatch.setattr(lg_memory_runtime, "logger", FakeLogger())
    monkeypatch.setattr(
        lg_memory_runtime,
        "create_memory_middleware_instance",
        failing_factory,
    )

    result = _run(lg_memory_runtime.get_memory_middleware())

    assert result is None
    assert messages == [("MemoryMiddleware 初始化失败，将以无记忆模式运行", True)]


def test_close_memory_middleware_closes_resources_and_resets_singleton(monkeypatch) -> None:
    middleware = FakeMiddleware()
    calls: list[FakeMiddleware] = []
    monkeypatch.setattr(lg_memory_runtime, "_memory_middleware_instance", middleware)

    async def fake_close_memory_resources(current):
        calls.append(current)

    monkeypatch.setattr(
        lg_memory_runtime,
        "close_memory_resources",
        fake_close_memory_resources,
    )

    _run(lg_memory_runtime.close_memory_middleware())

    assert calls == [middleware]
    assert lg_memory_runtime._memory_middleware_instance is None
