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


class MilvusDocRetriever(Retriever):
    """基于 doc_parser + Milvus 的文档检索器。"""

    def __init__(self) -> None:
        from app.knowledge.infrastructure.doc_parser.retrieval.config import RetrievalConfig
        from app.knowledge.infrastructure.doc_parser.retrieval.hybrid_search import HybridSearcher

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
            errors.append("app.knowledge.infrastructure.doc_parser 模块未安装")
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
        records: list[dict[str, Any]] = []
        if "records" in raw_result:
            raw_records = raw_result.get("records")
            if raw_records is None:
                records = []
            elif isinstance(raw_records, list):
                records = raw_records
            elif isinstance(raw_records, dict):
                records = [raw_records] if raw_records else []
            else:
                records = [{"value": raw_records}]
        else:
            for cypher in raw_result.get("cyphers", []):
                cypher_records = cypher.get("records")
                if cypher_records is None:
                    continue
                if isinstance(cypher_records, list):
                    records.extend(cypher_records)
                elif isinstance(cypher_records, dict):
                    if cypher_records:
                        records.append(cypher_records)
                else:
                    records.append({"value": cypher_records})

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
