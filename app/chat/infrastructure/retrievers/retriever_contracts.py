"""检索层共享契约。

职责：
- 定义检索器抽象接口和共享常量
- 定义 KG / RAG 相关常量

边界：
- 不承载具体的 Milvus / Neo4j 检索实现
- 不承载运行时单例管理
"""

from abc import ABC, abstractmethod
from typing import Any

KG_RETRIEVER_NAME = "kg"
RAG_RETRIEVER_NAME = "rag"


class Retriever(ABC):
    """检索器抽象接口。"""

    @abstractmethod
    async def search(self, task: str) -> dict[str, Any]:
        """执行检索并返回统一结构结果。"""
