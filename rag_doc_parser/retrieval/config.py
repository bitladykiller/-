"""
检索模块配置。

定义 Milvus 连接、BM25 参数、RRF 参数、Reranker 参数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RetrievalConfig:
    """检索模块配置。

    可从环境变量初始化，也可直接传参。
    """

    # ------------------------------------------------------------------ #
    # Milvus 配置
    # ------------------------------------------------------------------ #
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection_name: str = "rag_documents"
    milvus_embedding_dim: int = 1024  # bge-m3 维度
    milvus_index_type: str = "IVF_FLAT"
    milvus_metric_type: str = "COSINE"
    milvus_nlist: int = 1024

    # ------------------------------------------------------------------ #
    # 向量检索参数
    # ------------------------------------------------------------------ #
    vector_top_k: int = 20  # 向量检索返回条数（给 RRF 用，取多一些）

    # ------------------------------------------------------------------ #
    # BM25 检索参数
    # ------------------------------------------------------------------ #
    bm25_top_k: int = 20  # BM25 检索返回条数
    bm25_k1: float = 1.5  # BM25 词频饱和参数
    bm25_b: float = 0.75  # BM25 文档长度归一化参数

    # ------------------------------------------------------------------ #
    # RRF 融合参数
    # ------------------------------------------------------------------ #
    rrf_k: int = 60  # RRF 常数 k（越大头部权重越低）
    rrf_final_top_k: int = 10  # RRF 融合后最终返回条数

    # ------------------------------------------------------------------ #
    # Reranker 参数
    # ------------------------------------------------------------------ #
    enable_rerank: bool = True
    rerank_top_k: int = 5  # rerank 后最终返回条数
    rerank_model: str = "bge-reranker-v2-m3"  # 默认使用 BGE Reranker

    # ------------------------------------------------------------------ #
    # 文本字段配置
    # ------------------------------------------------------------------ #
    text_field: str = "embedding_text"  # 用于 embedding 的字段
    display_field: str = "raw_text"  # 用于展示的字段
    sparse_field: str = "raw_text"  # 用于 BM25 的字段

    def __post_init__(self):
        if self.vector_top_k <= 0:
            raise ValueError("vector_top_k 必须 > 0")
        if self.bm25_top_k <= 0:
            raise ValueError("bm25_top_k 必须 > 0")
        if self.rrf_final_top_k <= 0:
            raise ValueError("rrf_final_top_k 必须 > 0")
        if self.rerank_top_k <= 0:
            raise ValueError("rerank_top_k 必须 > 0")
