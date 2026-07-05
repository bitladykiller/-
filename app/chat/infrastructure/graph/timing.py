"""图节点耗时打点。

每个 LangGraph 节点包一层计时：配合日志里的 request_id，
一条请求的耗时分布（路由多久、检索多久、生成多久）直接可读，
不用再靠相邻日志时间戳心算。
"""

from __future__ import annotations

import functools
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.shared.core.logger import get_logger

logger = get_logger("app.chat.graph.timing")

NodeHandler = TypeVar("NodeHandler", bound=Callable[..., Awaitable[Any]])


def timed_node(node_name: str, handler: NodeHandler) -> NodeHandler:
    """包装图节点，记录执行耗时；异常时同样记录后原样抛出。"""

    @functools.wraps(handler)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        try:
            result = await handler(*args, **kwargs)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info("node=%s elapsed=%.1fms outcome=error", node_name, elapsed_ms)
            raise
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("node=%s elapsed=%.1fms", node_name, elapsed_ms)
        return result

    return wrapper  # type: ignore[return-value]


__all__ = ["timed_node"]
