from abc import ABC, abstractmethod
from pydantic import BaseModel, ConfigDict

class BaseCypherExampleRetriever(BaseModel, ABC):
    """Cypher 示例检索器抽象基类。"""

    model_config: ConfigDict = ConfigDict(**{"arbitrary_types_allowed": True})  # type: ignore[misc]

    @abstractmethod
    def get_examples(self, query: str, k: int = 5) -> str:
        """根据用户查询返回相关的 Cypher 示例文本。"""
        ...
