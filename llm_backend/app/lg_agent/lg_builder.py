"""
LangGraph Agent 图构建。
v3.7: 顶层 Router 2 分类，KG 子图 RetrievalPlan 5 路路由 + AgentReAct 兜底。
"""
from __future__ import annotations

import asyncio
from typing import cast, Literal, List, Dict, Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.messages import BaseMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_deepseek import ChatDeepSeek
from langchain_ollama import ChatOllama
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.core.config import settings, ServiceType
from app.lg_agent.lg_states import AgentState, InputState, Router, RetrievalPlan
from app.lg_agent.lg_prompts import (
    ROUTER_SYSTEM_PROMPT,
    GENERAL_QUERY_SYSTEM_PROMPT,
    GUARDRAILS_SYSTEM_PROMPT,
    RETRIEVAL_PLAN_ROUTER_PROMPT,
)
from app.lg_agent.kg_sub_graph.kg_neo4j_conn import get_neo4j_graph
from app.lg_agent.kg_sub_graph.agentic_rag_agents.retrievers.cypher_examples.northwind_retriever import (
    NorthwindCypherRetriever,
)
from app.lg_agent.kg_sub_graph.agentic_rag_agents.workflows.single_agent import (
    create_text2cypher_agent,
)
from app.lg_agent.kg_sub_graph.agentic_rag_agents.components.customer_tools import (
    create_rag_search_node,
)
from app.lg_agent.kg_sub_graph.agentic_rag_agents.components.summarize import (
    create_summarization_node,
)
from app.security import wrap_user_message
from app.memory.redis_short_term_memory import RedisShortTermMemory
from app.memory.simple_long_term_memory import SimpleLongTermMemory
from app.memory.memory_extractor import MemoryExtractor
from app.memory.memory_middleware import MemoryMiddleware
from app.memory.prompt_builder import build_memory_injection_prompt, build_summary_injection_prompt

SCOPE_DESCRIPTION = """
个人电商经营范围：智能家居产品（智能照明/安防/控制/音箱/厨电/清洁）。
不包含：服装、鞋类、体育用品、化妆品、食品等。
"""


# ------------------------------------------------------------------ #
# 模型工厂 + 温度分离
# ------------------------------------------------------------------ #

def _create_agent_model(temperature: float = 0.7):
    if settings.AGENT_SERVICE == ServiceType.DEEPSEEK:
        return ChatDeepSeek(api_key=settings.DEEPSEEK_API_KEY, model_name=settings.DEEPSEEK_MODEL, temperature=temperature)
    return ChatOllama(model=settings.OLLAMA_AGENT_MODEL, base_url=settings.OLLAMA_BASE_URL, temperature=temperature)


_agent_model = _create_agent_model(0.7)
_router_model = _create_agent_model(0.1)
_retrieval_plan_model = _create_agent_model(0.1)
_guardrails_model = _create_agent_model(0.1)
_cypher_model = _create_agent_model(0.2)


# ------------------------------------------------------------------ #
# 安全消息构建
# ------------------------------------------------------------------ #

def _build_safe_messages(system_prompt: str, messages: list) -> list:
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


# ------------------------------------------------------------------ #
# Memory Middleware 单例
# ------------------------------------------------------------------ #

_memory_middleware_instance: Optional[MemoryMiddleware] = None


def _get_memory_middleware() -> Optional[MemoryMiddleware]:
    global _memory_middleware_instance
    if _memory_middleware_instance is not None:
        return _memory_middleware_instance
    try:
        import redis.asyncio as redis
        from pymilvus import MilvusClient
        redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        redis_stm = RedisShortTermMemory(redis_client)
        if settings.EMBEDDING_TYPE == "ollama":
            from langchain_ollama import OllamaEmbeddings
            embedding_model = OllamaEmbeddings(model=settings.EMBEDDING_MODEL, base_url=settings.OLLAMA_BASE_URL)
        else:
            from langchain_community.embeddings import HuggingFaceEmbeddings
            embedding_model = HuggingFaceEmbeddings(model_name=settings.EMBEDDING_MODEL)
        milvus_client = MilvusClient(uri=settings.MILVUS_URL)
        milvus_ltm = SimpleLongTermMemory(milvus_client=milvus_client, embedding_model=embedding_model, collection_name=settings.MILVUS_COLLECTION_NAME)
        if settings.AGENT_SERVICE == ServiceType.DEEPSEEK:
            extractor_llm = ChatDeepSeek(api_key=settings.DEEPSEEK_API_KEY, model_name=settings.DEEPSEEK_MODEL, temperature=0.3)
        else:
            extractor_llm = ChatOllama(model=settings.OLLAMA_AGENT_MODEL, base_url=settings.OLLAMA_BASE_URL, temperature=0.3)
        memory_extractor = MemoryExtractor(llm_client=extractor_llm)
        _memory_middleware_instance = MemoryMiddleware(redis_stm=redis_stm, milvus_ltm=milvus_ltm, memory_extractor=memory_extractor)
        asyncio.ensure_future(_memory_middleware_instance.health_check())
        return _memory_middleware_instance
    except Exception:
        return None


def build_memory_context(session_summary, recent_messages, long_term_memories,
                         user_profile=None) -> str:
    parts = []
    if user_profile and isinstance(user_profile, dict):
        profile_lines = []
        if user_profile.get("preferred_brand"):
            profile_lines.append(f"偏好品牌: {user_profile['preferred_brand']}")
        if user_profile.get("budget_range"):
            profile_lines.append(f"预算范围: {user_profile['budget_range']}")
        if user_profile.get("preferred_category"):
            profile_lines.append(f"偏好品类: {user_profile['preferred_category']}")
        if user_profile.get("tags"):
            profile_lines.append(f"标签: {', '.join(user_profile['tags'])}")
        for fact in (user_profile.get("facts") or []):
            profile_lines.append(f"{fact.get('key','')}: {fact.get('value','')}")
        if profile_lines:
            parts.append("【用户画像】\n" + "\n".join(profile_lines))
    if recent_messages:
        messages_text = ""
        for msg in recent_messages:
            role = "用户" if msg.role == "user" else "助手"
            messages_text += f"[{role}]: {msg.content}\n"
        parts.append(f"【最近对话记录】\n{messages_text}")
    summary_text = build_summary_injection_prompt(session_summary)
    if summary_text:
        parts.append(summary_text)
    ltm_text = build_memory_injection_prompt(long_term_memories)
    if ltm_text:
        parts.append(ltm_text)
    if not parts:
        return ""
    return "\n\n" + "\n\n".join(parts)


# ================================================================== #
# 节点函数
# ================================================================== #

# ------------------------------------------------------------------ #
# 顶层 Router（2 分类）
# ------------------------------------------------------------------ #

async def analyze_and_route_query(state: AgentState, *, config: RunnableConfig) -> dict:
    messages = _build_safe_messages(ROUTER_SYSTEM_PROMPT, state.messages)
    response = cast(Router, await _router_model.with_structured_output(Router).ainvoke(messages))
    return {"router": response}


def route_query(state: AgentState) -> Literal["respond_to_general_query", "retrieval_plan_router"]:
    _type = state.router["type"]
    if _type == "general":
        return "respond_to_general_query"
    return "retrieval_plan_router"


# ------------------------------------------------------------------ #
# General 回复（闲聊 + 追问 + 图片上下文回复）
# ------------------------------------------------------------------ #

async def respond_to_general_query(state: AgentState, *, config: RunnableConfig) -> Dict[str, List[BaseMessage]]:
    system_prompt = GENERAL_QUERY_SYSTEM_PROMPT.format(logic=state.router["logic"])
    middleware = _get_memory_middleware()
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
            memory_context = build_memory_context(memory_state.session_summary, memory_state.recent_messages, memory_state.long_term_memories, memory_state.user_profile)
            if memory_context:
                system_prompt += memory_context
        except Exception:
            pass
    messages = _build_safe_messages(system_prompt, state.messages)
    response = await _agent_model.ainvoke(messages)
    return {"messages": [response]}


# ------------------------------------------------------------------ #
# Guardrails（KG 子图入口守卫）
# ------------------------------------------------------------------ #

async def guardrails_node(state: AgentState, *, config: RunnableConfig) -> Dict[str, List[BaseMessage] | str]:
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

    raw_question = state.messages[-1].content if state.messages else ""
    safe_question, _ = wrap_user_message(raw_question)
    guardrails_chain = full_system_prompt | _guardrails_model.with_structured_output(
        type("GOutput", (BaseModel,), {"decision": (Literal["continue", "end"], Field(description="continue or end"))})
    )
    guardrails_output = await guardrails_chain.ainvoke({"question": safe_question})

    if guardrails_output.decision == "end":
        return {"messages": [AIMessage(content="抱歉，我家暂时没有这方面的商品，可以在别家看看哦～")], "next_action": "end"}
    return {"next_action": "continue"}


def guardrails_edge(state: AgentState) -> Literal["retrieval_plan_route", "after_response"]:
    if hasattr(state, "next_action") and state.get("next_action") == "end":  # type: ignore[arg-type]
        return "after_response"
    return "retrieval_plan_route"


# ------------------------------------------------------------------ #
# RetrievalPlan Router（5 路检索计划）
# ------------------------------------------------------------------ #

class RetrievalPlanOutput(BaseModel):
    logic: str = Field(description="选择该计划的理由")
    plan: Literal["GRAPH_ONLY", "RAG_ONLY", "PARALLEL", "GRAPH_THEN_RAG", "AGENT_REACT"] = Field(
        description="最合适的检索策略"
    )


async def retrieval_plan_route(state: AgentState, *, config: RunnableConfig) -> dict:
    raw_question = state.messages[-1].content if state.messages else ""
    safe_question, _ = wrap_user_message(raw_question)
    plan_prompt = ChatPromptTemplate.from_messages([
        ("system", RETRIEVAL_PLAN_ROUTER_PROMPT),
        ("human", "问题：{question}"),
    ])
    chain = plan_prompt | _retrieval_plan_model.with_structured_output(RetrievalPlanOutput)
    output = await chain.ainvoke({"question": safe_question})

    plan: RetrievalPlan = {"logic": output.logic, "plan": output.plan}
    return {"retrieval_plan": plan}


def retrieval_plan_edge(state: AgentState) -> Literal[
    "execute_graph_only", "execute_rag_only", "execute_parallel",
    "execute_then", "execute_react"
]:
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
# 执行节点 — 5 个独立函数 + 共享辅助
# ================================================================== #

def _question(state: AgentState) -> str:
    return state.messages[-1].content if state.messages else ""

def _no_neo4j():
    return {"messages": [AIMessage(content="抱歉，知识库服务暂时不可用，请稍后重试。")]}

# 模块级单例：避免每个执行节点重复创建
_retriever = None
_t2c_cache: dict = {}  # keyed by graph id, since graph connects once
_summarize_node = None
_rag_node = None

def _get_retriever():
    global _retriever
    if _retriever is None:
        _retriever = NorthwindCypherRetriever()
    return _retriever

def _get_t2c(neo4j_graph):
    gid = id(neo4j_graph)
    if gid not in _t2c_cache:
        _t2c_cache[gid] = create_text2cypher_agent(
            llm=_cypher_model, graph=neo4j_graph,
            cypher_example_retriever=_get_retriever(),
        )
    return _t2c_cache[gid]

def _get_summarize():
    global _summarize_node
    if _summarize_node is None:
        _summarize_node = create_summarization_node(llm=_cypher_model)
    return _summarize_node

def _get_rag():
    global _rag_node
    if _rag_node is None:
        _rag_node = create_rag_search_node()
    return _rag_node

async def _summarize(question: str, records: list, fallback: str = "未查询到相关信息～") -> str:
    if not records:
        return fallback
    result = await _get_summarize().ainvoke({"question": question, "cyphers": [{"records": records}]})
    return result.get("summary", "") or fallback

def _safe_records(result: dict) -> list:
    """从 RAG 或 Text2Cypher 结果中提取 records，兼容 cyc/cyphers 两种格式。"""
    if "records" in result:
        return result.get("records", [])
    cyphers = result.get("cyphers", [])
    if cyphers:
        return cyphers[0].get("records", [])
    return []


async def execute_graph_only(state: AgentState, *, config: RunnableConfig) -> dict:
    neo4j_graph = get_neo4j_graph()
    if neo4j_graph is None: return _no_neo4j()

    result = await _get_t2c(neo4j_graph).ainvoke({"task": _question(state)})
    summary = await _summarize(_question(state), _safe_records(result), "未查询到相关信息，请确认后重新咨询～")
    return {"messages": [AIMessage(content="正在查询..."), AIMessage(content=summary)]}


async def execute_rag_only(state: AgentState, *, config: RunnableConfig) -> dict:
    result = await _get_rag()({"task": _question(state)})
    summary = await _summarize(_question(state), _safe_records(result), "未在文档中找到相关信息～")
    return {"messages": [AIMessage(content="正在检索文档..."), AIMessage(content=summary)]}


async def execute_parallel(state: AgentState, *, config: RunnableConfig) -> dict:
    neo4j_graph = get_neo4j_graph()
    if neo4j_graph is None: return _no_neo4j()

    import asyncio as _aio
    q = _question(state)
    neo4j_task = _aio.create_task(_get_t2c(neo4j_graph).ainvoke(
        {"task": q + "（仅查询结构化数据：价格、库存、订单等）"}))
    rag_task = _aio.create_task(_get_rag()(
        {"task": q + "（仅查询文档知识：售后政策、保修条款等）"}))
    neo_result, rag_result = await _aio.gather(neo4j_task, rag_task)

    all_records = _safe_records(neo_result) + _safe_records(rag_result)
    summary = await _summarize(q, all_records)
    return {"messages": [AIMessage(content="正在同时查询..."), AIMessage(content=summary)]}


async def execute_then(state: AgentState, *, config: RunnableConfig) -> dict:
    neo4j_graph = get_neo4j_graph()
    if neo4j_graph is None: return _no_neo4j()

    q = _question(state)
    neo_result = await _get_t2c(neo4j_graph).ainvoke({"task": q})
    neo_records = _safe_records(neo_result)

    rag_result = await _get_rag()({"task": f"已知信息：{neo_records}\n\n查询：{q}"})
    all_records = list(neo_records) + _safe_records(rag_result)
    summary = await _summarize(q, all_records)
    return {"messages": [AIMessage(content="正在先查数据库，再查文档..."), AIMessage(content=summary)]}


def _build_react_subgraph() -> CompiledStateGraph:
    neo4j_graph = get_neo4j_graph()
    t2c_agent = _get_t2c(neo4j_graph)

    @tool
    async def neo4j_query(task: str) -> str:
        r = await t2c_agent.ainvoke({"task": task})
        return str(r.get("records", []))

    @tool
    async def rag_search(query: str) -> str:
        r = await _get_rag()({"task": query})
        return str(r.get("records", []))

    tools = [neo4j_query, rag_search]
    tool_node = ToolNode(tools)
    llm_with_tools = _cypher_model.bind_tools(tools)

    def _should_continue(state: dict) -> Literal["tools", "__end__"]:
        messages = state.get("messages", [])
        if not messages: return "__end__"
        last = messages[-1]
        if hasattr(last, "tool_calls") and last.tool_calls: return "tools"
        return "__end__"

    async def _agent(state: dict) -> dict:
        response = await llm_with_tools.ainvoke(state["messages"])
        return {"messages": [response]}

    sg = StateGraph(dict)
    sg.add_node("agent", _agent)
    sg.add_node("tools", tool_node)
    sg.add_edge(START, "agent")
    sg.add_conditional_edges("agent", _should_continue, {"tools": "tools", "__end__": END})
    sg.add_edge("tools", "agent")
    return sg.compile()


_react_subgraph = None

def _get_react_subgraph() -> CompiledStateGraph:
    global _react_subgraph
    if _react_subgraph is None:
        _react_subgraph = _build_react_subgraph()
    return _react_subgraph


async def execute_react(state: AgentState, *, config: RunnableConfig) -> dict:
    """AgentReAct 兜底：LangGraph ToolNode 子图，bind_tools 自由探索，最多 3 轮。"""
    if get_neo4j_graph() is None: return _no_neo4j()
    react_round = getattr(state, "react_round", 0)
    if react_round >= 3:
        return {"messages": [AIMessage(content="亲～抱歉，这个问题可能需要人工客服协助～")]}

    q = _question(state)
    sg = _get_react_subgraph()
    result = await sg.ainvoke({
        "messages": [{"role": "system", "content": "你是电商客服 Agent。使用工具查询后回复用户。最多 3 轮工具调用。"},
                     {"role": "user", "content": q}]
    })
    answer = result["messages"][-1].content if result.get("messages") else "未能确定回答～"

    return {
        "messages": [AIMessage(content="正在综合分析..."), AIMessage(content=str(answer))],
        "react_round": react_round + 1,
    }


# after_response（记忆写入）
async def after_response(state: AgentState, *, config: RunnableConfig) -> dict:
    middleware = _get_memory_middleware()
    if middleware is None: return {}
    try:
        c = config.get("configurable", {})
        u_msg = state.messages[-2].content if len(state.messages) >= 2 else ""
        a_msg = state.messages[-1].content if state.messages else ""
        if u_msg and a_msg:
            await middleware.after_agent(
                tenant_id=c.get("tenant_id", "default"),
                user_id=c.get("user_id", "anonymous"),
                session_id=c.get("thread_id", "default"),
                user_message=u_msg, assistant_message=a_msg,
            )
    except Exception: pass
    return {}


# ================================================================== #
# 图构建 — 6 节点
# ================================================================== #

builder = StateGraph(AgentState, input=InputState)

builder.add_node(analyze_and_route_query)
builder.add_node(respond_to_general_query)
builder.add_node("guardrails_node", guardrails_node)
builder.add_node("retrieval_plan_route", retrieval_plan_route)
builder.add_node("execute_graph_only", execute_graph_only)
builder.add_node("execute_rag_only", execute_rag_only)
builder.add_node("execute_parallel", execute_parallel)
builder.add_node("execute_then", execute_then)
builder.add_node("execute_react", execute_react)
builder.add_node("after_response", after_response)

builder.add_edge(START, "analyze_and_route_query")
builder.add_conditional_edges("analyze_and_route_query", route_query, {
    "respond_to_general_query": "respond_to_general_query",
    "retrieval_plan_router": "guardrails_node",
})
builder.add_edge("respond_to_general_query", "after_response")
builder.add_conditional_edges("guardrails_node", guardrails_edge, {
    "retrieval_plan_route": "retrieval_plan_route",
    "after_response": "after_response",
})
builder.add_conditional_edges("retrieval_plan_route", retrieval_plan_edge, {
    "execute_graph_only": "execute_graph_only",
    "execute_rag_only": "execute_rag_only",
    "execute_parallel": "execute_parallel",
    "execute_then": "execute_then",
    "execute_react": "execute_react",
})

for n in ["execute_graph_only", "execute_rag_only", "execute_parallel", "execute_then", "execute_react"]:
    builder.add_edge(n, "after_response")
builder.add_edge("after_response", END)

graph = builder.compile()
