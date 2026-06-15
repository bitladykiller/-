"""主图中的响应后处理节点实现。"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.chat.infrastructure.graph.message_utils import (
    find_last_assistant_message,
    find_last_user_message,
)
from app.chat.infrastructure.graph.state import AgentState
from app.chat.infrastructure.memory_bridge.context import (
    _DEFAULT_SESSION_ID,
    _DEFAULT_TENANT_ID,
    _DEFAULT_USER_ID,
    get_memory_middleware,
)
from app.shared.core.logger import get_logger

logger = get_logger(__name__)


async def after_response(state: AgentState, *, config: RunnableConfig) -> dict:
    """将本轮对话写入 Redis STM，并触发 LTM 抽取。"""
    middleware = await get_memory_middleware()
    if middleware is None:
        return {}

    try:
        raw_configurable = config.get("configurable", {})
        configurable = raw_configurable if isinstance(raw_configurable, dict) else {}
        tenant_id = configurable.get("tenant_id")
        user_id = configurable.get("user_id")
        thread_id = configurable.get("thread_id")
        user_message = find_last_user_message(state.messages)
        assistant_message = find_last_assistant_message(state.messages)
        if user_message and assistant_message:
            await middleware.after_agent(
                tenant_id=tenant_id if isinstance(tenant_id, str) and tenant_id else _DEFAULT_TENANT_ID,
                user_id=user_id if isinstance(user_id, str) and user_id else _DEFAULT_USER_ID,
                session_id=thread_id if isinstance(thread_id, str) and thread_id else _DEFAULT_SESSION_ID,
                user_message=user_message,
                assistant_message=assistant_message,
            )
    except Exception:
        logger.warning("[memory] after_response 记忆写入失败，本轮对话可能丢失", exc_info=True)
    return {}


__all__ = ["after_response"]
