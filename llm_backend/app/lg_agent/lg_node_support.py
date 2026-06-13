"""`lg_nodes.py` 共享的轻量 helper。

这个模块负责：
- 节点间边路由的纯映射规则
- 问题包装和记忆上下文拼接
- `after_response` 写回前的消息提取与参数拼装

这个模块不负责：
- 调用模型
- 发起检索
- 初始化记忆中间件
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.messages import AIMessage

from app.lg_agent.lg_message_utils import (
    find_last_assistant_message,
    find_last_user_message,
)
from app.security import wrap_user_message

GeneralRouteName = Literal["respond_to_general_query", "retrieval_plan_router"]
GuardrailsEdgeName = Literal["retrieval_plan_route", "after_response"]
RetrievalEdgeName = Literal[
    "execute_graph_only",
    "execute_rag_only",
    "execute_parallel",
    "execute_then",
    "execute_react",
]

GUARDRAILS_BLOCK_MESSAGE = "抱歉，我家暂时没有这方面的商品，可以在别家看看哦～"
_RETRIEVAL_EDGE_MAP: dict[str, RetrievalEdgeName] = {
    "GRAPH_ONLY": "execute_graph_only",
    "RAG_ONLY": "execute_rag_only",
    "PARALLEL": "execute_parallel",
    "GRAPH_THEN_RAG": "execute_then",
    "AGENT_REACT": "execute_react",
}


def build_memory_augmented_system_prompt(
    *,
    system_prompt: str,
    memory_context: str,
) -> str:
    """把记忆上下文追加到 system prompt。无上下文时保持原样。"""
    if not memory_context:
        return system_prompt
    return system_prompt + memory_context


def build_wrapped_question(question: str) -> str:
    """对原始问题做 XML 隔离，供结构化判定节点复用。"""
    safe_question, _ = wrap_user_message(question)
    return safe_question


def route_query_type(route_type: str) -> GeneralRouteName:
    """把顶层 router 的类型映射成主图下一个节点。"""
    if route_type == "general":
        return "respond_to_general_query"
    return "retrieval_plan_router"


def route_guardrails_action(next_action: str | None) -> GuardrailsEdgeName:
    """把 guardrails 的决策映射成主图下一个节点。"""
    if next_action == "end":
        return "after_response"
    return "retrieval_plan_route"


def route_retrieval_plan(plan_name: str | None) -> RetrievalEdgeName:
    """把 RetrievalPlan 输出映射成执行节点名。"""
    return _RETRIEVAL_EDGE_MAP.get(plan_name or "AGENT_REACT", "execute_react")


def build_guardrails_block_response() -> dict[str, list[AIMessage] | str]:
    """构造 guardrails 拒绝继续执行时的固定回复。"""
    return {
        "messages": [AIMessage(content=GUARDRAILS_BLOCK_MESSAGE)],
        "next_action": "end",
    }


def build_after_response_payload(
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    messages: list[Any],
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
