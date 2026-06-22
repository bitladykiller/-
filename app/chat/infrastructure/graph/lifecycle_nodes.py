"""主图中的响应后处理节点实现。

职责：
- 将本轮对话写入 Redis STM，并触发 LTM 抽取
- 通过 AppContainer 获取 MemoryMiddleware
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.chat.infrastructure.graph.message_utils import (
    find_last_assistant_message,
    find_last_user_message,
)
from app.chat.infrastructure.graph.state import AgentState
from app.chat.infrastructure.memory_bridge.context import _get_memory_middleware, configurable_scope
from app.shared.core.logger import get_logger

logger = get_logger(__name__)


async def after_response(state: AgentState, *, config: RunnableConfig) -> dict:
    """将本轮对话写入 Redis STM，并触发 LTM 抽取。"""
    middleware = await _get_memory_middleware()
    if middleware is None:
        return {}

    try:
        tenant_id, user_id, session_id = configurable_scope(config)
        user_message = find_last_user_message(state.messages)
        assistant_message = find_last_assistant_message(state.messages)
        if user_message and assistant_message:
            await middleware.after_agent(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                user_message=user_message,
                assistant_message=assistant_message,
            )
    except Exception:
        logger.warning("[memory] after_response 记忆写入失败，本轮对话可能丢失", exc_info=True)
    return {}


__all__ = ["after_response"]
