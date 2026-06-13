"""LangGraph Agent facade。

对外暴露能力分包后的统一入口，同时保留旧平铺模块的兼容路径。
"""

from app.lg_agent.graph.builder import graph
from app.lg_agent.memory_bridge.runtime import (
    close_memory_middleware,
    configurable_scope,
    get_memory_middleware,
    warm_up_memory_middleware,
)
from app.lg_agent.retrieval.registry import get_retriever

__all__ = [
    "graph",
    "configurable_scope",
    "get_memory_middleware",
    "warm_up_memory_middleware",
    "close_memory_middleware",
    "get_retriever",
]
