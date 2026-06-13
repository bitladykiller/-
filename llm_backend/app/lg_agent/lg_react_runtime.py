"""ReAct 子图运行时缓存。

这个模块负责：
- 管理 ReAct 子图的模块级单例
- 用双检锁避免并发重复构建

这个模块不负责：
- 定义 ReAct 工具
- 编排答案充分性重试
- 处理主图状态
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from langgraph.graph.state import CompiledStateGraph

_react_subgraph: CompiledStateGraph | None = None
_react_lock: asyncio.Lock = asyncio.Lock()


async def get_react_subgraph(
    builder: Callable[[], Awaitable[CompiledStateGraph]],
) -> CompiledStateGraph:
    """获取 ReAct 子图单例（双检锁防并发创建）。"""
    global _react_subgraph
    if _react_subgraph is None:
        async with _react_lock:
            if _react_subgraph is None:
                _react_subgraph = await builder()
    return _react_subgraph
