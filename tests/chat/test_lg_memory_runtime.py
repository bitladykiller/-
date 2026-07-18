import asyncio

import app.chat.infrastructure.graph.memory_context as memory_context


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

    async def fake_get_container():
        class FakeContainer:
            memory_middleware = middleware

            async def warm_up(self):
                pass

            async def close(self):
                pass

        return FakeContainer()

    monkeypatch.setattr(
        "app.platform.container.get_container", fake_get_container
    )

    from app.platform.container import get_container

    async def get_middleware():
        container = await get_container()
        return container.memory_middleware

    first = _run(get_middleware())
    second = _run(get_middleware())

    assert first is middleware
    assert second is middleware


def test_get_memory_middleware_logs_and_returns_none_on_failure(monkeypatch) -> None:
    async def failing_container():
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "app.platform.container.get_container", failing_container
    )

    from app.platform.container import get_container

    async def get_middleware():
        try:
            container = await get_container()
            return container.memory_middleware
        except Exception:
            return None

    result = _run(get_middleware())
    assert result is None


def test_close_memory_middleware_closes_resources_and_resets_singleton(monkeypatch) -> None:
    from app.platform.container import reset_container

    _run(reset_container())

    from app.platform import container as cont_mod
    assert cont_mod._container is None