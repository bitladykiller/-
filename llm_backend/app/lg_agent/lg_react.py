"""ReAct 子图执行入口。

职责：
- 构建 ReAct 兜底子图
- 把 KG / RAG 检索器暴露成工具
- 在 ReAct 回答后追加充分性检查与有限重试

边界：
- 这里只处理 ReAct 兜底链路，不承载主图路由决策
"""
from __future__ import annotations

from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent
from langchain_core.tools import tool

from app.lg_agent.lg_states import AgentState
from app.lg_agent.lg_react_runtime import get_react_subgraph
from app.lg_agent.lg_retrievers import (
    KG_RETRIEVER_NAME,
    RAG_RETRIEVER_NAME,
    get_retriever,
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
from app.lg_agent.lg_react_support import (
    REACT_DEFAULT_INSUFFICIENCY_REASON,
    REACT_FALLBACK_ANSWER,
    REACT_INITIAL_REASON,
    REACT_STEP_EXHAUSTED_REASON,
    build_answer_check_messages,
    build_react_response,
    build_retry_message,
    build_retry_seed_messages,
    build_tool_error,
    build_transcript,
    dump_retriever_records,
    extract_last_answer,
    needs_more_steps,
)
from app.lg_agent.memory_bridge.context import enrich_question
from app.lg_agent.kg_sub_graph.kg_neo4j_conn import get_neo4j_graph
from app.lg_agent.utils import question_from_state, no_neo4j_response


REACT_MAX_ATTEMPTS = 5
REACT_RECURSION_LIMIT = 11


async def _judge_react_answer(
    question: str,
    result_messages: list[Any],
    candidate_answer: str,
) -> ReactAnswerCheckOutput:
    """调用裁判模型判断候选答案是否已经足够。"""
    transcript = build_transcript(result_messages)
    check_messages = build_answer_check_messages(
        judge_system_prompt=REACT_ANSWER_CHECK_PROMPT,
        question=question,
        transcript=transcript,
        candidate_answer=candidate_answer,
    )
    return cast(
        ReactAnswerCheckOutput,
        await react_judge_model.with_structured_output(
            ReactAnswerCheckOutput
        ).ainvoke(check_messages),
    )


async def _build_react_subgraph() -> CompiledStateGraph:
    """构建 ReAct 子图：两个工具（neo4j_query + rag_search）。

    通过 Retriever 接口而非直接调用底层实现（依赖倒置）。

    Returns:
        编译后的 ReAct LangGraph 子图。
    """
    kg = await get_retriever(KG_RETRIEVER_NAME)
    rag = await get_retriever(RAG_RETRIEVER_NAME)

    # ReAct 子图是单例，工具里直接捕获检索器引用即可，
    # 不必把“先拿注册表再查名字”的样板暴露给 ReAct 执行逻辑。
    @tool
    async def neo4j_query(task: str) -> str:
        """查询 Neo4j 知识图谱，获取商品、订单、客户等结构化数据。"""
        if kg is None:
            return build_tool_error("知识图谱服务不可用")
        return dump_retriever_records(await kg.search(task))

    @tool
    async def rag_search(query: str) -> str:
        """检索文档知识库，获取售后政策、保修条款等非结构化信息。"""
        if rag is None:
            return build_tool_error("文档检索服务不可用")
        return dump_retriever_records(await rag.search(query))

    tools = [neo4j_query, rag_search]
    return create_react_agent(
        model=react_model,
        tools=tools,
        prompt=REACT_SYSTEM_PROMPT,
        version="v2",
        name="customer_service_react_agent",
    )


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
        return no_neo4j_response()

    q = await enrich_question(state, config, question_from_state(state))
    sg = await get_react_subgraph(_build_react_subgraph)
    subgraph_config = dict(config) if config else {}
    # 单次 ReAct 子图的最大 agent/tools 步数
    subgraph_config["recursion_limit"] = REACT_RECURSION_LIMIT
    react_messages: list[dict[str, str]] = [{"role": "user", "content": q}]
    insufficiency_reason = REACT_INITIAL_REASON

    for attempt in range(1, REACT_MAX_ATTEMPTS + 1):
        if attempt > 1:
            react_messages.append(build_retry_message(insufficiency_reason))

        result = await sg.ainvoke({"messages": react_messages}, config=subgraph_config)
        result_messages = result.get("messages", [])
        last_answer = extract_last_answer(result_messages)

        if needs_more_steps(last_answer):
            insufficiency_reason = REACT_STEP_EXHAUSTED_REASON
        else:
            check = await _judge_react_answer(q, result_messages, last_answer)
            if check.decision == "sufficient":
                return build_react_response(last_answer)

            insufficiency_reason = check.reason or REACT_DEFAULT_INSUFFICIENCY_REASON

        # 准备下一轮：保留原始问题 + 上一轮候选答案
        react_messages = build_retry_seed_messages(q, last_answer)

    # 5 轮用尽仍未充分
    return build_react_response(REACT_FALLBACK_ANSWER)
