"""FastAPI 应用入口的运行时生命周期 helper。

职责：
- 承接 startup / shutdown 阶段的运行时资源预热与释放
- 提供可复用的 lifespan 构造器，避免应用工厂混入资源管理细节

边界：
- 不负责 CORS、中间件、路由或静态资源注册
- 不直接持有全局 FastAPI app 单例
- 不负责具体业务逻辑实现
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import FastAPI


class InfoLogger(Protocol):
    """运行时 helper 所需的最小日志接口。"""

    def info(self, msg: str, *args: object, **kwargs: object) -> object: ...


async def warm_up_runtime_resources(logger: InfoLogger) -> None:
    """预热懒加载资源，避免首请求承担初始化延迟。"""
    from app.lg_agent.memory_bridge.runtime import warm_up_memory_middleware

    logger.info("预热 MemoryMiddleware...")
    await warm_up_memory_middleware()


async def close_runtime_resources() -> None:
    """释放应用级运行时资源。"""
    from app.lg_agent.memory_bridge.runtime import close_memory_middleware
    from app.services.task_queue import close_task_manager

    await close_memory_middleware()
    await close_task_manager()


def build_lifespan(
    logger: InfoLogger,
    *,
    warm_up: Callable[[InfoLogger], Awaitable[None]] = warm_up_runtime_resources,
    close_runtime: Callable[[], Awaitable[None]] = close_runtime_resources,
) -> Callable[[FastAPI], object]:
    """构造 FastAPI lifespan 处理器。"""

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await warm_up(logger)
        logger.info("启动完成")
        try:
            yield
        finally:
            logger.info("关闭连接...")
            await close_runtime()
            logger.info("关闭完成")

    return lifespan


__all__ = [
    "InfoLogger",
    "build_lifespan",
    "close_runtime_resources",
    "warm_up_runtime_resources",
]
