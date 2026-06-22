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

from typing import Any, TypeAlias

from typing_extensions import TypedDict

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.chat.infrastructure.graph.message_utils import MessagePayload, build_progress_response
from app.chat.infrastructure.retrievers.retriever_contracts import Retriever

RetrieverRecord: TypeAlias = dict[str, Any]

_SUMMARIZE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """你是一个专业的电商智能客服助手，擅长将复杂信息整理成简洁明了的回答。

请以类似淘宝/京东等知名电商客服的风格回复用户：
- 开场要亲切，使用"亲～"或"顾客您好～"等问候语
- 保持积极、专业的语气
- 适当使用emoji表情（如 👋 😊 ❤️）增加亲和力
- 结尾表达感谢和继续服务的意愿""",
        ),
        (
            "human",
            """事实信息：{results}

问题："{question}"

请按照以下要求回答：
* 根据上述事实信息，以亲切的电商客服口吻回答用户问题
* 当事实不为空时，只使用这些信息构建回答
* 不要道歉或使用"根据系统"等机械表达
* 如果有多个结果，请以清晰的格式列出重要信息
* 回复应当简洁明了，保持专业而友好
* 可以在结尾表达继续服务的意愿（如"还有其他问题随时问我哦～"）""",
        ),
    ]
)


class RetrieverResult(TypedDict, total=False):
    """检索器标准输出结构。"""

    task: str
    records: list[RetrieverRecord]
    errors: list[Any]
    steps: list[Any]


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
        return {
            "task": query,
            "records": [],
            "errors": [],
            "steps": [],
        }
    return await retriever.search(query)


async def ainvoke_structured_question_output(
    *,
    system_prompt: str,
    human_prompt: str,
    model: Any,
    output_schema: type[Any],
    question: str,
) -> Any:
    """统一执行"system + 单个问题模板"的结构化输出链。"""
    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", human_prompt),
    ])
    chain = prompt | model.with_structured_output(output_schema)
    return await chain.ainvoke({"question": question})


async def summarize_records(
    query: str,
    records: list[RetrieverRecord],
    fallback: str = "未查询到相关信息～",
) -> str:
    """根据检索结果生成摘要，空结果时直接返回 fallback。"""
    if not records:
        return fallback

    from app.platform.container import get_container

    container = await get_container()
    if container.summarize_chain is None:
        from app.chat.infrastructure.modeling.models import cypher_model

        container.summarize_chain = _SUMMARIZE_PROMPT | cypher_model | StrOutputParser()

    summary = await container.summarize_chain.ainvoke({
        "question": query,
        "results": [records],
    })
    return summary or fallback


async def summarize_and_build_response(
    query: str,
    records: list[RetrieverRecord],
    *,
    progress_message: str,
    fallback: str = "未查询到相关信息～",
) -> MessagePayload:
    """统一执行摘要生成，并返回"两段式"进度响应。"""
    summary = await summarize_records(query, records, fallback)
    return build_progress_response(progress_message, summary)
