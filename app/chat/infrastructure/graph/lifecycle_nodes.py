"""主图中的响应后处理节点实现。"""

from app.chat.infrastructure.graph.state import AgentState
from app.chat.infrastructure.memory_bridge.context import (
    _DEFAULT_SESSION_ID,
    _DEFAULT_TENANT_ID,
    _DEFAULT_USER_ID,
    get_memory_middleware,
)
from app.shared.core.logger import get_logger
from langchain_core.messages import AnyMessage, ChatMessage
from langchain_core.runnables import RunnableConfig

logger = get_logger(__name__)


def _extract_message_fields(message: AnyMessage) -> tuple[str, str]:
    """把 LangChain 消息对象收口为统一的 role/content。"""
    raw_role = message.role if isinstance(message, ChatMessage) else message.type
    role = {
        "human": "user",
        "ai": "assistant",
    }.get(raw_role, raw_role)
    content = str(message.content or "")
    return role, content


async def after_response(state: AgentState, *, config: RunnableConfig) -> dict:
    """将本轮对话写入 Redis STM，并触发 LTM 抽取。"""
    middleware = await get_memory_middleware()
    if middleware is None:
        return {}

    try:
        configurable = config.get("configurable", {})
        tenant_id = configurable.get("tenant_id")
        user_id = configurable.get("user_id")
        thread_id = configurable.get("thread_id")
        user_message = ""
        assistant_message = ""
        assistant_fallback = ""
        for message in reversed(state.messages):
            role, content = _extract_message_fields(message)

            if not user_message and role == "user":
                user_message = content

            if not assistant_message and role == "assistant":
                if not assistant_fallback:
                    assistant_fallback = content
                if content and "正在" in content:
                    continue
                assistant_message = content

            if user_message and assistant_message:
                break

        if not assistant_message:
            assistant_message = assistant_fallback
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
