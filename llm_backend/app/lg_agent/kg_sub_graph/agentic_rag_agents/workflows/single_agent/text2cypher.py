"""Text2Cypher 单 Agent 图组装。

这个模块负责：
- 组装“预定义模板快速路径 + LLM 生成兜底路径”的 LangGraph 图
- 把模板匹配策略和 Cypher 生成节点串成统一入口

这个模块不负责：
- 具体模板匹配实现
- Cypher 校验规则定义
- Neo4j 连接管理
"""

from __future__ import annotations

from typing import Literal

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


def _coerce_task_text(task: str | list[str]) -> str:
    """把状态里的 task 统一转换为字符串。"""
    return task[0] if isinstance(task, list) else task


def create_text2cypher_agent(
    llm: BaseChatModel,
    graph: Neo4jGraph,
    cypher_example_retriever: BaseCypherExampleRetriever,
    llm_cypher_validation: bool = True,
    max_attempts: int = 3,
    attempt_cypher_execution_on_final_attempt: bool = False,
    predefined_cypher_dict: dict[str, str] | None = None,
    query_descriptions: dict[str, str] | None = None,
) -> CompiledStateGraph:
    """Create a Text2Cypher agent with predefined template matching.

    WHY：
    高频、结构稳定的问题先走模板匹配快速路径；
    未命中时再落回完整的 LLM 生成 + 校验 + 修正链路。

    这样做的好处是：
    - 常见查询延迟更低
    - 模板和 LLM 各自职责更清晰
    - 图结构只负责编排，不混入模板细节
    """
    text2cypher_graph_builder = StateGraph(
        CypherState,
        input_schema=CypherInputState,
        output_schema=OverallState,
    )

    execute_cypher = create_text2cypher_execution_node(graph=graph)

    if predefined_cypher_dict:
        matcher = create_vector_query_matcher(
            predefined_cypher_dict,
            query_descriptions or {},
        )
        template_strategy = PredefinedTemplateStrategy(
            matcher=matcher,
            graph=graph,
            llm=llm,
            similarity_threshold=0.6,
        )

        async def predefined_match(state: CypherState) -> dict:
            task = state.get("task", "")
            return await template_strategy.generate(str(_coerce_task_text(task)))

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
