"""LangGraph Agent 包入口。

职责：
- 承载 Agent 主图、子图、节点、状态、模型和检索适配层
- 作为智能客服主流程的编排中心

边界：
- HTTP 参数解析与 SSE 输出留在 `api/langgraph.py`
- 记忆读写细节留在 `memory/`
- 通用业务服务留在 `services/`
"""

from app.lg_agent.facade import (
    close_memory_middleware,
    configurable_scope,
    get_memory_middleware,
    get_retriever,
    graph,
    warm_up_memory_middleware,
)

__all__ = [
    "graph",
    "configurable_scope",
    "get_memory_middleware",
    "warm_up_memory_middleware",
    "close_memory_middleware",
    "get_retriever",
]
