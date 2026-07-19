"""Agent 查询应用门面。

职责：
- 为 API 层提供稳定的 LangGraph 查询入口
- 封装 InputState / graph.astream 细节

边界：
- 不负责 HTTP / SSE 协议转换
- 不负责节点内部业务逻辑
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, TypeAlias

from app.chat.infrastructure.graph.builder import graph
from app.chat.infrastructure.graph.state import InputState
from langchain_core.messages import HumanMessage

_ChunkMetadata: TypeAlias = Mapping[str, Any]
_GraphStreamChunk: TypeAlias = tuple[Any, _ChunkMetadata]
GraphStream: TypeAlias = AsyncIterator[_GraphStreamChunk]
STREAM_MODE_MESSAGES = "messages"


def stream_agent_query(
    *,
    query: str,
    user_id: int | str,
    thread_id: str,
) -> GraphStream:
    """启动 Agent 图流式执行。"""
    return graph.astream(
        input=InputState(messages=[HumanMessage(content=query)]),
        stream_mode=STREAM_MODE_MESSAGES,
        config={
            "configurable": {
                "thread_id": thread_id,
                "user_id": str(user_id),
            }
        },
    )


__all__ = ["stream_agent_query", "STREAM_MODE_MESSAGES", "GraphStream"]
