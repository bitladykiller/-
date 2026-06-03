"""
LangGraph Agent 节点函数。

v3.15: 从 lg_builder.py 拆分。
v3.16: 引入 Retriever 抽象接口，Agent 节点不再直接依赖 rag_doc_parser 或 Neo4j。
v3.17: 检索器管理 → lg_retrievers.py，模型类 → lg_models.py，ReAct → lg_react.py。
       本文件只保留：Router / Guardrails / RetrievalPlan / 4 个简单执行器 / after_response。

架构说明：
- 所有检索操作通过 Retriever 接口进行（依赖倒置原则）
- 检索器单例在 lg_retrievers.py 中管理（Registry 模式）
- ReAct 子图在 lg_react.py 中独立管理
"""
from __future__ import annotations

import asyncio
from typing import Literal, List, Dict

from langchain_core.runnables import RunnableConfig
from langchain_core.messages import BaseMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from app.lg_agent.lg_states import AgentState, Router, RetrievalPlan
from app.lg_agent.lg_retrievers import (
    _reg,
    _summarize,
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
)
from app.lg_agent.lg_context import (
    _get_memory_middleware,
    build_memory_context,
    enrich_question,
)
from app.lg_agent.kg_sub_graph.kg_neo4j_conn import get_neo4j_graph
from app.security import wrap_user_message


# ================================================================== #
# 常量
# ================================================================== #

SCOPE_DESCRIPTION = """
个人电商经营范围：智能家居产品（智能照明/安防/控制/音箱/厨电/清洁）。
不包含：服装、鞋类、体育用品、化妆品、食品等。
"""


# ================================================================== #
# 工具函数 — 节点间共享的辅助逻辑
# ================================================================== #

def _build_safe_messages(system_prompt: str, messages: list) -> list:
    """构建安全消息列表，对 user 消息做 XML 隔离防注入。"""
    safe = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        role = msg.get("role", msg.type if hasattr(msg, "type") else "user")
        content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
        if role == "user":
            wrapped, _ = wrap_user_message(content)
            safe.append({"role": "user", "content": wrapped})
        else:
            safe.append({"role": role, "content": content})
    return safe


def _question(state: AgentState) -> str:
    """从 state 中提取用户最新消息。"""
    return state.messages[-1].content if state.messages else ""


def _no_neo4j() -> dict:
    """Neo4j 不可用时的统一降级响应。"""
    return {"messages": [AIMessage(content="抱歉，知识库服务暂时不可用，请稍后重试。")]}


def _safe_records(result: dict) -> list:
    """从 RAG 或 Text2Cypher 结果中提取 records，兼容 records/cyphers 两种格式。"""
    if "records" in result:
        return result.get("records", [])
    cyphers = result.get("cyphers", [])
    if cyphers:
        return cyphers[0].get("records", [])
    return []


# ================================================================== #
# 顶层 Router（2 分类：general / rag_doc-query）
# ================================================================== #

async def analyze_and_route_query(state: AgentState, *, config: RunnableConfig) -> dict:
    """分析用户输入，路由到通用回复或知识库检索。"""
    messages = _build_safe_messages(ROUTER_SYSTEM_PROMPT, state.messages)
    response: Router = await router_model.with_structured_output(Router).ainvoke(messages)
    return {"router": response}


def route_query(state: AgentState) -> Literal["respond_to_general_query", "retrieval_plan_router"]:
    """根据路由结果选择下一个节点。"""
    _type = state.router["type"]
    if _type == "general":
        return "respond_to_general_query"
    return "retrieval_plan_router"


# ================================================================== #
# General 回复（闲聊 + 追问）
# ================================================================== #

async def respond_to_general_query(
    state: AgentState, *, config: RunnableConfig,
) -> Dict[str, List[BaseMessage]]:
    """处理通用查询：闲聊、追问等。注入记忆上下文增强回复。"""
    system_prompt = GENERAL_QUERY_SYSTEM_PROMPT.format(logic=state.router["logic"])

    # 注入记忆上下文（缓存到 state.memory_state 供后续节点复用）
    if state.memory_state is None:
        middleware = await _get_memory_middleware()
        if middleware is not None:
            try:
                configurable = config.get("configurable", {})
                user_message = state.messages[-1].content if state.messages else ""
                memory_state = await middleware.before_agent(
                    tenant_id=configurable.get("tenant_id", "default"),
                    user_id=configurable.get("user_id", "anonymous"),
                    session_id=configurable.get("thread_id", "default"),
                    user_input=user_message,
                )
                state.memory_state = memory_state  # 缓存
            except Exception:
                pass  # 记忆注入失败不影响回复

    if state.memory_state is not None:
        memory_context = build_memory_context(
            state.memory_state.session_summary,
            state.memory_state.recent_messages,
            state.memory_state.long_term_memories,
            state.memory_state.user_profile,
        )
        if memory_context:
            system_prompt += memory_context

    messages = _build_safe_messages(system_prompt, state.messages)
    response = await agent_model.ainvoke(messages)
    return {"messages": [response]}


# ================================================================== #
# Guardrails（业务范围 + 安全检查）
# ================================================================== #

async def guardrails_node(
    state: AgentState, *, config: RunnableConfig,
) -> Dict[str, List[BaseMessage] | str]:
    """守卫节点：检查问题是否在业务范围内，拦截恶意输入。"""
    scope_context = f"参考此范围描述来决策:\n{SCOPE_DESCRIPTION}"
    message = scope_context + "\nQuestion: {question}"
    full_system_prompt = ChatPromptTemplate.from_messages([
        ("system", GUARDRAILS_SYSTEM_PROMPT),
        ("human", message),
    ])

    raw_question = _question(state)
    safe_question, _ = wrap_user_message(raw_question)

    guardrails_chain = full_system_prompt | guardrails_model.with_structured_output(
        type("GOutput", (BaseModel,), {
            "decision": (Literal["continue", "end"], Field(description="continue or end")),
        })
    )
    guardrails_output = await guardrails_chain.ainvoke({"question": safe_question})

    if guardrails_output.decision == "end":
        return {
            "messages": [AIMessage(content="抱歉，我家暂时没有这方面的商品，可以在别家看看哦～")],
            "next_action": "end",
        }
    return {"next_action": "continue"}


def guardrails_edge(state: AgentState) -> Literal["retrieval_plan_route", "after_response"]:
    """守卫后的路由：continue → 检索计划，end → 直接回复。"""
    if state.next_action == "end":
        return "after_response"
    return "retrieval_plan_route"


# ================================================================== #
# RetrievalPlan Router（5 路检索计划）
# ================================================================== #

async def retrieval_plan_route(state: AgentState, *, config: RunnableConfig) -> dict:
    """根据问题特征选择最优检索策略。"""
    raw_question = _question(state)
    safe_question, _ = wrap_user_message(raw_question)
    plan_prompt = ChatPromptTemplate.from_messages([
        ("system", RETRIEVAL_PLAN_ROUTER_PROMPT),
        ("human", "问题：{question}"),
    ])
    chain = plan_prompt | retrieval_plan_model.with_structured_output(RetrievalPlanOutput)
    output = await chain.ainvoke({"question": safe_question})

    plan: RetrievalPlan = {"logic": output.logic, "plan": output.plan}
    return {"retrieval_plan": plan}


def retrieval_plan_edge(state: AgentState) -> Literal[
    "execute_graph_only", "execute_rag_only", "execute_parallel",
    "execute_then", "execute_react",
]:
    """根据检索计划路由到对应的执行节点。"""
    plan = (state.retrieval_plan or {}).get("plan", "AGENT_REACT")
    mapping = {
        "GRAPH_ONLY": "execute_graph_only",
        "RAG_ONLY": "execute_rag_only",
        "PARALLEL": "execute_parallel",
        "GRAPH_THEN_RAG": "execute_then",
        "AGENT_REACT": "execute_react",
    }
    return mapping.get(plan, "execute_react")  # type: ignore[return-value]


# ================================================================== #
# 执行节点 — 4 个简单检索执行器（ReAct 在 lg_react.py）
# ================================================================== #

async def execute_graph_only(state: AgentState, *, config: RunnableConfig) -> dict:
    """仅查 Neo4j 图数据库（通过 Retriever 接口）。"""
    kg = await _reg("kg")
    if kg is None:
        return _no_neo4j()

    q = await enrich_question(state, config, _question(state))
    result = await kg.search(q)
    summary = await _summarize(q, _safe_records(result), "未查询到相关信息，请确认后重新咨询～")
    return {"messages": [AIMessage(content="正在查询..."), AIMessage(content=summary)]}


async def execute_rag_only(state: AgentState, *, config: RunnableConfig) -> dict:
    """仅查 RAG 文档知识库（通过 Retriever 接口）。"""
    rag = await _reg("rag")
    if rag is None:
        return {"messages": [AIMessage(content="文档检索服务暂不可用。")]}

    q = await enrich_question(state, config, _question(state))
    result = await rag.search(q)
    summary = await _summarize(q, _safe_records(result), "未在文档中找到相关信息～")
    return {"messages": [AIMessage(content="正在检索文档..."), AIMessage(content=summary)]}


async def execute_parallel(state: AgentState, *, config: RunnableConfig) -> dict:
    """并行查 Neo4j + RAG（通过 Retriever 接口），合并结果后生成摘要。"""
    kg = await _reg("kg")
    if kg is None:
        return _no_neo4j()
    rag = await _reg("rag")

    q = await enrich_question(state, config, _question(state))
    neo4j_task = asyncio.create_task(kg.search(q + "（仅查询结构化数据：价格、库存、订单等）"))
    rag_task = asyncio.create_task(rag.search(q + "（仅查询文档知识：售后政策、保修条款等）")) if rag else None

    neo_result = await neo4j_task
    rag_result = await rag_task if rag_task else {"cyphers": [{"records": {}}]}

    all_records = _safe_records(neo_result) + _safe_records(rag_result)
    summary = await _summarize(q, all_records)
    return {"messages": [AIMessage(content="正在同时查询..."), AIMessage(content=summary)]}


async def execute_then(state: AgentState, *, config: RunnableConfig) -> dict:
    """先查 Neo4j 确定实体，再用结果查 RAG（通过 Retriever 接口）。"""
    kg = await _reg("kg")
    if kg is None:
        return _no_neo4j()
    rag = await _reg("rag")

    q = await enrich_question(state, config, _question(state))
    neo_result = await kg.search(q)
    neo_records = _safe_records(neo_result)

    # 用图查询结果增强 RAG 检索
    rag_result = await rag.search(f"已知信息：{neo_records}\n\n查询：{q}") if rag else {"cyphers": [{"records": {}}]}
    all_records = list(neo_records) + _safe_records(rag_result)
    summary = await _summarize(q, all_records)
    return {"messages": [AIMessage(content="正在先查数据库，再查文档..."), AIMessage(content=summary)]}


# ================================================================== #
# after_response — 响应后记忆写入
# ================================================================== #

def _find_last_user_message(messages: list) -> str:
    """从消息列表中反向查找最后一条用户消息。

    v3.17 修复：原实现硬编码 messages[-2] 为用户、messages[-1] 为助手，
    但当执行节点返回多条 AIMessage 时（如 execute_graph_only 返回"正在查询..."+
    摘要），messages[-2] 可能是 AIMessage 而非用户消息，导致记忆写入错乱。
    改为遍历查找真正的 role="user" 消息。
    """
    for msg in reversed(messages):
        role = getattr(msg, "type", None) or getattr(msg, "role", None) or ""
        if role == "human" or role == "user":
            return getattr(msg, "content", "") or ""
    return ""


def _find_last_assistant_message(messages: list) -> str:
    """从消息列表中反向查找最后一条包含有意义内容的助手消息。

    跳过"正在查询..."等进度提示消息。
    """
    for msg in reversed(messages):
        role = getattr(msg, "type", None) or getattr(msg, "role", None) or ""
        if role == "ai" or role == "assistant":
            content = getattr(msg, "content", "") or ""
            # 跳过进度提示占位符
            if content and "正在" not in content:
                return content
    # 如果所有助手消息都是进度提示，返回最后一条助手消息
    for msg in reversed(messages):
        role = getattr(msg, "type", None) or getattr(msg, "role", None) or ""
        if role == "ai" or role == "assistant":
            return getattr(msg, "content", "") or ""
    return ""


async def after_response(state: AgentState, *, config: RunnableConfig) -> dict:
    """将本轮对话写入 Redis STM，并触发 LTM 抽取。"""
    middleware = await _get_memory_middleware()
    if middleware is None:
        return {}
    try:
        c = config.get("configurable", {})
        u_msg = _find_last_user_message(state.messages)
        a_msg = _find_last_assistant_message(state.messages)
        if u_msg and a_msg:
            await middleware.after_agent(
                tenant_id=c.get("tenant_id", "default"),
                user_id=c.get("user_id", "anonymous"),
                session_id=c.get("thread_id", "default"),
                user_message=u_msg,
                assistant_message=a_msg,
            )
    except Exception:
        pass  # 记忆写入失败不影响响应
    return {}
