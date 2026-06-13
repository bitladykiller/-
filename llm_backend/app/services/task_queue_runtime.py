"""TaskQueue 运行时单例管理。

职责：
- 托管 `TaskManager` 这类可关闭运行时对象的单例生命周期
- 统一处理懒创建、全局引用切换和关闭时的兜底保护

边界：
- 不负责任务状态流转
- 不负责 Redis payload 编解码
- 不负责后台协程执行
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol, TypeVar, cast


class ClosableRuntime(Protocol):
    """可被运行时管理器托管的最小关闭契约。"""

    async def close(self) -> object: ...


RuntimeT = TypeVar("RuntimeT", bound=ClosableRuntime)

_runtime_instance: ClosableRuntime | None = None
_runtime_lock: asyncio.Lock = asyncio.Lock()


def current_runtime() -> ClosableRuntime | None:
    """返回当前已创建的运行时单例。"""
    return _runtime_instance


def reset_runtime() -> ClosableRuntime | None:
    """取出当前单例并立即清空全局引用。"""
    global _runtime_instance
    runtime = _runtime_instance
    _runtime_instance = None
    return runtime


async def get_or_create_runtime(factory: Callable[[], RuntimeT]) -> RuntimeT:
    """按需创建运行时单例，并保证并发场景只初始化一次。"""
    global _runtime_instance
    runtime = _runtime_instance
    if runtime is not None:
        return cast(RuntimeT, runtime)

    async with _runtime_lock:
        runtime = _runtime_instance
        if runtime is not None:
            return cast(RuntimeT, runtime)

        created = factory()
        _runtime_instance = created
        return created


async def close_runtime_safely(runtime: ClosableRuntime) -> None:
    """尽力关闭运行时对象，关闭失败也不阻断 shutdown 流程。"""
    try:
        await runtime.close()
    except Exception:
        return
