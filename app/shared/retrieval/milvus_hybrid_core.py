"""共享的 Milvus 混合检索核心。

被以下两条链路复用：
- knowledge doc_parser 的文档检索
- knowledge ltm 的长期记忆检索

这个模块刻意不认识任何领域 schema（DocumentChunk / LongTermMemory）。
调用方自己传 output_fields，并把返回的 entity 映射成各自的业务对象。

所有对 pymilvus 客户端和 embedding 模型的调用都经过 `run_blocking`——
这两个都是同步 SDK，直接在协程里调会阻塞整个事件循环，原因见 async_bridge 模块。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from app.shared.core.async_bridge import run_blocking
from pymilvus import AnnSearchRequest, MilvusClient, RRFRanker

logger = logging.getLogger(__name__)

QUERY_LOG_PREVIEW_LIMIT = 100
EMBEDDING_LOG_PREVIEW_LIMIT = 200
_SPARSE_TOKEN_ID_SPACE = 2**24


@lru_cache(maxsize=4)
def _get_sparse_analyzer(language: str) -> Any:
    """按语言缓存 BM25 分词器。

    WHY 缓存：`build_default_analyzer` 每次调用都会重新构建分词器
    （中文analyzer 会加载词典），而它对同一语言是完全无状态的纯配置对象。
    之前每个查询都重建一次，白白付出词典加载成本。

    Raises:
        ImportError: pymilvus 未安装稀疏检索扩展时由调用方降级处理。
    """
    from pymilvus.model.sparse.bm25.tokenizers import (  # pyright: ignore[reportMissingImports]
        build_default_analyzer,
    )

    return build_default_analyzer(language=language)


class MilvusHybridSearchCore:
    """Milvus dense / hybrid 检索核心。

    职责：
    - 生成 dense 查询向量（单条与批量）
    - 生成与 Milvus BM25 Function 兼容的 sparse 查询向量
    - 执行 dense 检索与 dense + sparse 原生 hybrid 检索
    - 把原始命中归一化成 `{"score": float, "entity": dict}`
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
        sparse_language: str = "zh",
    ) -> None:
        self.milvus_client = milvus_client
        self.embedding_model = embedding_model
        self.collection_name = collection_name
        self.dense_field = dense_field
        self.sparse_field = sparse_field
        self.dense_metric_type = dense_metric_type
        self.dense_search_params = dense_search_params or {"nprobe": 16}
        self.hybrid_rrf_k = hybrid_rrf_k
        self.sparse_language = sparse_language

    # ------------------------------------------------------------------ #
    # 向量生成
    # ------------------------------------------------------------------ #

    async def embed_query(self, text: str) -> list[float] | None:
        """生成单条查询的 dense 向量，失败返回 None。"""
        try:
            return await run_blocking(self.embedding_model.embed_query, text)
        except Exception as exc:  # pragma: no cover - defensive path
            logger.error(
                "embedding generation failed | text_preview=%s | %s",
                text[:EMBEDDING_LOG_PREVIEW_LIMIT],
                exc,
                exc_info=True,
            )
            return None

    async def embed_documents(self, texts: list[str]) -> list[list[float]] | None:
        """批量生成 dense 向量，失败返回 None。

        WHY 单独提供批量接口：索引一篇文档会产生几十到几百个 chunk。
        逐条 `embed_query` 意味着同样多次模型前向 / HTTP 往返；
        `embed_documents` 让 sentence-transformers 走一次批推理、
        让 Ollama 走一次批请求，是数量级的差异。

        对没有实现 `embed_documents` 的自定义模型自动回退到逐条 `embed_query`，
        保证鸭子类型的注入点（含测试 fake）不被破坏。
        """
        if not texts:
            return []
        try:
            batch_encode = getattr(self.embedding_model, "embed_documents", None)
            if callable(batch_encode):
                return await run_blocking(batch_encode, texts)
            return await run_blocking(
                lambda: [self.embedding_model.embed_query(text) for text in texts]
            )
        except Exception as exc:  # pragma: no cover - defensive path
            logger.error(
                "batch embedding generation failed | count=%s | first_preview=%s | %s",
                len(texts),
                texts[0][:EMBEDDING_LOG_PREVIEW_LIMIT],
                exc,
                exc_info=True,
            )
            return None

    def encode_query_sparse(self, query: str) -> dict[int, float]:
        """把查询编码成 Milvus BM25 期望的稀疏向量格式。

        分词器不可用时返回空 dict，调用方据此降级为纯 dense 检索。
        """
        try:
            analyzer = _get_sparse_analyzer(self.sparse_language)
        except ImportError:
            logger.warning("pymilvus sparse analyzer unavailable, sparse query disabled")
            return {}
        except Exception as exc:  # pragma: no cover - defensive path
            logger.error("sparse analyzer init failed | %s", exc, exc_info=True)
            return {}

        try:
            sparse: dict[int, float] = {}
            for token in analyzer(query):
                token_id = abs(hash(token)) % _SPARSE_TOKEN_ID_SPACE
                sparse[token_id] = sparse.get(token_id, 0.0) + 1.0
            return sparse
        except Exception as exc:  # pragma: no cover - defensive path
            logger.error(
                "sparse query encoding failed | query_preview=%s | %s",
                query[:QUERY_LOG_PREVIEW_LIMIT],
                exc,
                exc_info=True,
            )
            return {}

    # ------------------------------------------------------------------ #
    # 检索
    # ------------------------------------------------------------------ #

    async def search_dense(
        self,
        query: str,
        *,
        limit: int,
        filter_expr: str | None = None,
        output_fields: list[str] | None = None,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """纯 dense 检索并归一化命中。"""
        query_vector = await self.embed_query(query)
        if not query_vector:
            return []

        raw = await run_blocking(
            self.milvus_client.search,
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
        """Milvus 原生 hybrid 检索（dense + BM25 sparse，RRF 融合）。

        稀疏编码不可用或 hybrid 调用失败时，自动降级为纯 dense 检索。
        """
        query_vector = await self.embed_query(query)
        if not query_vector:
            return []

        search_limit = search_limit or limit
        sparse_query = self.encode_query_sparse(query)
        if sparse_query:
            try:
                raw = await run_blocking(
                    self.milvus_client.hybrid_search,
                    collection_name=self.collection_name,
                    reqs=[
                        self._build_dense_request(query_vector, search_limit, filter_expr),
                        AnnSearchRequest(
                            data=[sparse_query],
                            anns_field=self.sparse_field,
                            param={"metric_type": "BM25"},
                            limit=search_limit,
                            expr=filter_expr,
                        ),
                    ],
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

    def _build_dense_request(
        self,
        query_vector: list[float],
        search_limit: int,
        filter_expr: str | None,
    ) -> AnnSearchRequest:
        """构造 hybrid 检索中的 dense 分支请求。"""
        return AnnSearchRequest(
            data=[query_vector],
            anns_field=self.dense_field,
            param={
                "metric_type": self.dense_metric_type,
                "params": self.dense_search_params,
            },
            limit=search_limit,
            expr=filter_expr,
        )

    def _normalize_hits(
        self,
        raw_results: Any,
        *,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """把 Milvus 命中归一化成 `{"score": float, "entity": dict}`。"""
        if not raw_results or not raw_results[0]:
            return []

        normalized: list[dict[str, Any]] = []
        for item in raw_results[0]:
            score = float(item.get("distance", 0.0))
            if score_threshold is not None and score < score_threshold:
                continue
            normalized.append({"score": score, "entity": item.get("entity", {})})
        return normalized


__all__ = ["MilvusHybridSearchCore"]
