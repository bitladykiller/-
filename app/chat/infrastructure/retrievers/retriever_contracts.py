"""检索层共享契约。

职责：
- 定义检索器抽象接口和注册表
- 定义 KG / RAG 相关常量

边界：
- 不承载具体的 Milvus / Neo4j 检索实现
- 不承载运行时单例管理
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

KG_RETRIEVER_NAME = "kg"
RAG_RETRIEVER_NAME = "rag"
RAG_SEARCH_STEP = "execute_rag_search"


class Retriever(ABC):
    """检索器抽象接口。"""

    @abstractmethod
    async def search(self, task: str) -> dict[str, Any]:
        """执行检索并返回统一结构结果。"""


class RetrieverRegistry:
    """按名称管理所有 Retriever 实例的注册表。"""

    def __init__(self) -> None:
        self._retrievers: dict[str, Retriever] = {}

    def register(self, name: str, retriever: Retriever) -> None:
        """注册一个检索器。"""

        self._retrievers[name] = retriever

    def get(self, name: str) -> Retriever | None:
        """按名称获取检索器。"""

        return self._retrievers.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._retrievers


__all__ = [
    "KG_RETRIEVER_NAME",
    "RAG_RETRIEVER_NAME",
    "RAG_SEARCH_STEP",
    "Retriever",
    "RetrieverRegistry",
]
