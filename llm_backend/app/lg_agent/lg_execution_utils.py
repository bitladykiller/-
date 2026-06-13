"""LangGraph 执行辅助函数。

职责：
- 统一检索结果的空值占位、records 提取与多路合并
- 提供图检索 / 文档检索的查询改写样板
- 封装结构化输出链与摘要回复样板

边界：
- 不负责主图节点路由
- 不负责记忆上下文读取
"""

from __future__ import annotations

from typing import Any, TypedDict, TypeAlias

from langchain_core.prompts import ChatPromptTemplate

from app.lg_agent.lg_message_utils import MessagePayload, build_progress_response
from app.lg_agent.lg_retrievers import Retriever
from app.lg_agent.lg_summarize import summarize_records

RetrieverRecord: TypeAlias = dict[str, Any]


class RetrieverResult(TypedDict, total=False):
    """检索器标准输出结构。"""

    task: str
    records: list[RetrieverRecord]
    errors: list[Any]
    steps: list[Any]


def empty_retriever_result(task: str = "") -> RetrieverResult:
    """无检索器可用时的空结果占位。"""
    return {
        "task": task,
        "records": [],
        "errors": [],
        "steps": [],
    }


def records_from_result(result: RetrieverResult) -> list[RetrieverRecord]:
    """从统一 Retriever 结果中安全提取 records 列表。"""
    records = result.get("records", [])
    return records if isinstance(records, list) else []


def merge_retriever_records(*results: RetrieverResult) -> list[RetrieverRecord]:
    """按顺序合并多个检索结果中的 records。"""
    merged_records: list[RetrieverRecord] = []
    for result in results:
        merged_records.extend(records_from_result(result))
    return merged_records


def build_graph_only_query(question: str) -> str:
    """给图检索构造更聚焦的结构化查询提示。"""
    return question + "（仅查询结构化数据：价格、库存、订单等）"


def build_rag_only_query(question: str) -> str:
    """给文档检索构造更聚焦的知识型查询提示。"""
    return question + "（仅查询文档知识：售后政策、保修条款等）"


def build_graph_then_rag_query(
    question: str,
    graph_records: list[RetrieverRecord],
) -> str:
    """把图检索结果拼进后续文档检索查询。"""
    return f"已知信息：{graph_records}\n\n查询：{question}"


async def search_retriever(
    retriever: Retriever | None,
    query: str,
) -> RetrieverResult:
    """执行检索；检索器缺失时返回空结果占位。"""
    if retriever is None:
        return empty_retriever_result(query)
    return await retriever.search(query)


async def ainvoke_structured_question_output(
    *,
    system_prompt: str,
    human_prompt: str,
    model: Any,
    output_schema: type[Any],
    question: str,
) -> Any:
    """统一执行“system + 单个问题模板”的结构化输出链。"""
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", human_prompt),
    ])
    chain = prompt | model.with_structured_output(output_schema)
    return await chain.ainvoke({"question": question})


async def summarize_and_build_response(
    query: str,
    records: list[RetrieverRecord],
    *,
    progress_message: str,
    fallback: str = "未查询到相关信息～",
) -> MessagePayload:
    """统一执行摘要生成，并返回“两段式”进度响应。"""
    summary = await summarize_records(query, records, fallback)
    return build_progress_response(progress_message, summary)
