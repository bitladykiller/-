"""LangGraph 记忆上下文组装层。

这个模块负责：
- 统一加载当前请求的记忆状态
- 为上层节点提供记忆请求入口（加载 / 富化问题）

这个模块不负责：
- LangGraph 节点路由
- 具体检索执行
- 记忆抽取和持久化细节
- 具体的上下文文本拼装
- 运行时依赖初始化和单例生命周期
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.lg_agent.graph.state import AgentState
from app.lg_agent.memory_bridge.prompt import build_enriched_question
from app.lg_agent.memory_bridge.runtime import (
    close_memory_middleware,
    configurable_scope,
    get_memory_middleware,
    warm_up_memory_middleware,
)
from app.memory.schemas import AgentMemoryState


async def load_memory_state(
    state: AgentState,
    config: RunnableConfig,
    user_input: str,
) -> AgentMemoryState | None:
    """加载并缓存当前请求的记忆状态。"""
    if state.memory_state is not None:
        return state.memory_state

    middleware = await get_memory_middleware()
    if middleware is None:
        return None

    try:
        tenant_id, user_id, session_id = configurable_scope(config)
        memory_state = await middleware.before_agent(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            user_input=user_input,
        )
    except Exception:
        return None

    state.memory_state = memory_state
    return memory_state


async def enrich_question(
    state: AgentState,
    config: RunnableConfig,
    question: str,
) -> str:
    """将记忆上下文注入到检索问题中。"""
    mem = await load_memory_state(state, config, question)
    if mem is None:
        return question

    return build_enriched_question(question, mem)


__all__ = [
    "close_memory_middleware",
    "configurable_scope",
    "enrich_question",
    "get_memory_middleware",
    "load_memory_state",
    "warm_up_memory_middleware",
]
