"""ReAct 子图执行入口。

职责：
- 构建 ReAct 兜底子图
- 把 KG / RAG 检索器暴露成工具
- 在 ReAct 回答后追加充分性检查与有限重试

边界：
- 这里只处理 ReAct 兜底链路，不承载主图路由决策
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent
from langchain_core.tools import tool

from app.chat.infrastructure.graph.state import AgentState
from app.chat.infrastructure.retrievers.retriever_contracts import (
    KG_RETRIEVER_NAME,
    RAG_RETRIEVER_NAME,
)
from app.chat.infrastructure.retrievers.retriever_runtime import get_retriever
from app.chat.infrastructure.modeling.prompts import (
    REACT_SYSTEM_PROMPT,
    REACT_ANSWER_CHECK_PROMPT,
)
from app.chat.infrastructure.modeling.models import (
    react_model,
    react_judge_model,
    ReactAnswerCheckOutput,
)
from app.chat.infrastructure.memory_bridge.context import enrich_question
from app.chat.infrastructure.kg_sub_graph.kg_neo4j_conn import get_neo4j_graph
from app.chat.domain.utils import question_from_state, no_neo4j_response
from app.platform.config.app_config import app_config

# 所有运行时行为常量统一从 app_config 读取
_REACT_CFG = app_config.react

_react_subgraph: CompiledStateGraph | None = None
_react_lock: asyncio.Lock = asyncio.Lock()


async def get_react_subgraph(
    builder: Callable[[], Awaitable[CompiledStateGraph]],
) -> CompiledStateGraph:
    """获取 ReAct 子图单例（双检锁防并发创建）。"""
    global _react_subgraph
    if _react_subgraph is None:
        async with _react_lock:
            if _react_subgraph is None:
                _react_subgraph = await builder()
    return _react_subgraph


# ================================================================== #
# execute_react — ReAct 兜底执行 + 答案充分性检查
# ================================================================== #

async def execute_react(state: AgentState, *, config: RunnableConfig) -> dict:
    """ReAct 兜底执行 + 答案充分性检查，最多 N 轮。

    流程：
    1. 增强问题（注入记忆上下文）
    2. ReAct 子图执行（最多 recursion_limit 步 tool call）
    3. 答案充分性检查（sufficient / retry / handoff）
    4. 不足时最多重试 max_attempts 轮

    Args:
        state: Agent 状态。
        config: LangGraph 运行时配置。

    Returns:
        {"messages": [...]} 包含最终回复。
    """
    if get_neo4j_graph() is None:
        return no_neo4j_response()

    q = await enrich_question(state, config, question_from_state(state))

    async def build_react_subgraph() -> CompiledStateGraph:
        """构建只供当前执行入口缓存复用的 ReAct 子图。"""
        kg = await get_retriever(KG_RETRIEVER_NAME)
        rag = await get_retriever(RAG_RETRIEVER_NAME)

        @tool
        async def neo4j_query(task: str) -> str:
            """查询 Neo4j 知识图谱，获取商品、订单、客户等结构化数据。"""
            if kg is None:
                return json.dumps({"error": "知识图谱服务不可用"}, ensure_ascii=False)
            return json.dumps(
                (await kg.search(task)).get("records", []),
                ensure_ascii=False,
            )

        @tool
        async def rag_search(query: str) -> str:
            """检索文档知识库，获取售后政策、保修条款等非结构化信息。"""
            if rag is None:
                return json.dumps({"error": "文档检索服务不可用"}, ensure_ascii=False)
            return json.dumps(
                (await rag.search(query)).get("records", []),
                ensure_ascii=False,
            )

        return create_react_agent(
            model=react_model,
            tools=[neo4j_query, rag_search],
            prompt=REACT_SYSTEM_PROMPT,
            version="v2",
            name="customer_service_react_agent",
        )

    sg = await get_react_subgraph(build_react_subgraph)
    subgraph_config = dict(config) if config else {}
    subgraph_config["recursion_limit"] = _REACT_CFG.recursion_limit
    react_messages: list[dict[str, str]] = [{"role": "user", "content": q}]
    insufficiency_reason = _REACT_CFG.initial_reason

    for attempt in range(1, _REACT_CFG.max_attempts + 1):
        if attempt > 1:
            react_messages.append(
                {
                    "role": "user",
                    "content": f"{_REACT_CFG.retry_prompt}不足原因：{insufficiency_reason}",
                }
            )

        result = await sg.ainvoke({"messages": react_messages}, config=subgraph_config)
        result_messages = result.get("messages", [])
        if not result_messages:
            last_answer = "未能确定回答～"
        else:
            last_content = getattr(result_messages[-1], "content", "")
            last_answer = str(last_content) if last_content else "未能确定回答～"

        if _REACT_CFG.step_exhausted_marker in last_answer.lower():
            insufficiency_reason = _REACT_CFG.step_exhausted_reason
        else:
            transcript_lines: list[str] = []
            for message in result_messages[-_REACT_CFG.transcript_window:]:
                role = getattr(message, "type", None) or getattr(
                    message,
                    "role",
                    "assistant",
                )
                content = getattr(message, "content", "")
                if content:
                    transcript_lines.append(f"[{role}] {content}")
            transcript = "\n".join(transcript_lines)
            check = await react_judge_model.with_structured_output(
                ReactAnswerCheckOutput
            ).ainvoke(
                [
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
            )
            if check.decision == "sufficient":
                return {
                    "messages": [
                        AIMessage(content=_REACT_CFG.progress_message),
                        AIMessage(content=last_answer),
                    ],
                }

            insufficiency_reason = check.reason or _REACT_CFG.default_insufficiency_reason

        # 准备下一轮：保留原始问题 + 上一轮候选答案
        react_messages = [
            {"role": "user", "content": q},
            {"role": "assistant", "content": last_answer},
        ]

    # 所有轮次用尽仍未充分
    return {
        "messages": [
            AIMessage(content=_REACT_CFG.progress_message),
            AIMessage(content=_REACT_CFG.fallback_answer),
        ],
    }
