"""RAG 查询书面化改写。

职责：
- 将口语/电商客服问法改写成适合政策/说明书检索的书面问句
- 仅用于文档 RAG 支路（MilvusDocRetriever）

不做：
- HYDE（假文档）
- 退步改写（step-back）
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any

from app.shared.core.app_config import RagRewriteConfig, app_config
from app.shared.core.logger import get_logger
from pydantic import BaseModel, Field

logger = get_logger(__name__)

_FORMALIZE_SYSTEM = """你是智能家居电商客服场景的「检索查询改写器」。
把用户口语问题改写成适合检索售后政策、说明书、FAQ 的书面中文问句。

要求：
1. 保留产品类型、问题类型（保修/退换/安装/用法/库存相关说明等）与关键约束
2. 去掉语气词、口头禅、表情、无检索价值的寒暄
3. 不要编造用户未提到的品牌/型号/政策结论
4. 只输出一句书面问句，不要解释、不要引号、不要前缀标签
5. 若原句已经书面且清晰，可原样精炼，不要过度改写"""

_FORMALIZE_HUMAN = "用户问题：{question}\n书面检索问句："

_FILLER_PATTERNS = (
    re.compile(r"^(亲[～~!]*)+"),
    re.compile(r"^(您好|你好)[，,！!～~]*"),
    re.compile(r"[哈嘿啊呢吧哟哦嗯]+"),
    re.compile(r"[😂🤣😅😊👍❤️]+"),
)

StructuredInvokeFn = Callable[[str], Awaitable[Any]]


class FormalizedQuery(BaseModel):
    """结构化书面化输出。"""

    rewritten: str = Field(description="书面化后的单句检索问句")


def light_normalize_query(question: str) -> str:
    """无 LLM 的轻量清洗：去首尾空白与常见口语前缀。"""
    text = (question or "").strip()
    if not text:
        return ""
    for pattern in _FILLER_PATTERNS:
        text = pattern.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" ，,。.!！?？")
    return text.strip()


def _clip(text: str, max_chars: int) -> str:
    value = (text or "").strip()
    if max_chars > 0 and len(value) > max_chars:
        return value[:max_chars].rstrip()
    return value


def _extract_rewritten(result: Any) -> str:
    if isinstance(result, FormalizedQuery):
        return result.rewritten
    if isinstance(result, dict):
        return str(result.get("rewritten") or "")
    return str(getattr(result, "rewritten", "") or "")


async def _default_llm_invoke(question: str, model: Any) -> Any:
    from langchain_core.prompts import ChatPromptTemplate

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", _FORMALIZE_SYSTEM),
            ("human", _FORMALIZE_HUMAN),
        ]
    )
    chain = prompt | model.with_structured_output(FormalizedQuery)
    return await chain.ainvoke({"question": question})


async def formalize_rag_query(
    question: str,
    *,
    config: RagRewriteConfig | None = None,
    model: Any | None = None,
    structured_invoke: StructuredInvokeFn | None = None,
) -> str:
    """书面化用户问题；失败/超时/关闭时回退到轻量清洗或原句。"""
    cfg = config or app_config.rag_rewrite
    original = (question or "").strip()
    if not original:
        return ""

    if not cfg.formalize_enabled:
        return original

    light = light_normalize_query(original) or original

    try:
        invoke_fn: StructuredInvokeFn
        if structured_invoke is not None:
            invoke_fn = structured_invoke
        else:
            llm = model
            if llm is None:
                from app.chat.infrastructure.modeling.models import router_model

                llm = router_model

            async def _llm_invoke(q: str) -> Any:
                return await _default_llm_invoke(q, llm)

            invoke_fn = _llm_invoke

        result = await asyncio.wait_for(
            invoke_fn(light),
            timeout=max(0.1, float(cfg.timeout_seconds)),
        )
        rewritten = _clip(_extract_rewritten(result), cfg.max_chars)
        if rewritten:
            return rewritten
        return light
    except Exception as exc:
        logger.warning(
            "RAG 书面化改写失败，回退原问 | err=%s",
            exc,
            exc_info=True,
        )
        return light


__all__ = [
    "FormalizedQuery",
    "formalize_rag_query",
    "light_normalize_query",
]
