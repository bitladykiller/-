"""主图中的决策类节点实现。

这个模块负责：
- 顶层路由节点
- general 回复节点
- guardrails 节点
- retrieval plan 路由节点

这个模块不负责：
- KG / RAG 检索执行
- after_response 写回记忆
- 主图结构组装
"""

from __future__ import annotations

from typing import Literal

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig

from app.chat.domain.utils import question_from_state
from app.chat.infrastructure.graph.execution_utils import (
    ainvoke_structured_question_output,
)
from app.chat.infrastructure.graph.message_utils import (
    build_safe_messages,
)
from app.chat.infrastructure.graph.state import AgentState, RetrievalPlan, Router
from app.chat.infrastructure.memory_bridge.context import (
    build_memory_context,
    load_memory_state,
)
from app.chat.infrastructure.modeling.models import (
    GuardrailsDecision,
    RetrievalPlanOutput,
    agent_model,
    guardrails_model,
    retrieval_plan_model,
    router_model,
)
from app.chat.infrastructure.modeling.prompts import (
    GENERAL_QUERY_SYSTEM_PROMPT,
    GUARDRAILS_SYSTEM_PROMPT,
    RETRIEVAL_PLAN_ROUTER_PROMPT,
    ROUTER_SYSTEM_PROMPT,
)
from app.shared.security import wrap_user_message

GeneralRouteName = Literal["respond_to_general_query", "retrieval_plan_router"]
GuardrailsEdgeName = Literal["retrieval_plan_route", "after_response"]
RetrievalEdgeName = Literal[
    "execute_graph_only",
    "execute_rag_only",
    "execute_parallel",
    "execute_then",
    "execute_react",
]
_GUARDRAILS_BLOCK_MESSAGE = "抱歉，我家暂时没有这方面的商品，可以在别家看看哦～"
_RETRIEVAL_EDGE_MAP: dict[str, RetrievalEdgeName] = {
    "GRAPH_ONLY": "execute_graph_only",
    "RAG_ONLY": "execute_rag_only",
    "PARALLEL": "execute_parallel",
    "GRAPH_THEN_RAG": "execute_then",
    "AGENT_REACT": "execute_react",
}

SCOPE_DESCRIPTION = """
个人电商经营范围：智能家居产品（智能照明/安防/控制/音箱/厨电/清洁）。
不包含：服装、鞋类、体育用品、化妆品、食品等。
"""


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


def build_guardrails_block_response() -> dict[str, list[BaseMessage] | str]:
    """构造 guardrails 拒绝继续执行时的固定回复。"""
    return {
        "messages": [AIMessage(content=_GUARDRAILS_BLOCK_MESSAGE)],
        "next_action": "end",
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


async def build_general_query_system_prompt(
    *,
    state: AgentState,
    config: RunnableConfig,
    general_query_system_prompt: str,
) -> str:
    """构造 general 节点的系统提示词，并按需注入记忆上下文。"""
    system_prompt = general_query_system_prompt.format(logic=state.router["logic"])
    user_message = question_from_state(state)
    memory_state = await load_memory_state(state, config, user_message)
    if memory_state is None:
        return system_prompt

    memory_context = build_memory_context(
        memory_state.session_summary,
        memory_state.recent_messages,
        memory_state.long_term_memories,
        memory_state.user_profile,
    )
    return build_memory_augmented_system_prompt(
        system_prompt=system_prompt,
        memory_context=memory_context,
    )


async def analyze_and_route_query(state: AgentState, *, config: RunnableConfig) -> dict:
    """分析用户输入，路由到通用回复或知识库检索。"""
    _ = config
    messages = build_safe_messages(ROUTER_SYSTEM_PROMPT, state.messages)
    response: Router = await router_model.with_structured_output(Router).ainvoke(
        messages
    )
    return {"router": response}


def route_query(state: AgentState) -> GeneralRouteName:
    """根据路由结果选择下一个节点。"""
    return route_query_type(state.router["type"])


async def respond_to_general_query(
    state: AgentState,
    *,
    config: RunnableConfig,
) -> dict[str, list[BaseMessage]]:
    """处理通用查询：闲聊、追问等。注入记忆上下文增强回复。"""
    system_prompt = await build_general_query_system_prompt(
        state=state,
        config=config,
        general_query_system_prompt=GENERAL_QUERY_SYSTEM_PROMPT,
    )
    messages = build_safe_messages(system_prompt, state.messages)
    response = await agent_model.ainvoke(messages)
    return {"messages": [response]}


async def guardrails_node(
    state: AgentState,
    *,
    config: RunnableConfig,
) -> dict[str, list[BaseMessage] | str]:
    """守卫节点：检查问题是否在业务范围内，拦截恶意输入。"""
    _ = config
    guardrails_output = await ainvoke_structured_question_output(
        system_prompt=GUARDRAILS_SYSTEM_PROMPT,
        human_prompt=f"参考此范围描述来决策:\n{SCOPE_DESCRIPTION}\nQuestion: {{question}}",
        model=guardrails_model,
        output_schema=GuardrailsDecision,
        question=build_wrapped_question(question_from_state(state)),
    )

    if guardrails_output.decision == "end":
        return build_guardrails_block_response()
    return {"next_action": "continue"}


def guardrails_edge(state: AgentState) -> GuardrailsEdgeName:
    """守卫后的路由：continue → 检索计划，end → 直接回复。"""
    return route_guardrails_action(state.next_action)


async def retrieval_plan_route(
    state: AgentState,
    *,
    config: RunnableConfig,
) -> dict:
    """根据问题特征选择最优检索策略。"""
    _ = config
    output = await ainvoke_structured_question_output(
        system_prompt=RETRIEVAL_PLAN_ROUTER_PROMPT,
        human_prompt="问题：{question}",
        model=retrieval_plan_model,
        output_schema=RetrievalPlanOutput,
        question=build_wrapped_question(question_from_state(state)),
    )

    plan: RetrievalPlan = {"logic": output.logic, "plan": output.plan}
    return {"retrieval_plan": plan}


def retrieval_plan_edge(state: AgentState) -> RetrievalEdgeName:
    """根据检索计划路由到对应的执行节点。"""
    return route_retrieval_plan((state.retrieval_plan or {}).get("plan"))


__all__ = [
    "analyze_and_route_query",
    "build_guardrails_block_response",
    "build_general_query_system_prompt",
    "build_memory_augmented_system_prompt",
    "build_wrapped_question",
    "guardrails_edge",
    "guardrails_node",
    "route_guardrails_action",
    "route_query_type",
    "route_retrieval_plan",
    "respond_to_general_query",
    "retrieval_plan_edge",
    "retrieval_plan_route",
    "route_query",
]
