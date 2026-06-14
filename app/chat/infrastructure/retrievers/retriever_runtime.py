"""Retriever 运行时单例管理。

这个模块负责：
- 管理 RetrieverRegistry 的模块级单例
- 懒初始化 KG / RAG 检索器
- 缓存 Text2Cypher 子图和 Cypher 示例检索器

这个模块不负责：
- 定义 Retriever 抽象接口
- 适配 Milvus / Neo4j 的检索返回结构
- 生成检索结果摘要
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.chat.infrastructure.retrievers.retriever_contracts import (
    KG_RETRIEVER_NAME,
    RAG_RETRIEVER_NAME,
)

_registry: Any | None = None
_registry_lock: asyncio.Lock = asyncio.Lock()
_cypher_example_retriever: Any | None = None
_t2c_agent: Any | None = None


def _get_registry():
    """返回全局 Retriever 注册表；首次访问时再创建。"""
    global _registry
    if _registry is None:
        from app.chat.infrastructure.retrievers.retriever_contracts import (
            RetrieverRegistry,
        )

        _registry = RetrieverRegistry()
    return _registry


def _register_kg_retriever() -> None:
    """在 Neo4j 可用时注册知识图谱检索器。"""
    global _cypher_example_retriever, _t2c_agent

    from app.chat.infrastructure.kg_sub_graph.kg_neo4j_conn import get_neo4j_graph

    neo4j_graph = get_neo4j_graph()
    if neo4j_graph is None:
        return

    if _cypher_example_retriever is None:
        from app.chat.infrastructure.kg_sub_graph.agentic_rag_agents.retrievers.cypher_examples.northwind_retriever import (
            NorthwindCypherRetriever,
        )

        _cypher_example_retriever = NorthwindCypherRetriever()

    if _t2c_agent is None:
        from app.chat.infrastructure.kg_sub_graph.agentic_rag_agents.components.predefined_cypher.cypher_dict import (
            predefined_cypher_dict,
        )
        from app.chat.infrastructure.kg_sub_graph.agentic_rag_agents.components.predefined_cypher.descriptions import (
            QUERY_DESCRIPTIONS,
        )
        from app.chat.infrastructure.kg_sub_graph.agentic_rag_agents.workflows.single_agent.text2cypher import (
            create_text2cypher_agent,
        )
        from app.chat.infrastructure.modeling.models import cypher_model

        _t2c_agent = create_text2cypher_agent(
            llm=cypher_model,
            graph=neo4j_graph,
            cypher_example_retriever=_cypher_example_retriever,
            predefined_cypher_dict=predefined_cypher_dict,
            query_descriptions=QUERY_DESCRIPTIONS,
        )

    from app.chat.infrastructure.retrievers.retriever_implementations import (
        KnowledgeGraphRetriever,
    )

    _get_registry().register(
        KG_RETRIEVER_NAME,
        KnowledgeGraphRetriever(_t2c_agent),
    )


def _register_rag_retriever() -> None:
    """注册文档检索器。"""
    from app.chat.infrastructure.retrievers.retriever_implementations import (
        MilvusDocRetriever,
    )

    _get_registry().register(RAG_RETRIEVER_NAME, MilvusDocRetriever())


async def get_retriever(name: str):
    """获取检索器。确保 registry 已初始化。"""
    registry = _get_registry()
    if KG_RETRIEVER_NAME not in registry or RAG_RETRIEVER_NAME not in registry:
        async with _registry_lock:
            registry = _get_registry()
            if KG_RETRIEVER_NAME not in registry:
                _register_kg_retriever()
            if RAG_RETRIEVER_NAME not in registry:
                _register_rag_retriever()
    return _get_registry().get(name)


__all__ = ["get_retriever"]
