"""
ReAct Agent 子图 — 兜底策略节点。

v3.17 新增：从 lg_nodes.py 拆分。lg_nodes.py 已达 562 行且混合了
路由节点 + 执行节点 + 模型类 + 检索器管理多种职责。将 ReAct 子图的
构建和执行逻辑独立出来，降低单文件复杂度。

设计模式：Template Method（模板方法模式）
- _build_react_subgraph() 构建子图（模板）
- execute_react() 使用子图执行检索（算法骨架）
- 答案充分性检查是可替换的验证步骤
"""
from __future__ import annotations

import asyncio
import json
from typing import cast

from langchain_core.runnables import RunnableConfig
from langchain_core.messages import AIMessage
from langgraph.prebuilt import create_react_agent
from langgraph.graph.state import CompiledStateGraph
from langchain_core.tools import tool

from app.lg_agent.lg_states import AgentState
from app.lg_agent.lg_retrievers import (
    RetrieverRegistry,
    _ensure_registry,
    get_registry,
)
from app.lg_agent.lg_prompts import (
    REACT_SYSTEM_PROMPT,
    REACT_ANSWER_CHECK_PROMPT,
)
from app.lg_agent.lg_models import (
    react_model,
    react_judge_model,
    ReactAnswerCheckOutput,
)
from app.lg_agent.lg_context import enrich_question
from app.lg_agent.kg_sub_graph.kg_neo4j_conn import get_neo4j_graph


# ================================================================== #
# 工具函数 — ReAct 子图内部使用
# ================================================================== #

def _safe_records(result: dict) -> list:
    """从 RAG 或 Text2Cypher 结果中提取 records，兼容 records/cyphers 两种格式。"""
    if "records" in result:
        return result.get("records", [])
    cyphers = result.get("cyphers", [])
    if cyphers:
        return cyphers[0].get("records", [])
    return []


def _no_neo4j() -> dict:
    """Neo4j 不可用时的统一降级响应。"""
    return {"messages": [AIMessage(content="抱歉，知识库服务暂时不可用，请稍后重试。")]}


def _question(state: AgentState) -> str:
    """从 state 中提取用户最新消息。"""
    return state.messages[-1].content if state.messages else ""


# ================================================================== #
# ReAct 子图单例 — 懒初始化 + 双检锁
# ================================================================== #

_react_subgraph: CompiledStateGraph | None = None
_react_lock: asyncio.Lock = asyncio.Lock()


async def _build_react_subgraph() -> CompiledStateGraph:
    """构建 ReAct 子图：两个工具（neo4j_query + rag_search）。

    通过 Retriever 接口而非直接调用底层实现（依赖倒置）。

    Returns:
        编译后的 ReAct LangGraph 子图。
    """
    await _ensure_registry()
    registry = get_registry()
    kg = registry.get("kg")

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
        rag = registry.get("rag")
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
    """获取 ReAct 子图单例（双检锁防并发创建）。

    Returns:
        编译后的 ReAct LangGraph 子图。
    """
    global _react_subgraph
    if _react_subgraph is None:
        async with _react_lock:
            if _react_subgraph is None:
                _react_subgraph = await _build_react_subgraph()
    return _react_subgraph


# ================================================================== #
# execute_react — ReAct 兜底执行 + 答案充分性检查
# ================================================================== #

async def execute_react(state: AgentState, *, config: RunnableConfig) -> dict:
    """ReAct 兜底执行 + 答案充分性检查，最多 5 轮。

    流程：
    1. 增强问题（注入记忆上下文）
    2. ReAct 子图执行（最多 11 步 tool call）
    3. 答案充分性检查（sufficient / retry / handoff）
    4. 不足时最多重试 5 轮

    Args:
        state: Agent 状态。
        config: LangGraph 运行时配置。

    Returns:
        {"messages": [...]} 包含最终回复。
    """
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
                await react_judge_model.with_structured_output(
                    ReactAnswerCheckOutput
                ).ainvoke(check_messages),
            )

            if check.decision == "sufficient":
                return {
                    "messages": [
                        AIMessage(content="正在综合分析..."),
                        AIMessage(content=str(last_answer)),
                    ],
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
