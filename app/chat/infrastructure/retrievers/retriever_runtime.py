"""Retriever 运行时 — 通过 AppContainer 统一管理检索器生命周期。

职责：
- 从 AppContainer 获取检索器注册表和 KG 子图组件
- 懒初始化 KG / RAG 检索器
- 缓存 Text2Cypher 子图和 Cypher 示例检索器
"""

from __future__ import annotations

from typing import Any

from app.chat.infrastructure.retrievers.retriever_contracts import KG_RETRIEVER_NAME, RAG_RETRIEVER_NAME
from app.chat.infrastructure.kg_sub_graph.kg_neo4j_conn import _get_neo4j_graph

# 保留模块级属性供旧测试 monkeypatch 使用
_registry: Any | None = None
_cypher_example_retriever: Any | None = None
_t2c_agent: Any | None = None


async def get_retriever(name: str) -> Any:
    """获取检索器。

    通过 AppContainer 管理检索器注册表、KG 子图组件和 Neo4j 连接缓存。
    首次调用时懒初始化 KG/RAG 检索器并注册到容器的注册表中。
    """
    from app.platform.container import get_container

    container = await get_container()

    if container.retriever_registry is None:
        from app.chat.infrastructure.retrievers.retriever_contracts import RetrieverRegistry

        container.retriever_registry = RetrieverRegistry()

    registry = container.retriever_registry

    if KG_RETRIEVER_NAME not in registry or RAG_RETRIEVER_NAME not in registry:
        async with container.retriever_registry_lock:
            registry = container.retriever_registry

            if KG_RETRIEVER_NAME not in registry:
                neo4j_graph = _get_neo4j_graph(container)
                if neo4j_graph is not None:
                    if container._cypher_example_retriever is None:
                        from app.chat.infrastructure.kg_sub_graph.agentic_rag_agents.retrievers.cypher_examples.northwind_retriever import (
                            NorthwindCypherRetriever,
                        )

                        container._cypher_example_retriever = NorthwindCypherRetriever()

                    if container._t2c_agent is None:
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

                        container._t2c_agent = create_text2cypher_agent(
                            llm=cypher_model,
                            graph=neo4j_graph,
                            cypher_example_retriever=container._cypher_example_retriever,
                            predefined_cypher_dict=predefined_cypher_dict,
                            query_descriptions=QUERY_DESCRIPTIONS,
                        )

                    from app.chat.infrastructure.retrievers.retriever_implementations import (
                        KnowledgeGraphRetriever,
                    )

                    registry.register(KG_RETRIEVER_NAME, KnowledgeGraphRetriever(container._t2c_agent))

            if RAG_RETRIEVER_NAME not in registry:
                from app.chat.infrastructure.retrievers.retriever_implementations import MilvusDocRetriever

                registry.register(RAG_RETRIEVER_NAME, MilvusDocRetriever())

    return registry.get(name)


__all__ = ["get_retriever"]
