"""
混合检索主控 — 向量检索 + BM25 检索 → RRF 融合 → Reranker。

统一入口：hybrid_search(query) → List[Dict]
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from rag_doc_parser.retrieval.config import RetrievalConfig
from rag_doc_parser.retrieval.milvus_store import MilvusStore
from rag_doc_parser.retrieval.bm25_index import BM25Index
from rag_doc_parser.retrieval.rrf import rrf_fusion, Reranker

logger = logging.getLogger(__name__)


class HybridSearcher:
    """混合检索引擎。

    整合向量检索（Milvus）和关键词检索（BM25），
    通过 RRF 融合后可选 Reranker 精排。

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
        self.bm25 = BM25Index(self.config)
        self.reranker = Reranker(self.config.rerank_model) if self.config.enable_rerank else None
        self._indexed = False

    # ------------------------------------------------------------------ #
    # 索引
    # ------------------------------------------------------------------ #

    async def index(self, chunks: List[Any]) -> int:
        """将 DocumentChunk 列表同时写入 Milvus 和 BM25 索引。

        Args:
            chunks: DocumentChunk 列表。

        Returns:
            成功索引的数量。
        """
        # Milvus 向量索引
        count = await self.milvus.insert_chunks(chunks)

        # BM25 关键词索引
        self.bm25.add_documents(chunks)

        self._indexed = True
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
        1. Milvus 向量检索 → vector_results
        2. BM25 关键词检索 → bm25_results
        3. RRF 融合 → merged results
        4. Reranker 重排序（可选）→ final results

        Args:
            query: 查询文本。
            top_k: 最终返回条数（覆盖 config.rrf_final_top_k）。
            filter_expr: Milvus 过滤表达式。

        Returns:
            排序后的检索结果列表。
        """
        final_top_k = top_k or self.config.rrf_final_top_k

        # 1. 向量检索
        vector_results = await self.milvus.search(
            query, top_k=self.config.vector_top_k, filter_expr=filter_expr
        )

        # 2. BM25 检索
        bm25_results = self.bm25.search(query, top_k=self.config.bm25_top_k)

        # 3. RRF 融合
        fused = rrf_fusion(
            vector_results,
            bm25_results,
            k=self.config.rrf_k,
            top_k=final_top_k,
        )

        # 4. Reranker 重排序
        if self.reranker and self.reranker.available:
            fused = self.reranker.rerank(
                query, fused,
                top_k=self.config.rerank_top_k,
                text_field=self.config.display_field,
            )

        return fused

    def clear(self):
        """清空 BM25 索引（Milvus 数据保留）。"""
        self.bm25.clear()
        self._indexed = False
