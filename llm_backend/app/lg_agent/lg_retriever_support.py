"""`lg_retrievers.py` 共享的纯 helper。

这个模块负责：
- 检索结果 records 的统一收口
- Text2Cypher / RAG 原始结果的标准化
- Milvus 文档片段字段提取与降级记录构造

这个模块不负责：
- 注册检索器
- 持有运行时单例
- 调用具体检索后端
"""

from __future__ import annotations

from typing import Any


def coerce_records(records: Any) -> list[dict[str, Any]]:
    """将检索结果中的 `records` 统一为 `list[dict]`。"""
    if records is None:
        return []
    if isinstance(records, list):
        return records
    if isinstance(records, dict):
        return [records] if records else []
    return [{"value": records}]


def normalize_retriever_result(
    payload: dict[str, Any],
    *,
    task: str,
) -> dict[str, Any]:
    """将不同后端的原始结果归一化为统一结构。"""
    if "records" in payload:
        records = coerce_records(payload.get("records"))
    else:
        records: list[dict[str, Any]] = []
        for cypher in payload.get("cyphers", []):
            records.extend(coerce_records(cypher.get("records")))

    return {
        "task": task,
        "records": records,
        "errors": payload.get("errors", []),
        "steps": payload.get("steps", []),
        "raw": payload,
    }


def build_milvus_doc_record(result: dict[str, Any]) -> dict[str, Any]:
    """提取 Agent 真正消费的文档片段字段。"""
    return {
        "chunk_type": result.get("chunk_type", "text"),
        "section_path": result.get("section_path", ""),
        "source_file": result.get("source_file", ""),
        "raw_text": result.get("raw_text", ""),
        "rrf_score": result.get("rrf_score"),
        "rerank_score": result.get("rerank_score"),
    }


def build_milvus_doc_fallback_record(message: str) -> list[dict[str, str]]:
    """统一构造检索失败时的降级记录。"""
    return [{"message": message}]
