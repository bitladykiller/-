"""
LangGraph Agent 节点函数。

v3.15: 从 lg_builder.py 拆分。
v3.16: 引入 Retriever 抽象接口，Agent 节点不再直接依赖 rag_doc_parser 或 Neo4j。

架构说明：
- 所有检索操作通过 Retriever 接口进行（依赖倒置原则）
- RetrieverRegistry 集中管理所有检索器实例（注册表模式）
- 执行节点通过 registry["kg"] / registry["rag"] 获取检索器
- ReAct 子图内部通过闭包捕获 t2c_agent/rag_node（性能优化，避免每次工具调用都查注册表）
"""
from __future__ import annotations

import asyncio
import json
from typing import cast, Literal, List, Dict

from langchain_core.runnables import RunnableConfig
from langchain_core.messages import BaseMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END
from langgraph.prebuilt import create_react_agent
from langgraph.graph.state import CompiledStateGraph
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.lg_agent.lg_states import AgentState, InputState, Router, RetrievalPlan
from app.lg_agent.lg_retrievers import (
    RetrieverRegistry,
    MilvusDocRetriever,
    KnowledgeGraphRetriever,
)
from app.lg_agent.lg_prompts import (
    ROUTER_SYSTEM_PROMPT,
    GENERAL_QUERY_SYSTEM_PROMPT,
    GUARDRAILS_SYSTEM_PROMPT,
    RETRIEVAL_PLAN_ROUTER_PROMPT,
    REACT_SYSTEM_PROMPT,
    REACT_ANSWER_CHECK_PROMPT,
)
from app.lg_agent.lg_models import (
    agent_model,
    router_model,
    retrieval_plan_model,
    guardrails_model,
    cypher_model,
    react_model,
    react_judge_model,
)
from app.lg_agent.lg_context import (
    _get_memory_middleware,
    build_memory_context,
    enrich_question,
)
from app.lg_agent.kg_sub_graph.kg_neo4j_conn import get_neo4j_graph
from app.lg_agent.kg_sub_graph.agentic_rag_agents.retrievers.cypher_examples.northwind_retriever import (
    NorthwindCypherRetriever,
)
from app.lg_agent.kg_sub_graph.agentic_rag_agents.workflows.single_agent import (
    create_text2cypher_agent,
)
from app.lg_agent.kg_sub_graph.agentic_rag_agents.components.summarize import (
    create_summarization_node,
)
from app.lg_agent.kg_sub_graph.agentic_rag_agents.components.predefined_cypher.cypher_dict import (
    predefined_cypher_dict,
)
from app.lg_agent.kg_sub_graph.agentic_rag_agents.components.predefined_cypher.descriptions import (
    QUERY_DESCRIPTIONS,
)
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
# 检索器注册表 — 依赖倒置，Agent 不直接依赖具体检索实现
# ================================================================== #
#
# v3.16: 引入 Retriever 抽象，取代原来的 _get_t2c / _get_rag 直接调用。
# Agent 只通过 registry["kg"] / registry["rag"] 使用检索器，
# 不知道底层是 Neo4j、Milvus、Elasticsearch 还是别的什么。
#
# 保留的内部单例（供 ReAct 子图内部使用，性能优化）：
#   _t2c_agent — Text2Cypher compiled graph
#   _summarize_node — 摘要生成节点
# ================================================================== #

_registry: RetrieverRegistry = RetrieverRegistry()
_registry_lock: asyncio.Lock = asyncio.Lock()
_retriever = None
_t2c_agent = None  # 供 ReAct 子图内部直接使用（性能优化）
_summarize_node = None


async def _ensure_registry():
    """懒初始化检索器注册表。首次调用时创建所有 Retriever 实例。"""
    if "kg" in _registry and "rag" in _registry:
        return

    async with _registry_lock:
        if "kg" in _registry and "rag" in _registry:
            return

        neo4j_graph = get_neo4j_graph()
        if neo4j_graph is not None:
            # 初始化 Text2Cypher Agent
            global _t2c_agent
            if _t2c_agent is None:
                global _retriever
                if _retriever is None:
                    _retriever = NorthwindCypherRetriever()
                _t2c_agent = create_text2cypher_agent(
                    llm=cypher_model,
                    graph=neo4j_graph,
                    cypher_example_retriever=_retriever,
                    predefined_cypher_dict=predefined_cypher_dict,
                    query_descriptions=QUERY_DESCRIPTIONS,
                )
            _registry.register("kg", KnowledgeGraphRetriever(_t2c_agent))

        _registry.register("rag", MilvusDocRetriever())


async def _reg(name: str) -> "KnowledgeGraphRetriever | MilvusDocRetriever | None":
    """获取检索器。确保 registry 已初始化。"""
    await _ensure_registry()
    return _registry.get(name)


async def _summarize(question: str, records: list, fallback: str = "未查询到相关信息～") -> str:
    """对查询结果生成摘要。records 为空时返回 fallback。"""
    if not records:
        return fallback
    global _summarize_node
    if _summarize_node is None:
        _summarize_node = create_summarization_node(llm=cypher_model)
    result = await _summarize_node.ainvoke({
        "question": question,
        "cyphers": [{"records": records}],
    })
    return result.get("summary", "") or fallback


# ================================================================== #
# 顶层 Router（2 分类：general / rag_doc-query）
# ================================================================== #

async def analyze_and_route_query(state: AgentState, *, config: RunnableConfig) -> dict:
    """分析用户输入，路由到通用回复或知识库检索。"""
    messages = _build_safe_messages(ROUTER_SYSTEM_PROMPT, state.messages)
    response = cast(Router, await router_model.with_structured_output(Router).ainvoke(messages))
    return {"router": response}


def route_query(state: AgentState) -> Literal["respond_to_general_query", "retrieval_plan_router"]:
    """根据路由结果选择下一个节点。"""
    _type = state.router["type"]
    if _type == "general":
        return "respond_to_general_query"
    return "retrieval_plan_router"


# ================================================================== #
# General 回复（闲聊 + 追问 + 图片上下文回复）
# ================================================================== #

async def respond_to_general_query(
    state: AgentState, *, config: RunnableConfig,
) -> Dict[str, List[BaseMessage]]:
    """处理通用查询：闲聊、追问、图片上下文等。注入记忆上下文增强回复。"""
    system_prompt = GENERAL_QUERY_SYSTEM_PROMPT.format(logic=state.router["logic"])

    # 注入记忆上下文
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
            memory_context = build_memory_context(
                memory_state.session_summary,
                memory_state.recent_messages,
                memory_state.long_term_memories,
                memory_state.user_profile,
            )
            if memory_context:
                system_prompt += memory_context
        except Exception:
            pass  # 记忆注入失败不影响回复

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
    neo4j_graph = None
    try:
        neo4j_graph = get_neo4j_graph()
    except Exception:
        pass

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

class RetrievalPlanOutput(BaseModel):
    """检索计划输出结构。"""
    logic: str = Field(description="选择该计划的理由")
    plan: Literal["GRAPH_ONLY", "RAG_ONLY", "PARALLEL", "GRAPH_THEN_RAG", "AGENT_REACT"] = Field(
        description="最合适的检索策略"
    )


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


class ReactAnswerCheckOutput(BaseModel):
    """ReAct 答案校验输出结构。"""
    decision: Literal["sufficient", "retry", "handoff"] = Field(
        description="当前答案是否足够，或需要继续检索/转人工"
    )
    reason: str = Field(description="做出该判断的原因，供下一轮 ReAct 参考")


# ================================================================== #
# 执行节点 — 5 个独立的检索执行器
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
# ReAct Agent — 兜底策略，最多 5 轮完整尝试
# ================================================================== #

async def _build_react_subgraph() -> CompiledStateGraph:
    """构建 ReAct 子图：两个工具（neo4j_query + rag_search）。

    通过 Retriever 接口而非直接调用底层实现（依赖倒置）。
    """
    # 确保 registry 已初始化
    await _ensure_registry()
    kg = _registry.get("kg")

    # ReAct 子图内部使用闭包捕获检索器（性能优化，避免每次 tool call 都查注册表）
    @tool
    async def neo4j_query(task: str) -> str:
        """查询 Neo4j 知识图谱，获取商品、订单、客户等结构化数据。"""
        if kg is None:
            return json.dumps({"error": "知识图谱服务不可用"}, ensure_ascii=False)
        r = await kg.search(task)
        return json.dumps(_safe_records(r), ensure_ascii=False)

    @tool
    async def rag_search(query: str) -> str:
        """检索文档知识库，获取售后政策、保修条款等非结构化信息。"""
        rag = _registry.get("rag")
        if rag is None:
            return json.dumps({"error": "文档检索服务不可用"}, ensure_ascii=False)
        r = await rag.search(query)
        return json.dumps(_safe_records(r), ensure_ascii=False)

    tools = [neo4j_query, rag_search]
    return create_react_agent(
        model=react_model,
        tools=tools,
        prompt=REACT_SYSTEM_PROMPT,
        version="v2",
        name="customer_service_react_agent",
    )


async def _get_react_subgraph() -> CompiledStateGraph:
    """获取 ReAct 子图单例（加锁防并发创建）。"""
    global _react_subgraph
    if _react_subgraph is None:
        async with _react_lock:
            if _react_subgraph is None:
                _react_subgraph = await _build_react_subgraph()
    return _react_subgraph


async def execute_react(state: AgentState, *, config: RunnableConfig) -> dict:
    """ReAct 兜底执行 + 答案充分性检查，最多 5 轮。"""
    if get_neo4j_graph() is None:
        return _no_neo4j()

    q = await enrich_question(state, config, _question(state))
    sg = await _get_react_subgraph()
    subgraph_config = dict(config) if config else {}
    # 单次 ReAct 子图的最大 agent/tools 步数
    subgraph_config["recursion_limit"] = 11
    react_messages: list[dict[str, str]] = [{"role": "user", "content": q}]
    insufficiency_reason = "初始状态：尚未完成充分回答。"

    for attempt in range(1, 6):
        if attempt > 1:
            react_messages.append({
                "role": "user",
                "content": (
                    "上一次候选答案仍然不充分，请继续按标准 ReAct 检索并补足关键事实。"
                    f"不足原因：{insufficiency_reason}"
                ),
            })

        result = await sg.ainvoke({"messages": react_messages}, config=subgraph_config)
        result_messages = result.get("messages", [])
        last_answer = result_messages[-1].content if result_messages else "未能确定回答～"

        if isinstance(last_answer, str) and "need more steps" in last_answer.lower():
            insufficiency_reason = "单次 ReAct 内部步数耗尽，仍未得到足够答案。"
        else:
            # 构建 ReAct 过程记录供裁判评估
            transcript_lines: list[str] = []
            for msg in result_messages:
                role = getattr(msg, "type", None) or getattr(msg, "role", "assistant")
                content = getattr(msg, "content", "")
                if content:
                    transcript_lines.append(f"[{role}] {content}")
            transcript = "\n".join(transcript_lines[-20:])

            check_messages = [
                {"role": "system", "content": REACT_ANSWER_CHECK_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"用户问题：{q}\n\n"
                        f"ReAct 过程记录：\n{transcript}\n\n"
                        f"当前候选答案：{last_answer}"
                    ),
                },
            ]
            check = cast(
                ReactAnswerCheckOutput,
                await react_judge_model.with_structured_output(ReactAnswerCheckOutput).ainvoke(check_messages),
            )

            if check.decision == "sufficient":
                return {
                    "messages": [AIMessage(content="正在综合分析..."), AIMessage(content=str(last_answer))],
                }

            insufficiency_reason = check.reason or "答案信息不足。"

        # 准备下一轮：保留原始问题 + 上一轮候选答案
        react_messages = [
            {"role": "user", "content": q},
            {"role": "assistant", "content": str(last_answer)},
        ]

    # 5 轮用尽仍未充分
    return {
        "messages": [
            AIMessage(content="正在综合分析..."),
            AIMessage(content="亲～这个问题回答不了哦～"),
        ],
    }


# ================================================================== #
# after_response — 响应后记忆写入
# ================================================================== #

async def after_response(state: AgentState, *, config: RunnableConfig) -> dict:
    """将本轮对话写入 Redis STM，并触发 LTM 抽取。"""
    middleware = await _get_memory_middleware()
    if middleware is None:
        return {}
    try:
        c = config.get("configurable", {})
        u_msg = state.messages[-2].content if len(state.messages) >= 2 else ""
        a_msg = state.messages[-1].content if state.messages else ""
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
