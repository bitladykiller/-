import asyncio
import sys
import types

import app.main_runtime_support as main_runtime_support


class FakeLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, tuple[object, ...]]] = []

    def info(self, msg: str, *args: object, **kwargs: object) -> object:
        self.messages.append((msg, args))
        return None


def _run(awaitable):
    return asyncio.run(awaitable)


def test_warm_up_runtime_resources_delegates_to_memory_warmup(monkeypatch) -> None:
    logger = FakeLogger()
    called: list[str] = []
    fake_module = types.ModuleType("app.chat.infrastructure.memory_bridge.runtime")

    async def fake_warm_up_memory_middleware() -> None:
        called.append("warm_up_memory")

    fake_module.warm_up_memory_middleware = fake_warm_up_memory_middleware
    monkeypatch.setitem(
        sys.modules,
        "app.chat.infrastructure.memory_bridge.runtime",
        fake_module,
    )

    _run(main_runtime_support.warm_up_runtime_resources(logger))

    assert called == ["warm_up_memory"]
    assert logger.messages == [("预热 MemoryMiddleware...", ())]


def test_close_runtime_resources_delegates_to_runtime_closers(monkeypatch) -> None:
    called: list[str] = []
    fake_lg_context = types.ModuleType("app.chat.infrastructure.memory_bridge.runtime")
    fake_task_queue = types.ModuleType("app.chat.application.task_queue")

    async def fake_close_memory_middleware() -> None:
        called.append("close_memory")

    async def fake_close_task_manager() -> None:
        called.append("close_task")

    fake_lg_context.close_memory_middleware = fake_close_memory_middleware
    fake_task_queue.close_task_manager = fake_close_task_manager
    monkeypatch.setitem(
        sys.modules,
        "app.chat.infrastructure.memory_bridge.runtime",
        fake_lg_context,
    )
    monkeypatch.setitem(sys.modules, "app.chat.application.task_queue", fake_task_queue)

    _run(main_runtime_support.close_runtime_resources())

    assert called == ["close_memory", "close_task"]


def test_build_lifespan_delegates_runtime_hooks() -> None:
    logger = FakeLogger()
    called: list[str] = []

    async def fake_warm_up(logger_obj) -> None:
        assert logger_obj is logger
        called.append("warm_up")

    async def fake_close_runtime() -> None:
        called.append("close_runtime")

    async def scenario() -> None:
        lifespan = main_runtime_support.build_lifespan(
            logger,
            warm_up=fake_warm_up,
            close_runtime=fake_close_runtime,
        )
        async with lifespan(object()):
            assert called == ["warm_up"]

    _run(scenario())

    assert called == ["warm_up", "close_runtime"]
    assert logger.messages == [
        ("启动完成", ()),
        ("关闭连接...", ()),
        ("关闭完成", ()),
    ]
