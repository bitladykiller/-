"""LangGraph 记忆运行时 — 已废弃。

所有功能已迁移至 app/platform/container.py 的 AppContainer 中。
此文件仅保留向后兼容的别名和重新导出，供已有调用方过渡使用。
"""

from __future__ import annotations

import warnings

from app.platform.container import get_container as _get_container
from app.platform.container import reset_container

warnings.warn(
    "app.chat.infrastructure.memory_bridge.runtime 已废弃，"
    "所有功能已迁移至 app.platform.container.AppContainer",
    DeprecationWarning,
    stacklevel=2,
)

# 保留 _memory_middleware_instance 属性供旧测试 monkeypatch 使用
_memory_middleware_instance = None


async def warm_up_memory_middleware() -> None:
    container = await _get_container()
    await container.warm_up()


async def close_memory_middleware() -> None:
    await reset_container()


async def get_memory_middleware():
    try:
        container = await _get_container()
        return container.memory_middleware
    except Exception:
        import logging

        logging.getLogger(__name__).error(
            "MemoryMiddleware 初始化失败，将以无记忆模式运行", exc_info=True
        )
        return None


def create_memory_middleware_instance():
    raise RuntimeError("请使用 AppContainer.build() 初始化记忆中间件")


async def close_memory_resources(middleware) -> None:
    from app.platform.container import _close_memory_resources

    await _close_memory_resources(middleware)


__all__ = [
    "close_memory_resources",
    "close_memory_middleware",
    "create_memory_middleware_instance",
    "get_memory_middleware",
    "warm_up_memory_middleware",
]
