"""
RRF (Reciprocal Rank Fusion) 融合算法 + Reranker 重排序。

RRF 公式: score = Σ 1 / (k + rank_i)
k 默认 60，越大则排名头部差异越小。

Reranker: 使用 Cross-Encoder 对 RRF 结果重排序。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def rrf_fusion(
    vector_results: List[Dict[str, Any]],
    bm25_results: List[Dict[str, Any]],
    k: int = 60,
    top_k: int = 10,
    key_field: str = "chunk_id",
) -> List[Dict[str, Any]]:
    """RRF 融合：合并向量检索和 BM25 检索的排名。

    不依赖原始分数（向量分数和 BM25 分数不可直接比较），
    只依赖排名。

    Args:
        vector_results: 向量检索结果。
        bm25_results: BM25 检索结果。
        k: RRF 常数（默认 60）。
        top_k: 最终返回条数。
        key_field: 用于去重合并的 key 字段。

    Returns:
        融合排序后的结果列表，每项包含 rrf_score。
    """
    # 用 chunk_id 去重合并
    merged: Dict[str, Dict[str, Any]] = {}

    for rank, item in enumerate(vector_results, start=1):
        key = item.get(key_field, "")
        if not key:
            continue
        if key not in merged:
            merged[key] = {**item, "rrf_score": 0.0}
        merged[key]["rrf_score"] += 1.0 / (k + rank)

    for rank, item in enumerate(bm25_results, start=1):
        key = item.get(key_field, "")
        if not key:
            continue
        if key not in merged:
            merged[key] = {**item, "rrf_score": 0.0}
        merged[key]["rrf_score"] += 1.0 / (k + rank)

    # 按 RRF 分数降序排序
    sorted_items = sorted(merged.values(), key=lambda x: x["rrf_score"], reverse=True)
    return sorted_items[:top_k]


class Reranker:
    """基于 Cross-Encoder 的重排序器。

    对 RRF 融合后的 top-k 结果进一步精排。
    默认使用 FlagEmbedding 的 bge-reranker-v2-m3。
    """

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        """初始化 Reranker。

        Args:
            model_name: HuggingFace 模型名。

        注：需要 `pip install FlagEmbedding` 才能使用。
        如果未安装，自动降级为按 RRF 分数排序。
        """
        self.model_name = model_name
        self._model = None
        self._available = False
        self._init_model()

    def _init_model(self):
        """延迟加载模型。"""
        try:
            from FlagEmbedding import FlagReranker
            self._model = FlagReranker(self.model_name, use_fp16=True)
            self._available = True
            logger.info(f"Reranker 加载成功: {self.model_name}")
        except ImportError:
            logger.warning(
                "FlagEmbedding 未安装，Reranker 不可用。"
                "安装: pip install FlagEmbedding"
            )
        except Exception as e:
            logger.warning(f"Reranker 加载失败: {e}")

    def rerank(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        top_k: int = 5,
        text_field: str = "raw_text",
    ) -> List[Dict[str, Any]]:
        """对候选结果重排序。

        Args:
            query: 查询文本。
            candidates: RRF 融合后的候选结果。
            top_k: 最终返回条数。
            text_field: 用于计算相关性的文本字段。

        Returns:
            重排序后的结果列表。
        """
        if not self._available or not candidates:
            return candidates[:top_k]

        pairs = [[query, c.get(text_field, "")] for c in candidates]
        scores = self._model.compute_score(pairs, normalize=True)

        # 将 rerank 分数写入结果
        if not isinstance(scores, list):
            scores = [scores]
        for i, score in enumerate(scores):
            candidates[i]["rerank_score"] = float(score)

        candidates.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
        return candidates[:top_k]

    @property
    def available(self) -> bool:
        return self._available
