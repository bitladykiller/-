"""执行管道抽象 — 将检索执行节点的通用步骤抽取为可组合的管道。

职责：
- 定义 ExecutionPipeline 类，封装 enrich → search → merge → summarize → wrap 流程
- 让 5 个执行节点共享同一套编排骨架，只注入不同的 retriever 组合

不负责：
- 节点路由和守卫
- 记忆上下文读取
- ReAct 子图实现
"""

from __future__ import annotations

from typing import Any

from app.chat.infrastructure.graph.execution_utils import (
    merge_retriever_records,
    records_from_result,
    search_retriever,
    summarize_and_build_response,
)
from app.chat.infrastructure.graph.message_utils import MessagePayload
from app.chat.infrastructure.graph.state import AgentState
from app.chat.infrastructure.memory_bridge.context import enrich_question
from app.chat.infrastructure.retrievers.retriever_contracts import Retriever
from app.chat.domain.utils import question_from_state


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
        """单检索器执行：enrich → search → summarize。

        Args:
            state: Agent 状态
            config: LangGraph 运行时配置
            retriever: 检索器实例
            progress_message: 覆盖默认进度消息
            fallback: 覆盖默认兜底消息

        Returns:
            标准消息负载
        """
        query = await enrich_question(state, config, question_from_state(state))
        result = await search_retriever(retriever, query)
        return await summarize_and_build_response(
            query,
            records_from_result(result),
            progress_message=progress_message or self._progress_message,
            fallback=fallback or self._fallback,
        )

    async def execute_parallel(
        self,
        state: AgentState,
        config: Any,
        *retrievers: tuple[Retriever | None, str],
        progress_message: str | None = None,
        fallback: str | None = None,
    ) -> MessagePayload:
        """多检索器并行执行：enrich → 并行 search → merge → summarize。

        Args:
            state: Agent 状态
            config: LangGraph 运行时配置
            *retrievers: (检索器, 查询) 元组列表
            progress_message: 覆盖默认进度消息
            fallback: 覆盖默认兜底消息

        Returns:
            标准消息负载
        """
        import asyncio

        query = await enrich_question(state, config, question_from_state(state))
        results = await asyncio.gather(
            *(search_retriever(r, q) for r, q in retrievers)
        )
        all_records = merge_retriever_records(*results)
        return await summarize_and_build_response(
            query,
            all_records,
            progress_message=progress_message or self._progress_message,
            fallback=fallback or self._fallback,
        )

    async def execute_sequential(
        self,
        state: AgentState,
        config: Any,
        first: tuple[Retriever | None, str],
        second: tuple[Retriever | None, str],
        *,
        progress_message: str | None = None,
        fallback: str | None = None,
    ) -> MessagePayload:
        """两阶段串行执行：enrich → search_first → search_second(含 first 结果) → merge → summarize。

        Args:
            state: Agent 状态
            config: LangGraph 运行时配置
            first: (检索器, 查询) 第一阶段
            second: (检索器, 查询) 第二阶段
            progress_message: 覆盖默认进度消息
            fallback: 覆盖默认兜底消息

        Returns:
            标准消息负载
        """
        query = await enrich_question(state, config, question_from_state(state))
        first_retriever, first_query = first
        first_result = await search_retriever(first_retriever, first_query)
        second_retriever, second_query = second
        second_result = await search_retriever(second_retriever, second_query)
        all_records = merge_retriever_records(first_result, second_result)
        return await summarize_and_build_response(
            query,
            all_records,
            progress_message=progress_message or self._progress_message,
            fallback=fallback or self._fallback,
        )


__all__ = ["ExecutionPipeline"]
