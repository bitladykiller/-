"""async_bridge 单测。

这一层的价值全在"真的换了线程"上：如果 run_blocking 退化成直接调用，
所有 Milvus / embedding 调用就会重新阻塞事件循环，而功能测试完全看不出来。
所以这里明确断言线程身份，而不只是断言返回值。
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from app.shared.core.async_bridge import run_blocking, run_blocking_method


async def test_run_blocking_returns_value_and_passes_arguments() -> None:
    def add(a: int, b: int, *, scale: int = 1) -> int:
        return (a + b) * scale

    assert await run_blocking(add, 2, 3) == 5
    assert await run_blocking(add, 2, 3, scale=10) == 50


async def test_run_blocking_executes_off_the_event_loop_thread() -> None:
    """核心保证：同步函数不在事件循环所在线程上执行。"""
    loop_thread = threading.current_thread().ident
    worker_thread = await run_blocking(lambda: threading.current_thread().ident)

    assert worker_thread is not None
    assert worker_thread != loop_thread


async def test_run_blocking_keeps_event_loop_responsive() -> None:
    """阻塞调用进行期间，事件循环仍能调度其它协程。"""
    started = threading.Event()
    may_finish = threading.Event()
    progressed = False

    def blocking() -> str:
        started.set()
        may_finish.wait(timeout=5)
        return "done"

    async def other_work() -> None:
        nonlocal progressed
        # 等阻塞任务确实开跑，再确认自己仍能被调度
        while not started.is_set():
            await asyncio.sleep(0)
        progressed = True
        may_finish.set()

    blocking_result, _ = await asyncio.gather(run_blocking(blocking), other_work())

    assert blocking_result == "done"
    assert progressed is True


async def test_run_blocking_propagates_exceptions_unchanged() -> None:
    def boom() -> None:
        raise ValueError("原样抛出")

    with pytest.raises(ValueError, match="原样抛出"):
        await run_blocking(boom)


async def test_run_blocking_method_invokes_named_method() -> None:
    class Client:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple, dict]] = []

        def query(self, *args, **kwargs) -> str:
            self.calls.append((args, kwargs))
            return "rows"

    client = Client()

    assert await run_blocking_method(client, "query", 1, limit=2) == "rows"
    assert client.calls == [((1,), {"limit": 2})]


async def test_run_blocking_method_raises_on_missing_method() -> None:
    with pytest.raises(AttributeError):
        await run_blocking_method(object(), "not_there")
