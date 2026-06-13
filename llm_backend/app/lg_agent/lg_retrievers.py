"""检索器抽象与注册入口。

职责：
- 定义主图和 ReAct 共享的 `Retriever` 抽象接口
- 统一归一化 KG / RAG 检索结果结构
- 管理检索器注册表与懒初始化单例

设计原因：
- 让 Agent 依赖抽象而不是直接依赖 KG / RAG 底层实现
- 让检索后端更容易替换，也更容易在测试里注入 mock
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.lg_agent.lg_retriever_support import (
    build_milvus_doc_fallback_record,
    build_milvus_doc_record,
    normalize_retriever_result,
)

KG_RETRIEVER_NAME = "kg"
RAG_RETRIEVER_NAME = "rag"
RAG_SEARCH_STEP = "execute_rag_search"


class Retriever(ABC):
    """检索器抽象接口。

    所有检索后端（Milvus 文档检索、Neo4j 知识图谱）都实现此接口。
    Agent 只依赖此接口，不关心底层是 Milvus、Elasticsearch 还是其他。
    """

    @abstractmethod
    async def search(self, task: str) -> dict[str, Any]:
        """执行检索并返回结果。

        Args:
            task: 检索任务描述，可以是自然语言查询或结构化指令。

        Returns:
            检索结果字典，必须包含 "records" 字段（list），
            可选包含 "errors"（list[str]）和 "steps"（list[str]）。
        """
        ...


# ================================================================== #
# 检索器注册表 — 集中管理所有 Retriever 实例
# ================================================================== #

class RetrieverRegistry:
    """检索器注册表 — 按名称管理所有 Retriever 实例。

    使用方式：
      registry = RetrieverRegistry()
      registry.register("kg", neo4j_retriever)
      registry.register("rag", milvus_retriever)
      retriever = registry.get("rag")
      if retriever is not None:
          results = await retriever.search("查询保修政策")
    """

    def __init__(self) -> None:
        self._retrievers: dict[str, Retriever] = {}

    def register(self, name: str, retriever: Retriever) -> None:
        """注册一个检索器。"""
        self._retrievers[name] = retriever

    def get(self, name: str) -> Retriever | None:
        """按名称获取检索器。返回 None 表示未注册。"""
        return self._retrievers.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._retrievers


# ================================================================== #
# 具体实现：Milvus 文档检索器
# ================================================================== #

class MilvusDocRetriever(Retriever):
    """基于 rag_doc_parser + Milvus 的文档检索器。

    封装了 rag_doc_parser.retrieval.hybrid_search.HybridSearcher，
    使 Agent 不需要知道 rag_doc_parser 的存在。
    """

    def __init__(self):
        self._searcher = self._create_searcher()

    @staticmethod
    def _create_searcher():
        """创建 HybridSearcher 实例。

        注册表本身已经是模块级单例，这里不再额外做双层缓存。
        """
        from rag_doc_parser.retrieval.hybrid_search import HybridSearcher
        from rag_doc_parser.retrieval.config import RetrievalConfig

        return HybridSearcher(RetrievalConfig())

    async def search(self, task: str) -> dict[str, Any]:
        """检索 Milvus 文档知识库。

        Returns:
            {"records": [...], "errors": [...], "steps": [...]}
        """
        errors: list[str] = []

        try:
            results = await self._searcher.search(task)
            records = [build_milvus_doc_record(result) for result in results[:5]] if results else []
        except ImportError:
            records = build_milvus_doc_fallback_record("文档检索模块未安装。请先上传文档建立知识库。")
            errors.append("rag_doc_parser 模块未安装")
        except Exception as exc:
            records = build_milvus_doc_fallback_record("文档检索暂时不可用。")
            errors.append(str(exc))

        return {
            "task": task,
            "records": records,
            "errors": errors,
            "steps": [RAG_SEARCH_STEP],
        }


# ================================================================== #
# 具体实现：Neo4j 知识图谱检索器
# ================================================================== #

class KnowledgeGraphRetriever(Retriever):
    """基于 Neo4j + Text2Cypher 的知识图谱检索器。

    封装了 Text2Cypher Agent，Agent 不需要知道 Neo4j 连接细节。
    """

    def __init__(self, t2c_agent):
        """注入 Text2Cypher Agent。

        Args:
            t2c_agent: 已创建的 Text2Cypher 子图（CompiledStateGraph）。
        """
        self._t2c_agent = t2c_agent

    async def search(self, task: str) -> dict[str, Any]:
        """查询 Neo4j 知识图谱。

        Returns:
            统一格式结果，原始输出保存在 `raw` 字段中。
        """
        raw_result = await self._t2c_agent.ainvoke({"task": task})
        return normalize_retriever_result(raw_result, task=task)


from app.lg_agent.lg_retriever_runtime import ensure_registry, get_retriever
