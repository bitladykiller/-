"""同步阻塞调用 → 事件循环的桥接层。

这个模块负责：
- 把第三方同步 SDK（pymilvus、LangChain Embeddings）的阻塞调用挪出事件循环
- 为"看起来是 async、其实在同步阻塞"的调用点提供统一写法

这个模块不负责：
- 业务逻辑
- 线程池容量治理（沿用 asyncio 默认 executor）

WHY 需要这一层：
`pymilvus.MilvusClient` 和 LangChain 的 `Embeddings` 都是**同步**接口。
在 `async def` 里直接调用它们，协程不会让出控制权——单进程 FastAPI 下，
一次 LTM 检索（embedding 推理 + Milvus RTT，几百毫秒到数秒）会把
**当前进程所有并发请求**一起卡住，包括健康检查和 SSE 心跳。

`asyncio.to_thread` 把调用放进默认线程池执行，事件循环在等待期间可以继续
调度其它协程。CPU 密集的 embedding 推理（sentence-transformers / torch）
在计算时会释放 GIL，网络等待型调用（Ollama HTTP、Milvus gRPC）本身就不占 GIL，
两类负载都能真正并行。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import partial
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


async def run_blocking(
    func: Callable[P, R],
    /,
    *args: P.args,
    **kwargs: P.kwargs,
) -> R:
    """在默认线程池中执行同步函数，避免阻塞事件循环。

    Args:
        func: 任意同步可调用对象。
        *args / **kwargs: 透传给 `func` 的参数。

    Returns:
        `func` 的返回值。异常原样向上抛出，调用方的 try/except 语义不变。

    Example:
        >>> vector = await run_blocking(embedding_model.embed_query, text)
    """
    return await asyncio.to_thread(func, *args, **kwargs)


async def run_blocking_method(
    obj: Any,
    method_name: str,
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """按名字调用对象上的同步方法，方法缺失时抛 AttributeError。

    用于鸭子类型的第三方客户端（不同 pymilvus 版本方法集有差异），
    调用方可以先 `hasattr` 探测再决定走哪条路径。
    """
    method = getattr(obj, method_name)
    return await asyncio.to_thread(partial(method, *args, **kwargs))


__all__ = ["run_blocking", "run_blocking_method"]
