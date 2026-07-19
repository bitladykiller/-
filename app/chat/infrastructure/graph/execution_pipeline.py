"""执行管道抽象 — 将检索执行节点的通用步骤抽取为可组合的管道。

职责：
- 定义 ExecutionPipeline 类，封装 enrich → search → merge → summarize → wrap 流程
- 让 4 个执行节点共享同一套编排骨架，只注入不同的 retriever 组合
- 内部处理 query 构造（graph_only / rag_only / graph_then_rag），节点层不再关心

不负责：
- 节点路由和守卫
- 记忆上下文读取
- ReAct 子图实现
"""

from __future__ import annotations

from typing import Any, Literal

from app.chat.infrastructure.graph.execution_utils import (
    build_graph_only_query,
    build_graph_then_rag_query,
    build_rag_only_query,
    merge_retriever_records,
    records_from_result,
    search_retriever,
    summarize_and_build_response,
)
from app.chat.infrastructure.graph.memory_context import enrich_question
from app.chat.infrastructure.graph.message_utils import MessagePayload
from app.chat.infrastructure.graph.state import AgentState
from app.chat.infrastructure.retrievers.retriever_contracts import Retriever
from app.chat.infrastructure.shared.utils import question_from_state


class ExecutionPipeline:
    """检索执行管道。

    封装了所有执行节点共享的通用流程：
    1. enrich_question — 注入记忆上下文
    2. search_retriever — 调用检索器
    3. merge_retriever_records — 合并多路结果
    4. summarize_and_build_response — LLM 摘要 + 进度响应

    各执行节点只需提供检索器组合即可。
    """

    def __init__(
        self,
        *,
        progress_message: str = "正在查询...",
        fallback: str = "未查询到相关信息～",
    ) -> None:
        self._progress_message = progress_message
        self._fallback = fallback

    async def execute_single(
        self,
        state: AgentState,
        config: Any,
        retriever: Retriever | None,
        *,
        progress_message: str | None = None,
        fallback: str | None = None,
    ) -> MessagePayload:
        """单检索器执行：enrich → search → summarize。"""
        query = await enrich_question(state, config, question_from_state(state))
        result = await search_retriever(retriever, query)
        return await summarize_and_build_response(
            query,
            records_from_result(result),
            progress_message=progress_message or self._progress_message,
            fallback=fallback or self._fallback,
        )

    async def execute_dual(
        self,
        state: AgentState,
        config: Any,
        kg: Retriever | None,
        rag: Retriever | None,
        *,
        mode: Literal["parallel", "sequential"] = "parallel",
        progress_message: str | None = None,
        fallback: str | None = None,
    ) -> MessagePayload:
        """双检索器执行：enrich → 并行/串行 search → merge → summarize。

        Args:
            state: Agent 状态
            config: LangGraph 运行时配置
            kg: KG 检索器
            rag: RAG 检索器
            mode: "parallel" 并行, "sequential" 先 KG 后 RAG
            progress_message: 覆盖默认进度消息
            fallback: 覆盖默认兜底消息
        """
        import asyncio

        query = await enrich_question(state, config, question_from_state(state))

        if mode == "parallel":
            kg_result, rag_result = await asyncio.gather(
                search_retriever(kg, build_graph_only_query(query)),
                search_retriever(rag, build_rag_only_query(query)),
            )
        else:
            kg_result = await search_retriever(kg, query)
            kg_records = records_from_result(kg_result)
            rag_result = await search_retriever(
                rag,
                build_graph_then_rag_query(query, kg_records),
            )

        all_records = merge_retriever_records(kg_result, rag_result)
        return await summarize_and_build_response(
            query,
            all_records,
            progress_message=progress_message or self._progress_message,
            fallback=fallback or self._fallback,
        )


__all__ = ["ExecutionPipeline"]
