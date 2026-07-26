"""
检索模块配置。

定义 Milvus 混合检索与 Reranker 参数。
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _default_milvus_host() -> str:
    """从全局 settings 取 Milvus 主机名。"""
    from app.shared.core.config import settings

    return settings.MILVUS_HOST


def _default_milvus_port() -> int:
    """从全局 settings 取 Milvus 端口。"""
    from app.shared.core.config import settings

    return int(settings.MILVUS_PORT)


@dataclass
class RetrievalConfig:
    """检索模块配置。

    Milvus 连接参数默认取自全局 `settings`，其余检索调参保留字面默认值。

    WHY 连接参数必须走 settings：这里曾经硬编码 `localhost:19530`，
    而 `.env.docker` 里是 `MILVUS_HOST=milvus`。结果在 Docker 部署下，
    长期记忆（读 settings.MILVUS_URL）连得上，文档检索（读本类默认值）
    连的却是容器自己的 localhost——RAG 整条链路在容器里不可用。
    """

    # ------------------------------------------------------------------ #
    # Milvus 配置
    # ------------------------------------------------------------------ #
    milvus_host: str = field(default_factory=_default_milvus_host)
    milvus_port: int = field(default_factory=_default_milvus_port)
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
    #
    # BM25 由 Milvus 服务端 Function 计算（raw_text → sparse_vector），
    # 查询侧只需把原文交给 Milvus，不在客户端做分词或算 token id。
    # ------------------------------------------------------------------ #
    bm25_top_k: int = 20  # sparse 检索候选条数
    bm25_drop_ratio: float = 0.2  # 检索时丢弃的低权重项比例，压制长尾 token 噪音

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
        if not 0.0 <= self.bm25_drop_ratio < 1.0:
            raise ValueError("bm25_drop_ratio 必须落在 [0, 1)")
