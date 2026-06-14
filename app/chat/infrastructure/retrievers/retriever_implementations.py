"""检索层具体实现。

职责：
- 提供 Milvus 文档检索器实现
- 提供 Neo4j 知识图谱检索器实现

边界：
- 不承载注册表单例管理
- 不承载包级兼容导出
"""

from __future__ import annotations

from typing import Any

from app.chat.infrastructure.retrievers.retriever_contracts import (
    RAG_SEARCH_STEP,
    Retriever,
)


def _coerce_records(records: Any) -> list[dict[str, Any]]:
    """将检索结果中的 `records` 统一为 `list[dict]`。"""
    if records is None:
        return []
    if isinstance(records, list):
        return records
    if isinstance(records, dict):
        return [records] if records else []
    return [{"value": records}]


class MilvusDocRetriever(Retriever):
    """基于 rag_doc_parser + Milvus 的文档检索器。"""

    def __init__(self) -> None:
        from rag_doc_parser.retrieval.config import RetrievalConfig
        from rag_doc_parser.retrieval.hybrid_search import HybridSearcher

        self._searcher = HybridSearcher(RetrievalConfig())

    async def search(self, task: str) -> dict[str, Any]:
        """检索 Milvus 文档知识库。"""

        errors: list[str] = []
        try:
            results = await self._searcher.search(task)
            records = (
                [
                    {
                        "chunk_type": result.get("chunk_type", "text"),
                        "section_path": result.get("section_path", ""),
                        "source_file": result.get("source_file", ""),
                        "raw_text": result.get("raw_text", ""),
                        "rrf_score": result.get("rrf_score"),
                        "rerank_score": result.get("rerank_score"),
                    }
                    for result in results[:5]
                ]
                if results
                else []
            )
        except ImportError:
            records = [{"message": "文档检索模块未安装。请先上传文档建立知识库。"}]
            errors.append("rag_doc_parser 模块未安装")
        except Exception as exc:
            records = [{"message": "文档检索暂时不可用。"}]
            errors.append(str(exc))

        return {
            "task": task,
            "records": records,
            "errors": errors,
            "steps": [RAG_SEARCH_STEP],
        }


class KnowledgeGraphRetriever(Retriever):
    """基于 Neo4j + Text2Cypher 的知识图谱检索器。"""

    def __init__(self, t2c_agent: Any) -> None:
        self._t2c_agent = t2c_agent

    async def search(self, task: str) -> dict[str, Any]:
        """查询 Neo4j 知识图谱。"""

        raw_result = await self._t2c_agent.ainvoke({"task": task})
        if "records" in raw_result:
            records = _coerce_records(raw_result.get("records"))
        else:
            records: list[dict[str, Any]] = []
            for cypher in raw_result.get("cyphers", []):
                records.extend(_coerce_records(cypher.get("records")))

        return {
            "task": task,
            "records": records,
            "errors": raw_result.get("errors", []),
            "steps": raw_result.get("steps", []),
            "raw": raw_result,
        }


__all__ = [
    "KnowledgeGraphRetriever",
    "MilvusDocRetriever",
]
