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

_registry: Any | None = None
_registry_lock: asyncio.Lock = asyncio.Lock()
_cypher_example_retriever: Any | None = None
_t2c_agent: Any | None = None


def _get_registry():
    """返回全局 Retriever 注册表；首次访问时再创建。"""
    global _registry
    if _registry is None:
        from app.lg_agent.lg_retrievers import RetrieverRegistry

        _registry = RetrieverRegistry()
    return _registry


def _registry_ready() -> bool:
    """判断核心检索器是否都已注册。"""
    from app.lg_agent.lg_retrievers import KG_RETRIEVER_NAME, RAG_RETRIEVER_NAME

    registry = _get_registry()
    return KG_RETRIEVER_NAME in registry and RAG_RETRIEVER_NAME in registry


def _get_or_create_cypher_example_retriever():
    """获取 Northwind Cypher 示例检索器缓存。"""
    global _cypher_example_retriever
    if _cypher_example_retriever is None:
        from app.lg_agent.kg_sub_graph.agentic_rag_agents.retrievers.cypher_examples.northwind_retriever import (
            NorthwindCypherRetriever,
        )

        _cypher_example_retriever = NorthwindCypherRetriever()
    return _cypher_example_retriever


def _get_or_create_t2c_agent(neo4j_graph):
    """获取 Text2Cypher 子图缓存。"""
    global _t2c_agent
    if _t2c_agent is None:
        from app.lg_agent.kg_sub_graph.agentic_rag_agents.components.predefined_cypher.cypher_dict import (
            predefined_cypher_dict,
        )
        from app.lg_agent.kg_sub_graph.agentic_rag_agents.components.predefined_cypher.descriptions import (
            QUERY_DESCRIPTIONS,
        )
        from app.lg_agent.kg_sub_graph.agentic_rag_agents.workflows.single_agent import (
            create_text2cypher_agent,
        )
        from app.lg_agent.lg_models import cypher_model

        _t2c_agent = create_text2cypher_agent(
            llm=cypher_model,
            graph=neo4j_graph,
            cypher_example_retriever=_get_or_create_cypher_example_retriever(),
            predefined_cypher_dict=predefined_cypher_dict,
            query_descriptions=QUERY_DESCRIPTIONS,
        )
    return _t2c_agent


def _register_kg_retriever() -> None:
    """在 Neo4j 可用时注册知识图谱检索器。"""
    from app.lg_agent.kg_sub_graph.kg_neo4j_conn import get_neo4j_graph
    from app.lg_agent.lg_retrievers import KG_RETRIEVER_NAME, KnowledgeGraphRetriever

    neo4j_graph = get_neo4j_graph()
    if neo4j_graph is None:
        return

    _get_registry().register(
        KG_RETRIEVER_NAME,
        KnowledgeGraphRetriever(_get_or_create_t2c_agent(neo4j_graph)),
    )


def _register_rag_retriever() -> None:
    """注册文档检索器。"""
    from app.lg_agent.lg_retrievers import MilvusDocRetriever, RAG_RETRIEVER_NAME

    _get_registry().register(RAG_RETRIEVER_NAME, MilvusDocRetriever())


def _register_missing_retrievers() -> None:
    """只补注册缺失的检索器，避免重复创建已就绪实例。"""
    from app.lg_agent.lg_retrievers import KG_RETRIEVER_NAME, RAG_RETRIEVER_NAME

    registry = _get_registry()
    if KG_RETRIEVER_NAME not in registry:
        _register_kg_retriever()
    if RAG_RETRIEVER_NAME not in registry:
        _register_rag_retriever()


async def ensure_registry() -> None:
    """懒初始化检索器注册表。首次调用时补齐缺失的 Retriever 实例。"""
    if _registry_ready():
        return

    async with _registry_lock:
        if _registry_ready():
            return
        _register_missing_retrievers()


async def get_retriever(name: str):
    """获取检索器。确保 registry 已初始化。"""
    await ensure_registry()
    return _get_registry().get(name)
