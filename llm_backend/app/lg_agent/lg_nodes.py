"""LangGraph 主节点实现。

这个模块只负责：
- 顶层路由、守卫、检索计划和简单执行器节点
- 把检索结果交给摘要层生成最终回答
- 在响应结束后把本轮问答写回记忆系统

这个模块不负责：
- 图结构组装
- 检索器注册与底层连接管理
- ReAct 兜底子图实现
"""
from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig
from langchain_core.messages import BaseMessage

from app.lg_agent.lg_states import AgentState, Router, RetrievalPlan
from app.lg_agent.lg_retrievers import (
    KG_RETRIEVER_NAME,
    RAG_RETRIEVER_NAME,
    get_retriever,
)
from app.lg_agent.lg_prompts import (
    ROUTER_SYSTEM_PROMPT,
    GENERAL_QUERY_SYSTEM_PROMPT,
    GUARDRAILS_SYSTEM_PROMPT,
    RETRIEVAL_PLAN_ROUTER_PROMPT,
)
from app.lg_agent.lg_models import (
    agent_model,
    router_model,
    retrieval_plan_model,
    guardrails_model,
    RetrievalPlanOutput,
    GuardrailsDecision,
)
from app.lg_agent.memory_bridge.context import (
    configurable_scope,
    enrich_question,
    get_memory_middleware,
    load_memory_state,
)
from app.lg_agent.lg_execution_utils import (
    build_graph_only_query,
    build_graph_then_rag_query,
    build_rag_only_query,
    records_from_result,
    merge_retriever_records,
    search_retriever,
    ainvoke_structured_question_output,
    summarize_and_build_response,
)
from app.lg_agent.lg_message_utils import (
    build_safe_messages,
    build_simple_message_response,
)
from app.lg_agent.memory_bridge.prompt import build_memory_context
from app.lg_agent.lg_node_support import (
    GeneralRouteName,
    GuardrailsEdgeName,
    RetrievalEdgeName,
    build_after_response_payload,
    build_guardrails_block_response,
    build_memory_augmented_system_prompt,
    build_wrapped_question,
    route_guardrails_action,
    route_query_type,
    route_retrieval_plan,
)
from app.lg_agent.utils import question_from_state, no_neo4j_response
from app.core.logger import get_logger

logger = get_logger(__name__)


# ================================================================== #
# 常量
# ================================================================== #

SCOPE_DESCRIPTION = """
个人电商经营范围：智能家居产品（智能照明/安防/控制/音箱/厨电/清洁）。
不包含：服装、鞋类、体育用品、化妆品、食品等。
"""


async def _build_general_query_system_prompt(
    state: AgentState,
    config: RunnableConfig,
) -> str:
    """构造 general 节点的系统提示词，并按需注入记忆上下文。"""
    system_prompt = GENERAL_QUERY_SYSTEM_PROMPT.format(logic=state.router["logic"])
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


async def _enrich_current_question(
    state: AgentState,
    config: RunnableConfig,
) -> str:
    """读取当前用户问题并注入记忆上下文。"""
    return await enrich_question(state, config, question_from_state(state))


# ================================================================== #
# 顶层 Router（2 分类：general / rag_doc-query）
# ================================================================== #

async def analyze_and_route_query(state: AgentState, *, config: RunnableConfig) -> dict:
    """分析用户输入，路由到通用回复或知识库检索。"""
    messages = build_safe_messages(ROUTER_SYSTEM_PROMPT, state.messages)
    response: Router = await router_model.with_structured_output(Router).ainvoke(
        messages
    )
    return {"router": response}


def route_query(state: AgentState) -> GeneralRouteName:
    """根据路由结果选择下一个节点。"""
    return route_query_type(state.router["type"])


# ================================================================== #
# General 回复（闲聊 + 追问）
# ================================================================== #

async def respond_to_general_query(
    state: AgentState,
    *,
    config: RunnableConfig,
) -> dict[str, list[BaseMessage]]:
    """处理通用查询：闲聊、追问等。注入记忆上下文增强回复。"""
    system_prompt = await _build_general_query_system_prompt(state, config)
    messages = build_safe_messages(system_prompt, state.messages)
    response = await agent_model.ainvoke(messages)
    return {"messages": [response]}


# ================================================================== #
# Guardrails（业务范围 + 安全检查）
# ================================================================== #

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


# ================================================================== #
# RetrievalPlan Router（5 路检索计划）
# ================================================================== #

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


# ================================================================== #
# 执行节点 — 4 个简单检索执行器（ReAct 在 lg_react.py）
# ================================================================== #

async def execute_graph_only(state: AgentState, *, config: RunnableConfig) -> dict:
    """仅查 Neo4j 图数据库（通过 Retriever 接口）。"""
    kg = await get_retriever(KG_RETRIEVER_NAME)
    if kg is None:
        return no_neo4j_response()

    q = await _enrich_current_question(state, config)
    result = await search_retriever(kg, q)
    return await summarize_and_build_response(
        q,
        records_from_result(result),
        progress_message="正在查询...",
        fallback="未查询到相关信息，请确认后重新咨询～",
    )


async def execute_rag_only(state: AgentState, *, config: RunnableConfig) -> dict:
    """仅查 RAG 文档知识库（通过 Retriever 接口）。"""
    rag = await get_retriever(RAG_RETRIEVER_NAME)
    if rag is None:
        return build_simple_message_response("文档检索服务暂不可用。")

    q = await _enrich_current_question(state, config)
    result = await search_retriever(rag, q)
    return await summarize_and_build_response(
        q,
        records_from_result(result),
        progress_message="正在检索文档...",
        fallback="未在文档中找到相关信息～",
    )


async def execute_parallel(state: AgentState, *, config: RunnableConfig) -> dict:
    """并行查 Neo4j + RAG（通过 Retriever 接口），合并结果后生成摘要。"""
    kg = await get_retriever(KG_RETRIEVER_NAME)
    if kg is None:
        return no_neo4j_response()
    rag = await get_retriever(RAG_RETRIEVER_NAME)

    q = await _enrich_current_question(state, config)
    neo_result, rag_result = await asyncio.gather(
        search_retriever(kg, build_graph_only_query(q)),
        search_retriever(rag, build_rag_only_query(q)),
    )

    all_records = merge_retriever_records(neo_result, rag_result)
    return await summarize_and_build_response(
        q,
        all_records,
        progress_message="正在同时查询...",
    )


async def execute_then(state: AgentState, *, config: RunnableConfig) -> dict:
    """先查 Neo4j 确定实体，再用结果查 RAG（通过 Retriever 接口）。"""
    kg = await get_retriever(KG_RETRIEVER_NAME)
    if kg is None:
        return no_neo4j_response()
    rag = await get_retriever(RAG_RETRIEVER_NAME)

    q = await _enrich_current_question(state, config)
    neo_result = await search_retriever(kg, q)
    neo_records = records_from_result(neo_result)

    # 用图查询结果增强 RAG 检索
    rag_result = await search_retriever(
        rag,
        build_graph_then_rag_query(q, neo_records),
    )
    all_records = merge_retriever_records(neo_result, rag_result)
    return await summarize_and_build_response(
        q,
        all_records,
        progress_message="正在先查数据库，再查文档...",
    )


# ================================================================== #
# after_response — 响应后记忆写入
# ================================================================== #

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
