"""LangGraph 执行辅助函数。

职责：
- 统一检索结果的空值占位、records 提取与多路合并
- 封装结构化输出链与摘要回复样板

边界：
- 不负责主图节点路由
- 不负责记忆上下文读取
"""

from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

_summarize_chain = None


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
    records: list[dict[str, Any]],
    *,
    progress_message: str,
    fallback: str = "未查询到相关信息～",
) -> dict[str, list[AIMessage]]:
    """统一执行摘要生成，并返回“两段式”进度响应。"""
    if not records:
        return {
            "messages": [
                AIMessage(content=progress_message),
                AIMessage(content=fallback),
            ]
        }

    global _summarize_chain
    if _summarize_chain is None:
        from app.chat.infrastructure.modeling.models import cypher_model

        _summarize_chain = ChatPromptTemplate.from_messages(
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
        ) | cypher_model | StrOutputParser()

    summary = await _summarize_chain.ainvoke({
        "question": query,
        "results": [records],
    })
    return {
        "messages": [
            AIMessage(content=progress_message),
            AIMessage(content=summary or fallback),
        ]
    }
