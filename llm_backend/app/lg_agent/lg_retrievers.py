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


# ================================================================== #
# 检索器单例管理 — 懒初始化 + 双检锁
# ================================================================== #
#
# v3.17: 从 lg_nodes.py 迁移至此。检索器注册表的初始化和管理逻辑
# 放在检索器接口模块中更符合模块边界。
#
# 设计模式：Registry（注册表模式）+ Lazy Initialization（懒初始化）
# ================================================================== #

import asyncio
import logging

logger = logging.getLogger(__name__)

# 模块级单例
_registry: RetrieverRegistry = RetrieverRegistry()
_registry_lock: asyncio.Lock = asyncio.Lock()
_retriever = None
_t2c_agent = None  # Text2Cypher compiled graph 缓存
_summarize_node = None  # 摘要生成节点缓存


async def _ensure_registry():
    """懒初始化检索器注册表。首次调用时创建所有 Retriever 实例。

    使用双检锁（double-checked locking）防止并发请求重复创建。
    创建完后 registry 可通过 get_registry() 获取。
    """
    if "kg" in _registry and "rag" in _registry:
        return

    async with _registry_lock:
        if "kg" in _registry and "rag" in _registry:
            return

        from app.lg_agent.kg_sub_graph.kg_neo4j_conn import get_neo4j_graph

        neo4j_graph = get_neo4j_graph()
        if neo4j_graph is not None:
            global _t2c_agent
            if _t2c_agent is None:
                global _retriever
                if _retriever is None:
                    from app.lg_agent.kg_sub_graph.agentic_rag_agents.retrievers.cypher_examples.northwind_retriever import (
                        NorthwindCypherRetriever,
                    )
                    _retriever = NorthwindCypherRetriever()
                from app.lg_agent.lg_models import cypher_model
                from app.lg_agent.kg_sub_graph.agentic_rag_agents.workflows.single_agent import (
                    create_text2cypher_agent,
                )
                from app.lg_agent.kg_sub_graph.agentic_rag_agents.components.predefined_cypher.cypher_dict import (
                    predefined_cypher_dict,
                )
                from app.lg_agent.kg_sub_graph.agentic_rag_agents.components.predefined_cypher.descriptions import (
                    QUERY_DESCRIPTIONS,
                )
                _t2c_agent = create_text2cypher_agent(
                    llm=cypher_model,
                    graph=neo4j_graph,
                    cypher_example_retriever=_retriever,
                    predefined_cypher_dict=predefined_cypher_dict,
                    query_descriptions=QUERY_DESCRIPTIONS,
                )
            _registry.register("kg", KnowledgeGraphRetriever(_t2c_agent))

        _registry.register("rag", MilvusDocRetriever())


async def _reg(name: str) -> Retriever | None:
    """获取检索器。确保 registry 已初始化。

    Args:
        name: 检索器名称，如 "kg" 或 "rag"。

    Returns:
        Retriever 实例，不存在时返回 None。
    """
    await _ensure_registry()
    return _registry.get(name)


def get_registry() -> RetrieverRegistry:
    """获取检索器注册表（不保证已初始化）。

    供 ReAct 子图构建等需要注册表引用的场景使用。
    调用者需先执行 _ensure_registry()。
    """
    return _registry


async def _summarize(
    question: str, records: list, fallback: str = "未查询到相关信息～"
) -> str:
    """对查询结果生成摘要。records 为空时返回 fallback。

    Args:
        question: 用户问题。
        records: 查询结果列表。
        fallback: 无结果时的默认回复。

    Returns:
        生成的摘要文本。
    """
    if not records:
        return fallback
    global _summarize_node
    if _summarize_node is None:
        from app.lg_agent.lg_models import cypher_model
        from app.lg_agent.kg_sub_graph.agentic_rag_agents.components.summarize import (
            create_summarization_node,
        )
        _summarize_node = create_summarization_node(llm=cypher_model)
    result = await _summarize_node.ainvoke({
        "question": question,
        "cyphers": [{"records": records}],
    })
    return result.get("summary", "") or fallback
