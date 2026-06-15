"""检索层具体实现。

职责：
- 提供 Milvus 文档检索器实现
- 提供 Neo4j 知识图谱检索器实现

边界：
- 不承载注册表单例管理
- 不承载包级兼容导出
"""

from typing import Any

from app.chat.infrastructure.retrievers.retriever_contracts import (
    Retriever,
)


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
            records = [
                {
                    "chunk_type": result["chunk_type"],
                    "section_path": result["section_path"],
                    "source_file": result["source_file"],
                    "raw_text": result["raw_text"],
                    "rrf_score": result["rrf_score"],
                    "rerank_score": result.get("rerank_score"),
                }
                for result in results[:5]
            ]
        except Exception as exc:
            records = [{"message": "文档检索暂时不可用。"}]
            errors.append(str(exc))

        return {
            "task": task,
            "records": records,
            "errors": errors,
            "steps": ["execute_rag_search"],
        }


class KnowledgeGraphRetriever(Retriever):
    """基于 Neo4j + Text2Cypher 的知识图谱检索器。"""

    def __init__(self, t2c_agent: Any) -> None:
        self._t2c_agent = t2c_agent

    async def search(self, task: str) -> dict[str, Any]:
        """查询 Neo4j 知识图谱。"""

        raw_result = await self._t2c_agent.ainvoke({"task": task})
        cyphers = raw_result["cyphers"]
        records = [record for cypher in cyphers for record in cypher["records"]]
        errors = [error for cypher in cyphers for error in cypher["errors"]]

        return {
            "task": task,
            "records": records,
            "errors": errors,
            "steps": raw_result["steps"],
        }
