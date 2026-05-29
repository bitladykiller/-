"""
LangGraph Agent 图构建。
v3.7: 顶层 Router 2 分类，KG 子图 RetrievalPlan 5 路路由 + AgentReAct 兜底。
"""
from __future__ import annotations

import asyncio
import sys
from typing import cast, Literal, List, Dict, Any, Optional

from langchain_core.runnables import RunnableConfig
from langchain_core.messages import BaseMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_deepseek import ChatDeepSeek
from langchain_ollama import ChatOllama
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.core.config import settings, ServiceType
from app.lg_agent.lg_states import AgentState, InputState, Router, RetrievalPlan
from app.lg_agent.lg_prompts import (
    ROUTER_SYSTEM_PROMPT,
    GENERAL_QUERY_SYSTEM_PROMPT,
    GUARDRAILS_SYSTEM_PROMPT,
    RETRIEVAL_PLAN_ROUTER_PROMPT,
    RAGSEARCH_SYSTEM_PROMPT,
)
from app.lg_agent.kg_sub_graph.kg_neo4j_conn import get_neo4j_graph
from app.lg_agent.kg_sub_graph.agentic_rag_agents.retrievers.cypher_examples.northwind_retriever import (
    NorthwindCypherRetriever,
)
from app.lg_agent.kg_sub_graph.agentic_rag_agents.workflows.multi_agent.multi_tool import (
    create_multi_tool_workflow,
)
from app.lg_agent.kg_sub_graph.agentic_rag_agents.components.predefined_cypher.cypher_dict import (
    predefined_cypher_dict,
)
from app.lg_agent.kg_sub_graph.kg_tools_list import cypher_query, predefined_cypher, rag_document_query
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
    "execute_graph_then_rag", "agent_react_node"
]:
    plan = (state.retrieval_plan or {}).get("plan", "AGENT_REACT")
    mapping = {
        "GRAPH_ONLY": "execute_graph_only",
        "RAG_ONLY": "execute_rag_only",
        "PARALLEL": "execute_parallel",
        "GRAPH_THEN_RAG": "execute_graph_then_rag",
        "AGENT_REACT": "agent_react_node",
    }
    return mapping.get(plan, "agent_react_node")  # type: ignore[return-value]


# ------------------------------------------------------------------ #
# 执行节点：GRAPH_ONLY
# ------------------------------------------------------------------ #

async def execute_graph_only(state: AgentState, *, config: RunnableConfig) -> Dict[str, List[BaseMessage]]:
    neo4j_graph = get_neo4j_graph()
    if neo4j_graph is None:
        return {"messages": [AIMessage(content="抱歉，知识库服务暂时不可用，请稍后重试。")]}

    cypher_retriever = NorthwindCypherRetriever()
    tool_schemas: List[type[BaseModel]] = [cypher_query, predefined_cypher]
    workflow = create_multi_tool_workflow(
        llm=_cypher_model, graph=neo4j_graph, tool_schemas=tool_schemas,
        predefined_cypher_dict=predefined_cypher_dict,
        cypher_example_retriever=cypher_retriever,
        scope_description=SCOPE_DESCRIPTION, llm_cypher_validation=True,
    )
    last_message = state.messages[-1].content if state.messages else ""
    response = await workflow.ainvoke({"question": last_message, "data": [], "history": []})
    return {"messages": [
        AIMessage(content="正在查询...  "),
        AIMessage(content=response["answer"]),
    ]}


# ------------------------------------------------------------------ #
# 执行节点：RAG_ONLY
# ------------------------------------------------------------------ #

async def execute_rag_only(state: AgentState, *, config: RunnableConfig) -> Dict[str, List[BaseMessage]]:
    neo4j_graph = get_neo4j_graph()
    if neo4j_graph is None:
        return {"messages": [AIMessage(content="抱歉，知识库服务暂时不可用，请稍后重试。")]}

    cypher_retriever = NorthwindCypherRetriever()
    tool_schemas: List[type[BaseModel]] = [rag_document_query]
    workflow = create_multi_tool_workflow(
        llm=_cypher_model, graph=neo4j_graph, tool_schemas=tool_schemas,
        predefined_cypher_dict=predefined_cypher_dict,
        cypher_example_retriever=cypher_retriever,
        scope_description=SCOPE_DESCRIPTION, llm_cypher_validation=True,
    )
    last_message = state.messages[-1].content if state.messages else ""
    response = await workflow.ainvoke({"question": last_message, "data": [], "history": []})
    return {"messages": [
        AIMessage(content="正在检索文档...  "),
        AIMessage(content=response["answer"]),
    ]}


# ------------------------------------------------------------------ #
# 执行节点：PARALLEL（Neo4j + RAG 并行）
# ------------------------------------------------------------------ #

async def execute_parallel(state: AgentState, *, config: RunnableConfig) -> Dict[str, List[BaseMessage]]:
    neo4j_graph = get_neo4j_graph()
    if neo4j_graph is None:
        return {"messages": [AIMessage(content="抱歉，知识库服务暂时不可用，请稍后重试。")]}

    cypher_retriever = NorthwindCypherRetriever()
    tool_schemas: List[type[BaseModel]] = [cypher_query, predefined_cypher, rag_document_query]
    workflow = create_multi_tool_workflow(
        llm=_cypher_model, graph=neo4j_graph, tool_schemas=tool_schemas,
        predefined_cypher_dict=predefined_cypher_dict,
        cypher_example_retriever=cypher_retriever,
        scope_description=SCOPE_DESCRIPTION, llm_cypher_validation=True,
    )
    last_message = state.messages[-1].content if state.messages else ""
    response = await workflow.ainvoke({"question": last_message, "data": [], "history": []})
    return {"messages": [
        AIMessage(content="正在同时查询数据库和文档...  "),
        AIMessage(content=response["answer"]),
    ]}


# ------------------------------------------------------------------ #
# 执行节点：GRAPH_THEN_RAG（先 Neo4j 再 RAG，串行）
# ------------------------------------------------------------------ #

async def execute_graph_then_rag(state: AgentState, *, config: RunnableConfig) -> Dict[str, List[BaseMessage]]:
    neo4j_graph = get_neo4j_graph()
    if neo4j_graph is None:
        return {"messages": [AIMessage(content="抱歉，知识库服务暂时不可用，请稍后重试。")]}

    cypher_retriever = NorthwindCypherRetriever()
    tool_schemas: List[type[BaseModel]] = [cypher_query, predefined_cypher, rag_document_query]
    workflow = create_multi_tool_workflow(
        llm=_cypher_model, graph=neo4j_graph, tool_schemas=tool_schemas,
        predefined_cypher_dict=predefined_cypher_dict,
        cypher_example_retriever=cypher_retriever,
        scope_description=SCOPE_DESCRIPTION, llm_cypher_validation=True,
    )
    last_message = state.messages[-1].content if state.messages else ""
    response = await workflow.ainvoke({"question": last_message, "data": [], "history": []})
    return {"messages": [
        AIMessage(content="正在先查询数据库，再检索文档...  "),
        AIMessage(content=response["answer"]),
    ]}


# ------------------------------------------------------------------ #
# AgentReAct 兜底节点（最多 3 轮）
# ------------------------------------------------------------------ #

REACT_SYSTEM_PROMPT = """你是一个电商客服 Agent，可以使用工具来回答用户问题。

可用工具：
- neo4j_query: 查询 Neo4j 图数据库中的商品/订单/客户等结构化数据
- predefined_cypher: 使用预定义的 Cypher 模板进行常见查询
- rag_search: 检索文档知识库中的售后政策、保修条款等

规则：
1. 观察工具返回的结果，判断是否足够回答用户问题
2. 如果结果不足，可以调用更多工具
3. 如果结果足够，直接生成最终回复
4. 最多 3 轮工具调用
"""


async def agent_react_node(state: AgentState, *, config: RunnableConfig) -> Dict[str, List[BaseMessage] | int]:
    neo4j_graph = get_neo4j_graph()
    if neo4j_graph is None:
        return {"messages": [AIMessage(content="抱歉，知识库服务暂时不可用，请稍后重试。")]}

    cypher_retriever = NorthwindCypherRetriever()
    tool_schemas: List[type[BaseModel]] = [cypher_query, predefined_cypher, rag_document_query]
    workflow = create_multi_tool_workflow(
        llm=_cypher_model, graph=neo4j_graph, tool_schemas=tool_schemas,
        predefined_cypher_dict=predefined_cypher_dict,
        cypher_example_retriever=cypher_retriever,
        scope_description=SCOPE_DESCRIPTION, llm_cypher_validation=True,
        max_attempts=3,
    )
    last_message = state.messages[-1].content if state.messages else ""

    # 最多 3 轮迭代
    react_round = getattr(state, "react_round", 0)
    if react_round >= 3:
        return {"messages": [AIMessage(content="亲～抱歉，这个问题可能需要人工客服协助，我帮您转接～")]}

    response = await workflow.ainvoke({"question": last_message, "data": [], "history": []})
    return {
        "messages": [AIMessage(content=response["answer"])],
        "react_round": react_round + 1,
        "question": last_message,
    }


# ------------------------------------------------------------------ #
# after_response（记忆写入）
# ------------------------------------------------------------------ #

async def after_response(state: AgentState, *, config: RunnableConfig) -> dict:
    middleware = _get_memory_middleware()
    if middleware is None:
        return {}
    try:
        configurable = config.get("configurable", {})
        tenant_id = configurable.get("tenant_id", "default")
        user_id = configurable.get("user_id", "anonymous")
        session_id = configurable.get("thread_id", "default")
        user_message = state.messages[-2].content if len(state.messages) >= 2 else ""
        assistant_message = state.messages[-1].content if state.messages else ""
        if user_message and assistant_message:
            await middleware.after_agent(tenant_id=tenant_id, user_id=user_id, session_id=session_id,
                                         user_message=user_message, assistant_message=assistant_message)
    except Exception:
        pass
    return {}


# ================================================================== #
# 图构建
# ================================================================== #

builder = StateGraph(AgentState, input=InputState)

# 节点注册
builder.add_node(analyze_and_route_query)
builder.add_node(respond_to_general_query)
builder.add_node("guardrails_node", guardrails_node)
builder.add_node("retrieval_plan_route", retrieval_plan_route)
builder.add_node("execute_graph_only", execute_graph_only)
builder.add_node("execute_rag_only", execute_rag_only)
builder.add_node("execute_parallel", execute_parallel)
builder.add_node("execute_graph_then_rag", execute_graph_then_rag)
builder.add_node("agent_react_node", agent_react_node)
builder.add_node("after_response", after_response)

# 边
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
    "execute_graph_then_rag": "execute_graph_then_rag",
    "agent_react_node": "agent_react_node",
})

# 所有执行路径汇聚到 after_response
for node in ["execute_graph_only", "execute_rag_only", "execute_parallel",
             "execute_graph_then_rag", "agent_react_node"]:
    builder.add_edge(node, "after_response")

builder.add_edge("after_response", END)

graph = builder.compile()
