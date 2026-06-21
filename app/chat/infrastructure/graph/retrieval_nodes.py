"""主图中的检索执行节点实现。

这个模块负责：
- KG-only、RAG-only、并行检索、串行检索节点
- 统一把检索结果交给摘要层生成最终回复

这个模块不负责：
- 顶层路由和守卫
- ReAct 子图实现
- after_response 写回记忆

重构后：
- 使用 ExecutionPipeline 抽取通用逻辑
- 各节点只需声明检索策略
"""

from __future__ import annotations

import asyncio

from langchain_core.runnables import RunnableConfig

from app.chat.domain.utils import no_neo4j_response, question_from_state
from app.chat.infrastructure.graph.execution_pipeline import ExecutionPipeline
from app.chat.infrastructure.graph.execution_utils import (
    build_graph_only_query,
    build_graph_then_rag_query,
    build_rag_only_query,
)
from app.chat.infrastructure.graph.message_utils import (
    build_simple_message_response,
)
from app.chat.infrastructure.graph.state import AgentState
from app.chat.infrastructure.memory_bridge.context import enrich_question
from app.chat.infrastructure.retrievers.retriever_contracts import (
    KG_RETRIEVER_NAME,
    RAG_RETRIEVER_NAME,
)
from app.chat.infrastructure.retrievers.retriever_runtime import get_retriever

# 共享的管道实例，注入不同的 progress_message 和 fallback 即可
_pipeline = ExecutionPipeline()


async def execute_graph_only(state: AgentState, *, config: RunnableConfig) -> dict:
    """仅查 Neo4j 图数据库（通过 Retriever 接口）。"""
    kg = await get_retriever(KG_RETRIEVER_NAME)
    if kg is None:
        return no_neo4j_response()

    return await _pipeline.execute_single(
        state,
        config,
        kg,
        progress_message="正在查询...",
        fallback="未查询到相关信息，请确认后重新咨询～",
    )


async def execute_rag_only(state: AgentState, *, config: RunnableConfig) -> dict:
    """仅查 RAG 文档知识库（通过 Retriever 接口）。"""
    rag = await get_retriever(RAG_RETRIEVER_NAME)
    if rag is None:
        return build_simple_message_response("文档检索服务暂不可用。")

    return await _pipeline.execute_single(
        state,
        config,
        rag,
        progress_message="正在检索文档...",
        fallback="未在文档中找到相关信息～",
    )


async def execute_parallel(state: AgentState, *, config: RunnableConfig) -> dict:
    """并行查 Neo4j + RAG（通过 Retriever 接口），合并结果后生成摘要。"""
    kg = await get_retriever(KG_RETRIEVER_NAME)
    if kg is None:
        return no_neo4j_response()
    rag = await get_retriever(RAG_RETRIEVER_NAME)

    # 使用 ExecutionPipeline 的并行能力
    query = await enrich_question(state, config, question_from_state(state))
    kg_query = build_graph_only_query(query)
    rag_query = build_rag_only_query(query)

    return await _pipeline.execute_parallel(
        state,
        config,
        (kg, kg_query),
        (rag, rag_query),
        progress_message="正在同时查询...",
    )


async def execute_then(state: AgentState, *, config: RunnableConfig) -> dict:
    """先查 Neo4j 确定实体，再用结果查 RAG（通过 Retriever 接口）。"""
    kg = await get_retriever(KG_RETRIEVER_NAME)
    if kg is None:
        return no_neo4j_response()
    rag = await get_retriever(RAG_RETRIEVER_NAME)

    # 使用 ExecutionPipeline 的串行能力
    query = await enrich_question(state, config, question_from_state(state))
    rag_query = build_graph_then_rag_query(query, [])  # 占位，实际由 pipeline 处理

    return await _pipeline.execute_sequential(
        state,
        config,
        first=(kg, query),
        second=(rag, rag_query),
        progress_message="正在先查数据库，再查文档...",
    )


__all__ = [
    "execute_graph_only",
    "execute_parallel",
    "execute_rag_only",
    "execute_then",
]
