"""主图中的检索执行节点实现。

这个模块负责：
- KG-only、RAG-only、并行检索、串行检索节点
- 统一把检索结果交给摘要层生成最终回复

这个模块不负责：
- 顶层路由和守卫
- ReAct 子图实现
- after_response 写回记忆
"""

import asyncio

from app.chat.infrastructure.graph.execution_utils import (
    summarize_and_build_response,
)
from app.chat.infrastructure.graph.state import AgentState
from app.chat.infrastructure.memory_bridge.context import enrich_question
from app.chat.infrastructure.retrievers.retriever_contracts import (
    KG_RETRIEVER_NAME,
    RAG_RETRIEVER_NAME,
)
from app.chat.infrastructure.retrievers.retriever_runtime import get_retriever
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig

_NEO4J_UNAVAILABLE_MESSAGE = "抱歉，知识库服务暂时不可用，请稍后重试。"


async def execute_graph_only(state: AgentState, *, config: RunnableConfig) -> dict:
    """仅查 Neo4j 图数据库（通过 Retriever 接口）。"""
    kg = await get_retriever(KG_RETRIEVER_NAME)
    if kg is None:
        return {"messages": [AIMessage(content=_NEO4J_UNAVAILABLE_MESSAGE)]}

    query = await enrich_question(
        state,
        config,
        state.messages[-1].content,
    )
    records = await kg.search(query)
    return await summarize_and_build_response(
        query,
        records,
        progress_message="正在查询...",
        fallback="未查询到相关信息，请确认后重新咨询～",
    )


async def execute_rag_only(state: AgentState, *, config: RunnableConfig) -> dict:
    """仅查 RAG 文档知识库（通过 Retriever 接口）。"""
    rag = await get_retriever(RAG_RETRIEVER_NAME)
    query = await enrich_question(
        state,
        config,
        state.messages[-1].content,
    )
    records = await rag.search(query)
    return await summarize_and_build_response(
        query,
        records,
        progress_message="正在检索文档...",
        fallback="未在文档中找到相关信息～",
    )


async def execute_parallel(state: AgentState, *, config: RunnableConfig) -> dict:
    """并行查 Neo4j + RAG（通过 Retriever 接口），合并结果后生成摘要。"""
    kg = await get_retriever(KG_RETRIEVER_NAME)
    if kg is None:
        return {"messages": [AIMessage(content=_NEO4J_UNAVAILABLE_MESSAGE)]}
    rag = await get_retriever(RAG_RETRIEVER_NAME)

    query = await enrich_question(
        state,
        config,
        state.messages[-1].content,
    )
    graph_query = query + "（仅查询结构化数据：价格、库存、订单等）"
    rag_query = query + "（仅查询文档知识：售后政策、保修条款等）"
    neo_records, rag_records = await asyncio.gather(
        kg.search(graph_query),
        rag.search(rag_query),
    )

    all_records = [
        *neo_records,
        *rag_records,
    ]
    return await summarize_and_build_response(
        query,
        all_records,
        progress_message="正在同时查询...",
    )


async def execute_then(state: AgentState, *, config: RunnableConfig) -> dict:
    """先查 Neo4j 确定实体，再用结果查 RAG（通过 Retriever 接口）。"""
    kg = await get_retriever(KG_RETRIEVER_NAME)
    if kg is None:
        return {"messages": [AIMessage(content=_NEO4J_UNAVAILABLE_MESSAGE)]}
    rag = await get_retriever(RAG_RETRIEVER_NAME)

    query = await enrich_question(
        state,
        config,
        state.messages[-1].content,
    )
    neo_records = await kg.search(query)
    rag_query = f"已知信息：{neo_records}\n\n查询：{query}"
    rag_records = await rag.search(rag_query)
    all_records = [*neo_records, *rag_records]
    return await summarize_and_build_response(
        query,
        all_records,
        progress_message="正在先查数据库，再查文档...",
    )
