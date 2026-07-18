"""
混合检索主控 — 向量检索 + BM25 检索 → RRF 融合 → Reranker。

统一入口：hybrid_search(query) → List[Dict]
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

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
        searcher.index(chunks)
        results = await searcher.search("查询文本")
    """

    def __init__(
        self,
        config: Optional[RetrievalConfig] = None,
        embedding_model=None,
    ):
        self.config = config or RetrievalConfig()
        self.milvus = MilvusStore(self.config, embedding_model)
        self.reranker = Reranker(self.config.rerank_model) if self.config.enable_rerank else None

    # ------------------------------------------------------------------ #
    # 索引
    # ------------------------------------------------------------------ #

    async def index(self, chunks: List[Any]) -> int:
        """将 DocumentChunk 列表写入 Milvus 检索集合。

        Args:
            chunks: DocumentChunk 列表。

        Returns:
            成功索引的数量。
        """
        # Milvus 向量索引
        count = await self.milvus.insert_chunks(chunks)

        logger.info(f"混合索引完成: {count} 条记录")
        return count

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #

    async def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """混合检索。

        流程:
        1. Milvus 原生 hybrid_search（向量 + BM25 + RRF）
        2. Reranker 重排序（可选）→ final results

        Args:
            query: 查询文本。
            top_k: 最终返回条数（覆盖 config.rrf_final_top_k）。
            filter_expr: Milvus 过滤表达式。

        Returns:
            排序后的检索结果列表。
        """
        final_top_k = top_k or self.config.rrf_final_top_k

        fused = await self.milvus.hybrid_search(
            query,
            top_k=max(final_top_k, self.config.rerank_top_k if self.reranker else final_top_k),
            filter_expr=filter_expr,
        )

        # Reranker 重排序
        if self.reranker and self.reranker.available:
            fused = self.reranker.rerank(
                query, fused,
                top_k=self.config.rerank_top_k,
                text_field=self.config.display_field,
            )

        return fused[:final_top_k]
