import asyncio

import app.services.task_queue_runtime as task_queue_runtime


class FakeRuntime:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.closed = False

    async def close(self) -> None:
        self.closed = True
        if self.should_fail:
            raise RuntimeError("close failed")


def test_get_or_create_runtime_reuses_existing_instance() -> None:
    async def scenario() -> None:
        task_queue_runtime.reset_runtime()
        created: list[FakeRuntime] = []

        def factory() -> FakeRuntime:
            runtime = FakeRuntime()
            created.append(runtime)
            return runtime

        first = await task_queue_runtime.get_or_create_runtime(factory)
        second = await task_queue_runtime.get_or_create_runtime(factory)

        assert first is second
        assert created == [first]
        assert task_queue_runtime.reset_runtime() is first

    asyncio.run(scenario())


def test_close_runtime_safely_swallows_close_errors() -> None:
    async def scenario() -> None:
        runtime = FakeRuntime(should_fail=True)
        await task_queue_runtime.close_runtime_safely(runtime)
        assert runtime.closed is True

    asyncio.run(scenario())
