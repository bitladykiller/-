"""主图中的检索执行节点实现。

这个模块负责：
- KG-only、RAG-only、并行检索、串行检索节点
- 统一把检索结果交给摘要层生成最终回复

这个模块不负责：
- 顶层路由和守卫
- ReAct 子图实现
- after_response 写回记忆

重构后：
- 使用 ExecutionPipeline 抽取通用逻辑，各节点只需声明检索策略
- enrich_question / question_from_state 已内聚到 pipeline 中
"""

from __future__ import annotations

from app.chat.infrastructure.graph.execution_pipeline import ExecutionPipeline
from app.chat.infrastructure.graph.message_utils import (
    MessagePayload,
    build_simple_message_response,
)
from app.chat.infrastructure.graph.state import AgentState
from app.chat.infrastructure.retrievers.retriever_contracts import (
    KG_RETRIEVER_NAME,
    RAG_RETRIEVER_NAME,
)
from app.chat.infrastructure.retrievers.retriever_runtime import get_retriever
from app.chat.infrastructure.utils.helpers import no_neo4j_response
from langchain_core.runnables import RunnableConfig

# 共享的管道实例，注入不同的 progress_message 和 fallback 即可
_pipeline = ExecutionPipeline()


async def execute_graph_only(
    state: AgentState, *, config: RunnableConfig
) -> MessagePayload | dict[str, object]:
    """GRAPH_ONLY：结构化查询（库存/订单/价格等）只走 Neo4j。"""
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


async def execute_rag_only(
    state: AgentState, *, config: RunnableConfig
) -> MessagePayload | dict[str, object]:
    """RAG_ONLY：保修政策/说明书等文档语义检索。"""
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


async def execute_parallel(
    state: AgentState, *, config: RunnableConfig
) -> MessagePayload | dict[str, object]:
    """PARALLEL：两端同时查，合并 records 再摘要（召回优先）。"""
    kg = await get_retriever(KG_RETRIEVER_NAME)
    if kg is None:
        return no_neo4j_response()
    rag = await get_retriever(RAG_RETRIEVER_NAME)

    return await _pipeline.execute_dual(
        state,
        config,
        kg,
        rag,
        mode="parallel",
        progress_message="正在同时查询...",
    )


async def execute_then(
    state: AgentState, *, config: RunnableConfig
) -> MessagePayload | dict[str, object]:
    """GRAPH_THEN_RAG：先图谱锚定实体，再带着结果查文档。"""
    kg = await get_retriever(KG_RETRIEVER_NAME)
    if kg is None:
        return no_neo4j_response()
    rag = await get_retriever(RAG_RETRIEVER_NAME)

    return await _pipeline.execute_dual(
        state,
        config,
        kg,
        rag,
        mode="sequential",
        progress_message="正在先查数据库，再查文档...",
    )


__all__ = [
    "execute_graph_only",
    "execute_parallel",
    "execute_rag_only",
    "execute_then",
]
