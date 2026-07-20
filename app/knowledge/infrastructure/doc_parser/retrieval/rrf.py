"""混合检索结果的重排序支持。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)
class Reranker:
    """基于 Cross-Encoder 的重排序器。

    对混合检索返回的 top-k 结果进一步精排。
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
            from FlagEmbedding import FlagReranker  # pyright: ignore[reportMissingImports]

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
        candidates: list[dict[str, Any]],
        top_k: int = 5,
        text_field: str = "raw_text",
    ) -> list[dict[str, Any]]:
        """对候选结果重排序。

        Args:
            query: 查询文本。
            candidates: 混合检索返回的候选结果。
            top_k: 最终返回条数。
            text_field: 用于计算相关性的文本字段。

        Returns:
            重排序后的结果列表。
        """
        if not self._available or not candidates or self._model is None:
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
