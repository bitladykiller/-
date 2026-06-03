"""
检索器接口抽象。

v3.16 新增。设计动机（WHY）：
Agent 的 5 条执行路径（GRAPH_ONLY / RAG_ONLY / PARALLEL / GRAPH_THEN_RAG / AGENT_REACT）
之前直接依赖 rag_doc_parser 和 kg_sub_graph 的内部实现。
这导致：
1. 检索后端不可替换 — 换用 Elasticsearch 需要改 Agent 核心代码
2. 测试困难 — 不能 mock 检索器来单独测试路由逻辑
3. 违反依赖倒置原则 — 高层模块（Agent）依赖低层模块（具体检索实现）

解决方案：定义 Retriever 接口，让 Agent 依赖抽象，具体实现通过依赖注入。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


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
      results = await registry["rag"].search("查询保修政策")
    """

    def __init__(self):
        self._retrievers: dict[str, Retriever] = {}

    def register(self, name: str, retriever: Retriever):
        """注册一个检索器。"""
        self._retrievers[name] = retriever

    def get(self, name: str) -> Retriever | None:
        """按名称获取检索器。返回 None 表示未注册。"""
        return self._retrievers.get(name)

    def __getitem__(self, name: str) -> Retriever:
        """便捷访问，不存在时抛 KeyError。"""
        return self._retrievers[name]

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
        """创建 HybridSearcher 实例（懒加载 + 缓存）。"""
        from rag_doc_parser.retrieval.hybrid_search import HybridSearcher
        from rag_doc_parser.retrieval.config import RetrievalConfig
        config = RetrievalConfig()
        return HybridSearcher(config)

    async def search(self, task: str) -> dict[str, Any]:
        """检索 Milvus 文档知识库。

        Returns:
            {"records": {"result": str}, "errors": [...], "steps": [...]}
        """
        errors: list[str] = []

        try:
            results = await self._searcher.search(task)
            if results:
                records = {
                    "result": "\n\n".join(
                        f"[{r.get('chunk_type', 'text')}] {r.get('section_path', '')}\n{r.get('raw_text', '')}"
                        for r in results[:5]
                    )
                }
            else:
                records = {"result": "未在文档知识库中找到相关信息。"}
        except ImportError:
            records = {"result": "文档检索模块未安装。请先上传文档建立知识库。"}
            errors.append("rag_doc_parser 模块未安装")
        except Exception as e:
            records = {"result": "文档检索暂时不可用。"}
            errors.append(str(e))

        return {
            "task": task,
            "records": records,
            "errors": errors,
            "steps": ["execute_rag_search"],
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
            Text2Cypher 子图的原始输出（含 cyphers/records/steps/errors）。
        """
        return await self._t2c_agent.ainvoke({"task": task})
