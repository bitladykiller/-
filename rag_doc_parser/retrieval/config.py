"""
检索模块配置。

定义 Milvus 混合检索与 Reranker 参数。
"""

from dataclasses import dataclass


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
    vector_top_k: int = 20  # dense 检索候选条数

    # ------------------------------------------------------------------ #
    # 稀疏检索参数
    # ------------------------------------------------------------------ #
    bm25_top_k: int = 20  # sparse 检索候选条数

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
    display_field: str = "raw_text"  # 用于展示的字段

    def __post_init__(self):
        if self.vector_top_k <= 0:
            raise ValueError("vector_top_k 必须 > 0")
        if self.bm25_top_k <= 0:
            raise ValueError("bm25_top_k 必须 > 0")
        if self.rrf_final_top_k <= 0:
            raise ValueError("rrf_final_top_k 必须 > 0")
        if self.rerank_top_k <= 0:
            raise ValueError("rerank_top_k 必须 > 0")
