from typing import Any, Callable, Coroutine, Dict, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_neo4j import Neo4jGraph

from ....retrievers.cypher_examples.base import BaseCypherExampleRetriever
from ....workflows.single_agent import create_text2cypher_agent


def create_cypher_query_node(
    llm: BaseChatModel,
    graph: Neo4jGraph,
    cypher_example_retriever: BaseCypherExampleRetriever,
    llm_cypher_validation: bool = True,
    max_attempts: int = 3,
) -> Callable[
    [Dict[str, Any]],
    Coroutine[Any, Any, Dict[str, Any]],
]:
    """创建 Text2Cypher 查询节点，内部使用 create_text2cypher_agent 子图。

    子图流程：generate_cypher → validate_cypher ⇄ correct_cypher → execute_cypher
    验证失败时自动进入修正循环，最多重试 max_attempts 次。
    """

    text2cypher_agent = create_text2cypher_agent(
        llm=llm,
        graph=graph,
        cypher_example_retriever=cypher_example_retriever,
        llm_cypher_validation=llm_cypher_validation,
        max_attempts=max_attempts,
    )

    async def cypher_query(state: Dict[str, Any]) -> Dict[str, Any]:
        task = state.get("task", "")
        result = await text2cypher_agent.ainvoke({"task": task})

        return {
            "cyphers": [
                {
                    "task": task,
                    "statement": result.get("statement", ""),
                    "parameters": result.get("parameters"),
                    "errors": result.get("errors", []),
                    "records": result.get("records", []),
                    "steps": result.get("steps", []),
                }
            ],
            "steps": ["cypher_query"],
        }

    return cypher_query
