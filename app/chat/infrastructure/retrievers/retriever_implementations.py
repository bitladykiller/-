"""检索层具体实现。

职责：
- 提供 Milvus 文档检索器实现（含 RAG 书面化改写）
- 提供 Neo4j 知识图谱检索器实现

边界：
- 不承载注册表单例管理
- 不承载包级兼容导出
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.chat.infrastructure.retrievers.rag_query_formalize import formalize_rag_query
from app.chat.infrastructure.retrievers.retriever_contracts import (
    RAG_SEARCH_STEP,
    Retriever,
)
from app.shared.core.logger import get_logger

logger = get_logger(__name__)

# 可注入：async (question) -> rewritten
FormalizeFn = Callable[[str], Awaitable[str]]

#: 回传给 LLM 的文档片段上限。再多会挤占上下文窗口且边际收益递减。
RAG_MAX_RECORDS = 5
#: 日志里问句预览的截断长度
_QUERY_LOG_PREVIEW = 80


class MilvusDocRetriever(Retriever):
    """基于 doc_parser + Milvus 的文档检索器。

    检索前默认做书面化改写（可配置关闭）；图谱检索器不做改写。
    """

    def __init__(
        self,
        *,
        formalize_fn: FormalizeFn | None = None,
        formalize_enabled: bool | None = None,
        searcher: Any | None = None,
    ) -> None:
        from app.knowledge.infrastructure.doc_parser.retrieval.hybrid_search import (
            get_shared_searcher,
        )
        from app.shared.core.app_config import app_config

        # 与索引侧共用同一个进程内实例，避免重复建 Milvus 连接和重载 embedding 模型
        self._searcher = searcher if searcher is not None else get_shared_searcher()
        self._formalize_fn = formalize_fn
        self._formalize_enabled = (
            formalize_enabled
            if formalize_enabled is not None
            else app_config.rag_rewrite.formalize_enabled
        )

    async def _rewrite_for_rag(self, task: str) -> str:
        """书面化；关闭或失败时用原句。"""
        text = (task or "").strip()
        if not text or not self._formalize_enabled:
            return text
        try:
            if self._formalize_fn is not None:
                rewritten = await self._formalize_fn(text)
            else:
                rewritten = await formalize_rag_query(text)
            rewritten = (rewritten or "").strip()
            if rewritten and rewritten != text:
                logger.info(
                    "RAG 书面化 | original=%s | rewritten=%s",
                    text[:_QUERY_LOG_PREVIEW],
                    rewritten[:_QUERY_LOG_PREVIEW],
                )
            return rewritten or text
        except Exception as exc:
            logger.warning("RAG 书面化异常，使用原问 | %s", exc, exc_info=True)
            return text

    async def search(self, task: str) -> dict[str, Any]:
        """检索前书面化，再查 Milvus 文档知识库。"""

        errors: list[str] = []
        original = (task or "").strip()
        query = await self._rewrite_for_rag(original)
        try:
            results = await self._searcher.search(query)
            records = [
                {
                    "chunk_type": result.get("chunk_type", "text"),
                    "section_path": result.get("section_path", ""),
                    "source_file": result.get("source_file", ""),
                    "raw_text": result.get("raw_text", ""),
                    "rrf_score": result.get("rrf_score"),
                    "rerank_score": result.get("rerank_score"),
                }
                for result in (results or [])[:RAG_MAX_RECORDS]
            ]
        except ImportError:
            records = [{"message": "文档检索模块未安装。请先上传文档建立知识库。"}]
            errors.append("app.knowledge.infrastructure.doc_parser 模块未安装")
        except Exception as exc:
            records = [{"message": "文档检索暂时不可用。"}]
            errors.append(str(exc))

        payload: dict[str, Any] = {
            "task": original,
            "records": records,
            "errors": errors,
            "steps": [RAG_SEARCH_STEP],
        }
        # 便于排障：仅当改写生效时附带
        if query and query != original:
            payload["rewritten_query"] = query
        return payload


class KnowledgeGraphRetriever(Retriever):
    """基于 Neo4j + Text2Cypher 的知识图谱检索器。"""

    def __init__(self, t2c_agent: Any) -> None:
        self._t2c_agent = t2c_agent

    async def search(self, task: str) -> dict[str, Any]:
        """查询 Neo4j 知识图谱（不做 RAG 书面化）。"""

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
