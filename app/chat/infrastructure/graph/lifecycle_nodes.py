"""主图中的响应后处理节点实现。"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.chat.infrastructure.graph.message_utils import (
    find_last_assistant_message,
    find_last_user_message,
)
from app.chat.infrastructure.graph.state import AgentState
from app.chat.infrastructure.memory_bridge.context import configurable_scope, get_memory_middleware
from app.shared.core.logger import get_logger

logger = get_logger(__name__)


def build_after_response_payload(
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    messages: list[object],
) -> dict[str, str] | None:
    """提取写回记忆所需的 user / assistant 消息对。"""
    user_message = find_last_user_message(messages)
    assistant_message = find_last_assistant_message(messages)
    if not user_message or not assistant_message:
        return None

    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "session_id": session_id,
        "user_message": user_message,
        "assistant_message": assistant_message,
    }


async def after_response(state: AgentState, *, config: RunnableConfig) -> dict:
    """将本轮对话写入 Redis STM，并触发 LTM 抽取。"""
    middleware = await get_memory_middleware()
    if middleware is None:
        return {}

    try:
        tenant_id, user_id, session_id = configurable_scope(config)
        payload = build_after_response_payload(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            messages=state.messages,
        )
        if payload:
            await middleware.after_agent(**payload)
    except Exception:
        logger.warning("[memory] after_response 记忆写入失败，本轮对话可能丢失", exc_info=True)
    return {}


__all__ = ["after_response", "build_after_response_payload"]
