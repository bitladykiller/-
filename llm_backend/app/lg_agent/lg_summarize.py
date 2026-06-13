"""检索结果摘要器。

将 KG / RAG 检索到的结构化 `records` 统一交给摘要节点处理，
避免该职责混入 `lg_retrievers.py` 的检索器注册与适配逻辑中。
"""
from __future__ import annotations

from typing import Any

_summarize_node = None


async def summarize_records(
    question: str,
    records: list[dict[str, Any]],
    fallback: str = "未查询到相关信息～",
) -> str:
    """根据检索结果生成摘要，空结果时直接返回 fallback。"""
    if not records:
        return fallback

    global _summarize_node
    if _summarize_node is None:
        from app.lg_agent.lg_models import cypher_model
        from app.lg_agent.kg_sub_graph.agentic_rag_agents.components.summarize import (
            create_summarization_node,
        )

        _summarize_node = create_summarization_node(llm=cypher_model)

    result = await _summarize_node.ainvoke({
        "question": question,
        "cyphers": [{"records": records}],
    })
    return result.get("summary", "") or fallback
