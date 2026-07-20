"""Shared Milvus hybrid retrieval core.

This module centralizes the common retrieval mechanics used by:
- knowledge doc_parser document retrieval
- llm_backend long-term memory retrieval

It intentionally does not know anything about domain-specific schemas such as
DocumentChunk or LongTermMemory. Callers provide output fields and map the
returned entities into their own business objects.
"""

from __future__ import annotations

import logging
from typing import Any

from pymilvus import AnnSearchRequest, MilvusClient, RRFRanker

logger = logging.getLogger(__name__)


class MilvusHybridSearchCore:
    """Shared Milvus dense/hybrid search core.

    Responsibilities:
    - generate dense query embeddings
    - generate sparse BM25 query vectors compatible with Milvus Function BM25
    - execute dense search
    - execute Milvus native hybrid search with dense + sparse retrieval
    - normalize raw Milvus hits into a stable shape
    """

    def __init__(
        self,
        milvus_client: MilvusClient,
        embedding_model: Any,
        collection_name: str,
        *,
        dense_field: str = "embedding",
        sparse_field: str = "sparse_vector",
        dense_metric_type: str = "COSINE",
        dense_search_params: dict[str, Any] | None = None,
        hybrid_rrf_k: int = 60,
    ) -> None:
        self.milvus_client = milvus_client
        self.embedding_model = embedding_model
        self.collection_name = collection_name
        self.dense_field = dense_field
        self.sparse_field = sparse_field
        self.dense_metric_type = dense_metric_type
        self.dense_search_params = dense_search_params or {"nprobe": 16}
        self.hybrid_rrf_k = hybrid_rrf_k

    async def embed_query(self, text: str) -> list[float] | None:
        """Generate a dense embedding for a query string."""
        try:
            return self.embedding_model.embed_query(text)
        except Exception as exc:  # pragma: no cover - defensive path
            logger.error(
                "embedding generation failed | text_preview=%s | %s",
                text[:200],
                exc,
                exc_info=True,
            )
            return None

    def encode_query_sparse(self, query: str) -> dict[int, float]:
        """Encode a query into the sparse BM25 vector format expected by Milvus."""
        try:
            from pymilvus.model.sparse.bm25.tokenizers import (  # pyright: ignore[reportMissingImports]
                build_default_analyzer,
            )

            analyzer = build_default_analyzer(language="zh")
            tokens = analyzer(query)

            sparse: dict[int, float] = {}
            for token in tokens:
                token_id = abs(hash(token)) % (2**24)
                sparse[token_id] = sparse.get(token_id, 0.0) + 1.0
            return sparse
        except ImportError:
            logger.warning("pymilvus sparse analyzer unavailable, sparse query disabled")
            return {}
        except Exception as exc:  # pragma: no cover - defensive path
            logger.error(
                "sparse query encoding failed | query_preview=%s | %s",
                query[:100],
                exc,
                exc_info=True,
            )
            return {}

    async def search_dense(
        self,
        query: str,
        *,
        limit: int,
        filter_expr: str | None = None,
        output_fields: list[str] | None = None,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """Run dense-only Milvus search and normalize hits."""
        query_vector = await self.embed_query(query)
        if not query_vector:
            return []

        raw = self.milvus_client.search(
            collection_name=self.collection_name,
            data=[query_vector],
            filter=filter_expr or "",
            limit=limit,
            output_fields=output_fields or [],
        )
        return self._normalize_hits(raw, score_threshold=score_threshold)

    async def search_hybrid(
        self,
        query: str,
        *,
        limit: int,
        filter_expr: str | None = None,
        output_fields: list[str] | None = None,
        score_threshold: float | None = None,
        search_limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Run native Milvus hybrid search with dense + BM25 sparse retrieval.

        Falls back to dense-only search when sparse encoding or hybrid search is
        not available.
        """
        query_vector = await self.embed_query(query)
        if not query_vector:
            return []

        search_limit = search_limit or limit
        dense_req = AnnSearchRequest(
            data=[query_vector],
            anns_field=self.dense_field,
            param={
                "metric_type": self.dense_metric_type,
                "params": self.dense_search_params,
            },
            limit=search_limit,
            expr=filter_expr,
        )

        sparse_query = self.encode_query_sparse(query)
        if sparse_query:
            try:
                sparse_req = AnnSearchRequest(
                    data=[sparse_query],
                    anns_field=self.sparse_field,
                    param={"metric_type": "BM25"},
                    limit=search_limit,
                    expr=filter_expr,
                )
                raw = self.milvus_client.hybrid_search(
                    collection_name=self.collection_name,
                    reqs=[dense_req, sparse_req],
                    ranker=RRFRanker(k=self.hybrid_rrf_k),
                    limit=limit,
                    output_fields=output_fields or [],
                )
                return self._normalize_hits(raw, score_threshold=score_threshold)
            except Exception as exc:
                logger.warning(
                    "milvus hybrid_search failed, fallback to dense | collection=%s | %s",
                    self.collection_name,
                    exc,
                    exc_info=True,
                )
        return await self.search_dense(
            query,
            limit=limit,
            filter_expr=filter_expr,
            output_fields=output_fields,
            score_threshold=score_threshold,
        )

    def _normalize_hits(
        self,
        raw_results: Any,
        *,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """Normalize Milvus hits into {'score': float, 'entity': dict}."""
        if not raw_results or not raw_results[0]:
            return []

        normalized: list[dict[str, Any]] = []
        for item in raw_results[0]:
            score = float(item.get("distance", 0.0))
            if score_threshold is not None and score < score_threshold:
                continue
            normalized.append(
                {
                    "score": score,
                    "entity": item.get("entity", {}),
                }
            )
        return normalized
