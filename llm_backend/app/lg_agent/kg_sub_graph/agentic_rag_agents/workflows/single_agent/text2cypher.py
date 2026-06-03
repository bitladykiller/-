from typing import Dict, Literal, Optional

from langchain_core.language_models import BaseChatModel
from langchain_neo4j import Neo4jGraph
from langgraph.constants import END, START
from langgraph.graph.state import CompiledStateGraph, StateGraph

from ...components.state import OverallState
from ...components.text2cypher import (
    create_text2cypher_correction_node,
    create_text2cypher_execution_node,
    create_text2cypher_generation_node,
    create_text2cypher_validation_node,
)
from ...components.text2cypher.state import CypherInputState, CypherState
from ...components.predefined_cypher.utils import create_vector_query_matcher
from ...retrievers.cypher_examples.base import BaseCypherExampleRetriever
from .cypher_strategies import PredefinedTemplateStrategy


def create_text2cypher_agent(
    llm: BaseChatModel,
    graph: Neo4jGraph,
    cypher_example_retriever: BaseCypherExampleRetriever,
    llm_cypher_validation: bool = True,
    max_attempts: int = 3,
    attempt_cypher_execution_on_final_attempt: bool = False,
    predefined_cypher_dict: Optional[Dict[str, str]] = None,
    query_descriptions: Optional[Dict[str, str]] = None,
) -> CompiledStateGraph:
    """Create a Text2Cypher agent with predefined template matching.

    架构（v3.16 策略模式）：
    - PredefinedTemplateStrategy: 语义匹配预定义模板 → 直接执行（<100ms，快速路径）
    - LLMGenerationStrategy: LLM 生成 + 5 层验证 → 执行（~600ms，兜底路径）

    策略封装在 cypher_strategies.py 中，此处仅负责 LangGraph 图组装。
    """
    text2cypher_graph_builder = StateGraph(
        CypherState, input=CypherInputState, output=OverallState
    )

    execute_cypher = create_text2cypher_execution_node(graph=graph)

    if predefined_cypher_dict:
        # 预定义模板策略 — 封装在 PredefinedTemplateStrategy 中
        matcher = create_vector_query_matcher(predefined_cypher_dict, query_descriptions or {})
        template_strategy = PredefinedTemplateStrategy(
            matcher=matcher, graph=graph, llm=llm, similarity_threshold=0.6,
        )

        async def predefined_match(state: CypherState) -> dict:
            task = state.get("task", "")
            task_str = task[0] if isinstance(task, list) else task
            return await template_strategy.generate(str(task_str))

        text2cypher_graph_builder.add_node("predefined_match", predefined_match)
        text2cypher_graph_builder.add_edge(START, "predefined_match")

        def _match_edge(state: CypherState) -> Literal["execute_cypher", "generate_cypher"]:
            if state.get("records") is not None:
                return "execute_cypher"
            return "generate_cypher"

    # LLM generation + validation + correction
    generate_cypher = create_text2cypher_generation_node(
        llm=llm, graph=graph, cypher_example_retriever=cypher_example_retriever
    )
    validate_cypher = create_text2cypher_validation_node(
        llm=llm, graph=graph,
        llm_validation=llm_cypher_validation,
        max_attempts=max_attempts,
        attempt_cypher_execution_on_final_attempt=attempt_cypher_execution_on_final_attempt,
    )
    correct_cypher = create_text2cypher_correction_node(llm=llm, graph=graph)

    text2cypher_graph_builder.add_node("generate_cypher", generate_cypher)
    text2cypher_graph_builder.add_node("validate_cypher", validate_cypher)
    text2cypher_graph_builder.add_node("correct_cypher", correct_cypher)
    text2cypher_graph_builder.add_node("execute_cypher", execute_cypher)

    if predefined_cypher_dict:
        text2cypher_graph_builder.add_conditional_edges("predefined_match", _match_edge, {
            "execute_cypher": "execute_cypher",
            "generate_cypher": "generate_cypher",
        })
    else:
        text2cypher_graph_builder.add_edge(START, "generate_cypher")

    text2cypher_graph_builder.add_edge("generate_cypher", "validate_cypher")
    text2cypher_graph_builder.add_conditional_edges("validate_cypher", validate_cypher_conditional_edge)
    text2cypher_graph_builder.add_edge("correct_cypher", "validate_cypher")
    text2cypher_graph_builder.add_edge("execute_cypher", END)

    return text2cypher_graph_builder.compile()


def validate_cypher_conditional_edge(
    state: CypherState,
) -> Literal["correct_cypher", "execute_cypher", "__end__"]:
    match state.get("next_action_cypher"):
        case "correct_cypher":
            return "correct_cypher"
        case "execute_cypher":
            return "execute_cypher"
        case "__end__":
            return "__end__"
        case _:
            return "__end__"
