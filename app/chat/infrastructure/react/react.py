"""ReAct 子图执行入口。

职责：
- 构建 ReAct 兜底子图
- 把 KG / RAG 检索器暴露成工具
- 在 ReAct 回答后追加充分性检查与有限重试

边界：
- 这里只处理 ReAct 兜底链路，不承载主图路由决策
"""

import asyncio
import json

from app.chat.infrastructure.graph.message_utils import normalize_message_role
from app.chat.infrastructure.graph.state import AgentState
from app.chat.infrastructure.memory_bridge.context import enrich_question
from app.chat.infrastructure.modeling.models import (
    ReactAnswerCheckOutput,
    react_judge_model,
    react_model,
)
from app.chat.infrastructure.modeling.prompts import (
    REACT_ANSWER_CHECK_PROMPT,
    REACT_SYSTEM_PROMPT,
)
from app.chat.infrastructure.retrievers.retriever_contracts import (
    KG_RETRIEVER_NAME,
    RAG_RETRIEVER_NAME,
)
from app.chat.infrastructure.retrievers.retriever_runtime import get_retriever
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent

REACT_MAX_ATTEMPTS = 5
REACT_RECURSION_LIMIT = 11
_react_subgraph: CompiledStateGraph | None = None
_react_lock: asyncio.Lock = asyncio.Lock()


async def get_react_subgraph() -> CompiledStateGraph:
    """获取 ReAct 子图单例（双检锁防并发创建）。"""
    global _react_subgraph
    if _react_subgraph is None:
        async with _react_lock:
            if _react_subgraph is None:
                kg = await get_retriever(KG_RETRIEVER_NAME)
                if kg is None:
                    raise RuntimeError("kg retriever unavailable")
                rag = await get_retriever(RAG_RETRIEVER_NAME)

                # ReAct 子图是单例，工具里直接捕获检索器引用即可，
                # 不必把“先拿注册表再查名字”的样板暴露给执行逻辑。
                @tool
                async def neo4j_query(task: str) -> str:
                    """查询 Neo4j 知识图谱，获取商品、订单、客户等结构化数据。"""
                    return json.dumps(
                        await kg.search(task),
                        ensure_ascii=False,
                    )

                @tool
                async def rag_search(query: str) -> str:
                    """检索文档知识库，获取售后政策、保修条款等非结构化信息。"""
                    return json.dumps(
                        await rag.search(query),
                        ensure_ascii=False,
                    )

                _react_subgraph = create_react_agent(
                    model=react_model,
                    tools=[neo4j_query, rag_search],
                    prompt=REACT_SYSTEM_PROMPT,
                    version="v2",
                    name="customer_service_react_agent",
                )
    return _react_subgraph


# ================================================================== #
# execute_react — ReAct 兜底执行 + 答案充分性检查
# ================================================================== #

async def execute_react(state: AgentState, *, config: RunnableConfig) -> dict:
    """ReAct 兜底执行 + 答案充分性检查，最多 5 轮。

    流程：
    1. 增强问题（注入记忆上下文）
    2. ReAct 子图执行（最多 11 步 tool call）
    3. 答案充分性检查（sufficient / retry）
    4. 不足时最多重试 5 轮

    Args:
        state: Agent 状态。
        config: LangGraph 运行时配置。

    Returns:
        {"messages": [...]} 包含最终回复。
    """
    try:
        sg = await get_react_subgraph()
    except RuntimeError:
        return {"messages": [AIMessage(content="抱歉，知识库服务暂时不可用，请稍后重试。")]}

    q = await enrich_question(
        state,
        config,
        state.messages[-1].content,
    )
    subgraph_config = dict(config) if config else {}
    # 单次 ReAct 子图的最大 agent/tools 步数
    subgraph_config["recursion_limit"] = REACT_RECURSION_LIMIT
    react_messages: list[dict[str, str]] = [{"role": "user", "content": q}]
    insufficiency_reason = "初始状态：尚未完成充分回答。"

    for attempt in range(1, REACT_MAX_ATTEMPTS + 1):
        if attempt > 1:
            react_messages.append(
                {
                    "role": "user",
                    "content": (
                        "上一次候选答案仍然不充分，请继续按标准 ReAct 检索并补足关键事实。"
                        f"不足原因：{insufficiency_reason}"
                    ),
                }
            )

        result = await sg.ainvoke({"messages": react_messages}, config=subgraph_config)
        result_messages = result["messages"]
        if not result_messages:
            last_answer = "未能确定回答～"
        else:
            last_answer = str(result_messages[-1].text) or "未能确定回答～"

        if "need more steps" in last_answer.lower():
            insufficiency_reason = "单次 ReAct 内部步数耗尽，仍未得到足够答案。"
        else:
            transcript_lines: list[str] = []
            for message in result_messages[-20:]:
                role = normalize_message_role(message)
                content = str(message.text)
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
                        AIMessage(content="正在综合分析..."),
                        AIMessage(content=last_answer),
                    ],
                }

            insufficiency_reason = check.reason or "答案信息不足。"

        # 准备下一轮：保留原始问题 + 上一轮候选答案
        react_messages = [
            {"role": "user", "content": q},
            {"role": "assistant", "content": last_answer},
        ]

    # 5 轮用尽仍未充分
    return {
        "messages": [
            AIMessage(content="正在综合分析..."),
            AIMessage(content="亲～这个问题回答不了哦～"),
        ],
    }
