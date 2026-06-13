"""长期记忆运行时样板 helper。

这个模块负责：
- 创建默认的 Milvus 混合检索核心
- 初始化长期记忆 collection 并输出统一提示
- dense / hybrid 检索调用样板
- 去重检索与待合并聚类加载

这个模块不负责：
- 决定何时保存、软删或合并记忆
- 生成 embedding
- 处理长期记忆的业务级降级策略
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from app.memory.ltm_collection import (
    ensure_memory_collection,
    query_records,
    search_records,
)
from app.memory.ltm_store_utils import (
    MilvusRecord,
    cluster_memory_records,
    has_dedup_match,
    search_results_from_hits,
)
from app.memory.schemas import MemorySearchResult

if TYPE_CHECKING:
    from shared_retrieval import MilvusHybridSearchCore


def create_default_retrieval_core(
    *,
    milvus_client: Any,
    embedding_model: Any,
    collection_name: str,
) -> MilvusHybridSearchCore:
    """创建默认的 Milvus 混合检索核心。"""
    from shared_retrieval import MilvusHybridSearchCore

    return MilvusHybridSearchCore(
        milvus_client=milvus_client,
        embedding_model=embedding_model,
        collection_name=collection_name,
        dense_field="embedding",
        sparse_field="sparse_vector",
        dense_metric_type="COSINE",
        dense_search_params={"nprobe": 16},
        hybrid_rrf_k=60,
    )


def ensure_collection_ready(
    *,
    milvus_client: Any,
    collection_name: str,
    logger: Any,
) -> None:
    """初始化长期记忆 collection，不存在时自动创建。"""
    created = ensure_memory_collection(
        milvus_client,
        collection_name,
    )
    if not created:
        logger.info(f"Collection {collection_name} 已存在")
        return
    logger.info(f"Collection {collection_name} 创建成功（含 BM25 全文索引）")


async def search_dense_memories(
    *,
    retrieval_core: Any,
    query: str,
    top_k: int,
    filter_expr: str,
    output_fields: list[str],
    score_threshold: float,
) -> list[MemorySearchResult]:
    """执行 dense 检索并转换成统一结果结构。"""
    hits = await retrieval_core.search_dense(
        query,
        limit=top_k,
        filter_expr=filter_expr,
        output_fields=output_fields,
        score_threshold=score_threshold,
    )
    return search_results_from_hits(hits)


async def search_hybrid_memories(
    *,
    retrieval_core: Any,
    query: str,
    top_k: int,
    filter_expr: str,
    output_fields: list[str],
    score_threshold: float,
    search_limit_multiplier: int,
) -> list[MemorySearchResult]:
    """执行 hybrid 检索并转换成统一结果结构。"""
    hits = await retrieval_core.search_hybrid(
        query,
        limit=top_k,
        filter_expr=filter_expr,
        output_fields=output_fields,
        score_threshold=score_threshold,
        search_limit=top_k * search_limit_multiplier,
    )
    return search_results_from_hits(hits)


def should_insert_memory(
    *,
    milvus_client: Any,
    collection_name: str,
    embedding: list[float],
    filter_expr: str,
    top_k: int,
    output_fields: list[str],
    similarity_threshold: float,
) -> bool:
    """执行去重检索，返回是否应继续插入新长期记忆。"""
    results = search_records(
        milvus_client,
        collection_name,
        embedding,
        filter_expr,
        limit=top_k,
        output_fields=output_fields,
    )
    return not has_dedup_match(results, similarity_threshold)


def load_merge_clusters(
    *,
    milvus_client: Any,
    collection_name: str,
    filter_expr: str,
    output_fields: list[str],
    similarity_threshold: float,
) -> list[list[MilvusRecord]]:
    """查询活跃记忆并按 embedding 相似度聚类。"""
    results = query_records(
        milvus_client,
        collection_name,
        filter_expr,
        output_fields,
    )
    if not results or len(results) < 2:
        return []
    return cluster_memory_records(results, similarity_threshold)
