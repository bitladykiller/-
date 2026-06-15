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

from typing import Literal

from app.chat.infrastructure.graph.execution_utils import (
    ainvoke_structured_question_output,
)
from app.chat.infrastructure.graph.message_utils import (
    build_safe_messages,
    wrap_user_message,
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
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig


async def analyze_and_route_query(state: AgentState, *, config: RunnableConfig) -> dict:
    """分析用户输入，路由到通用回复或知识库检索。"""
    _ = config
    messages = build_safe_messages(ROUTER_SYSTEM_PROMPT, state.messages)
    response: Router = await router_model.with_structured_output(Router).ainvoke(
        messages
    )
    return {"router": response}


def route_query(
    state: AgentState,
) -> Literal["respond_to_general_query", "retrieval_plan_router"]:
    """根据路由结果选择下一个节点。"""
    if state.router["type"] == "general":
        return "respond_to_general_query"
    return "retrieval_plan_router"


async def respond_to_general_query(
    state: AgentState,
    *,
    config: RunnableConfig,
) -> dict[str, list[BaseMessage]]:
    """处理通用查询：闲聊、追问等。注入记忆上下文增强回复。"""
    system_prompt = GENERAL_QUERY_SYSTEM_PROMPT.format(logic=state.router["logic"])
    user_message = state.messages[-1].content
    memory_state = await load_memory_state(state, config, user_message)
    if memory_state is not None:
        memory_context = build_memory_context(
            memory_state.session_summary,
            memory_state.recent_messages,
            memory_state.long_term_memories,
            memory_state.user_profile,
        )
        if memory_context:
            system_prompt += memory_context
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
    question = state.messages[-1].content
    wrapped_question = wrap_user_message(question)
    guardrails_output = await ainvoke_structured_question_output(
        system_prompt=GUARDRAILS_SYSTEM_PROMPT,
        human_prompt=(
            "参考此范围描述来决策:\n"
            "个人电商经营范围：智能家居产品（智能照明/安防/控制/音箱/厨电/清洁）。\n"
            "不包含：服装、鞋类、体育用品、化妆品、食品等。\n"
            "Question: {question}"
        ),
        model=guardrails_model,
        output_schema=GuardrailsDecision,
        question=wrapped_question,
    )

    if guardrails_output.decision == "end":
        return {
            "messages": [AIMessage(content="抱歉，我家暂时没有这方面的商品，可以在别家看看哦～")],
            "next_action": "end",
        }
    return {"next_action": "continue"}


def guardrails_edge(
    state: AgentState,
) -> Literal["retrieval_plan_route", "after_response"]:
    """守卫后的路由：continue → 检索计划，end → 直接回复。"""
    if state.next_action == "end":
        return "after_response"
    return "retrieval_plan_route"


async def retrieval_plan_route(
    state: AgentState,
    *,
    config: RunnableConfig,
) -> dict:
    """根据问题特征选择最优检索策略。"""
    _ = config
    question = state.messages[-1].content
    wrapped_question = wrap_user_message(question)
    output = await ainvoke_structured_question_output(
        system_prompt=RETRIEVAL_PLAN_ROUTER_PROMPT,
        human_prompt="问题：{question}",
        model=retrieval_plan_model,
        output_schema=RetrievalPlanOutput,
        question=wrapped_question,
    )

    plan: RetrievalPlan = {"logic": output.logic, "plan": output.plan}
    return {"retrieval_plan": plan}


def retrieval_plan_edge(
    state: AgentState,
) -> Literal[
    "execute_graph_only",
    "execute_rag_only",
    "execute_parallel",
    "execute_then",
    "execute_react",
]:
    """根据检索计划路由到对应的执行节点。"""
    return {
        "GRAPH_ONLY": "execute_graph_only",
        "RAG_ONLY": "execute_rag_only",
        "PARALLEL": "execute_parallel",
        "GRAPH_THEN_RAG": "execute_then",
        "AGENT_REACT": "execute_react",
    }[state.retrieval_plan["plan"]]
