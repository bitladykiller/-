"""
混合检索主控 — 向量检索 + BM25 检索 → RRF 融合 → Reranker。

统一入口：hybrid_search(query) → List[Dict]
索引支持 create（index）与 replace（reindex，策略 2 软删+version）。
"""

from __future__ import annotations

import logging
from typing import Any

from app.knowledge.infrastructure.doc_parser.retrieval.config import RetrievalConfig
from app.knowledge.infrastructure.doc_parser.retrieval.milvus_store import MilvusStore
from app.knowledge.infrastructure.doc_parser.retrieval.rrf import Reranker

logger = logging.getLogger(__name__)


class HybridSearcher:
    """混合检索引擎。

    基于 Milvus 原生 hybrid search 做 dense + sparse 召回，
    再按需接入 Reranker 精排。

    用法:
        searcher = HybridSearcher(config, embedding_model)
        await searcher.index(chunks)
        await searcher.reindex(doc_id, chunks)
        results = await searcher.search("查询文本")
    """

    def __init__(
        self,
        config: RetrievalConfig | None = None,
        embedding_model=None,
    ):
        self.config = config or RetrievalConfig()
        self.milvus = MilvusStore(self.config, embedding_model)
        self.reranker = Reranker(self.config.rerank_model) if self.config.enable_rerank else None

    # ------------------------------------------------------------------ #
    # 索引
    # ------------------------------------------------------------------ #

    async def index(
        self,
        chunks: list[Any],
        *,
        version: int = 1,
        content_hash: str = "",
    ) -> int:
        """将 DocumentChunk 列表写入 Milvus 检索集合（新建）。"""
        count = await self.milvus.insert_chunks(
            chunks,
            version=version,
            content_hash=content_hash,
        )
        logger.info("混合索引完成: %s 条记录 | version=%s", count, version)
        return count

    async def reindex(
        self,
        doc_id: str,
        chunks: list[Any],
        *,
        content_hash: str = "",
    ) -> dict[str, int]:
        """文档动态更新：软删旧 chunk，再写入新 version。"""
        result = await self.milvus.reindex_document(
            doc_id,
            chunks,
            content_hash=content_hash,
        )
        logger.info(
            "混合 reindex 完成 | doc_id=%s soft_deleted=%s version=%s chunks=%s",
            doc_id,
            result.get("soft_deleted"),
            result.get("version"),
            result.get("chunks"),
        )
        return result

    async def soft_delete_document(self, doc_id: str) -> dict[str, int]:
        """仅软删除文档（不写入新版）。"""
        return self.milvus.soft_delete_by_doc_id(doc_id)

    def hard_purge_soft_deleted(
        self,
        *,
        retention_seconds: int = 7 * 24 * 3600,
        batch_limit: int = 16384,
    ) -> int:
        """物理删除过期软删 chunk。"""
        return self.milvus.hard_purge_soft_deleted(
            retention_seconds=retention_seconds,
            batch_limit=batch_limit,
        )

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #

    async def search(
        self,
        query: str,
        top_k: int | None = None,
        filter_expr: str | None = None,
    ) -> list[dict[str, Any]]:
        """混合检索。

        流程:
        1. Milvus 原生 hybrid_search（向量 + BM25 + RRF），默认排除软删
        2. Reranker 重排序（可选）→ final results
        """
        final_top_k = top_k or self.config.rrf_final_top_k

        fused = await self.milvus.hybrid_search(
            query,
            top_k=max(
                final_top_k,
                self.config.rerank_top_k if self.reranker else final_top_k,
            ),
            filter_expr=filter_expr,
        )

        if self.reranker and self.reranker.available:
            fused = self.reranker.rerank(
                query,
                fused,
                top_k=self.config.rerank_top_k,
                text_field=self.config.display_field,
            )

        return fused[:final_top_k]
