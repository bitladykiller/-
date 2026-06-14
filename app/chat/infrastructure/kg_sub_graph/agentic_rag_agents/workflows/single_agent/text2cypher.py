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

from operator import add
from typing import Annotated, Any, List, Literal, Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_neo4j import Neo4jGraph
from langgraph.constants import END, START
from langgraph.graph.state import CompiledStateGraph, StateGraph
from typing_extensions import TypedDict

from ...components.text2cypher.validation.models import ValidateCypherOutput
from ...components.text2cypher.validation.validators import (
    correct_cypher_query_relationship_direction,
    validate_cypher_query_syntax,
    validate_cypher_query_with_llm,
    validate_cypher_query_with_schema,
    validate_no_writes_in_cypher_query,
)
from ...components.text2cypher.state import CypherInputState, CypherOutputState, CypherState
from ...components.predefined_cypher.utils import create_vector_query_matcher


class OverallState(TypedDict):
    question: str
    tasks: Annotated[list, add]
    next_action: str
    cyphers: Annotated[List[CypherOutputState], add]
    summary: str
    steps: Annotated[List[str], add]


class CypherExampleRetriever(Protocol):
    """Text2Cypher 生成节点需要的最小示例检索接口。"""

    def get_examples(self, query: str, k: int = 5) -> str: ...


_GENERATION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            (
                "根据输入的问题，将其转换为Cypher查询语句。不要添加任何前言。"
                "不要在响应中包含任何反引号或其他标记。注意：只返回Cypher语句！"
            ),
        ),
        (
            "human",
            """你是一位Neo4j专家。根据输入的问题，创建一个语法正确的Cypher查询语句。
                        不要在响应中包含任何反引号或其他标记。只使用MATCH或WITH子句开始查询。只返回Cypher语句！

                        以下是数据库模式信息：
                        {schema}

                        下面是一些问题和对应Cypher查询的示例：

                        {fewshot_examples}

                        用户输入: {question}
                        Cypher查询:""",
        ),
    ]
)

_CORRECTION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            (
                "You are a Cypher expert reviewing a statement written by a junior developer. "
                "You need to correct the Cypher statement based on the provided errors. No pre-amble."
                "Do not wrap the response in any backticks or anything else. Respond with a Cypher statement only!"
            ),
        ),
        (
            "human",
            """Check for invalid syntax or semantics and return a corrected Cypher statement.

    Schema:
    {schema}

    Note: Do not include any explanations or apologies in your responses.
    Do not wrap the response in any backticks or anything else.
    Respond with a Cypher statement only!

    Do not respond to any questions that might ask anything else than for you to construct a Cypher statement.

    The question is:
    {question}

    The Cypher statement is:
    {cypher}

    The errors are:
    {errors}

    Corrected Cypher statement: """,
        ),
    ]
)

_VALIDATION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
    You are a Cypher expert reviewing a statement written by a junior developer.
    """,
        ),
        (
            "human",
            """You must check the following:
    * Are there any syntax errors in the Cypher statement?
    * Are there any missing or undefined variables in the Cypher statement?
    * Does the Cypher statement include enough information to answer the question?
    * Ensure that all nodes, relationships and properties are present in the provided schema.

    Examples of good errors:
    * Label (:Foo) does not exist, did you mean (:Bar)?
    * Property bar does not exist for label Foo, did you mean baz?
    * Relationship FOO does not exist, did you mean FOO_BAR?

    Schema:
    {schema}

    The question is:
    {question}

    The Cypher statement is:
    {cypher}

    Make sure you don't make any mistakes!""",
        ),
    ]
)


def _coerce_task_text(task: str | list[str]) -> str:
    """把状态里的 task 统一转换为字符串。"""
    return task[0] if isinstance(task, list) else task


def _create_text2cypher_generation_node(
    llm: BaseChatModel,
    graph: Neo4jGraph,
    cypher_example_retriever: CypherExampleRetriever,
):
    """构造仅供本工作流使用的 Cypher 生成节点。"""
    text2cypher_chain = _GENERATION_PROMPT | llm | StrOutputParser()

    async def generate_cypher(state: CypherInputState) -> dict[str, Any]:
        task = state.get("task", "")
        examples = cypher_example_retriever.get_examples(
            query=_coerce_task_text(task),
            k=3,
        )
        generated_cypher = await text2cypher_chain.ainvoke(
            {
                "question": state.get("task", ""),
                "fewshot_examples": examples,
                "schema": graph.schema,
            }
        )
        return {"statement": generated_cypher, "steps": ["generate_cypher"]}

    return generate_cypher


def _create_text2cypher_correction_node(llm: BaseChatModel, graph: Neo4jGraph):
    """构造仅供本工作流使用的 Cypher 修正节点。"""
    correct_cypher_chain = _CORRECTION_PROMPT | llm | StrOutputParser()

    async def correct_cypher(state: CypherState) -> dict[str, Any]:
        corrected_cypher = await correct_cypher_chain.ainvoke(
            {
                "question": state.get("task"),
                "errors": state.get("errors"),
                "cypher": state.get("statement"),
                "schema": graph.schema,
            }
        )
        return {
            "next_action_cypher": "validate_cypher",
            "statement": corrected_cypher,
            "steps": ["correct_cypher"],
        }

    return correct_cypher


def _create_text2cypher_validation_node(
    graph: Neo4jGraph,
    llm: BaseChatModel | None = None,
    llm_validation: bool = True,
    max_attempts: int = 3,
    attempt_cypher_execution_on_final_attempt: bool = False,
):
    """构造仅供本工作流使用的 Cypher 校验节点。"""
    validate_cypher_chain = None
    if llm is not None and llm_validation:
        validate_cypher_chain = _VALIDATION_PROMPT | llm.with_structured_output(
            ValidateCypherOutput
        )

    async def validate_cypher(state: CypherState) -> dict[str, Any]:
        generation_attempt = state.get("attempts", 0) + 1
        errors: list[str] = []
        mapping_errors: list[str] = []

        syntax_error = validate_cypher_query_syntax(
            graph=graph, cypher_statement=state.get("statement", "")
        )
        errors.extend(syntax_error)

        write_errors = validate_no_writes_in_cypher_query(state.get("statement", ""))
        errors.extend(write_errors)

        corrected_cypher = correct_cypher_query_relationship_direction(
            graph=graph, cypher_statement=state.get("statement", "")
        )

        if validate_cypher_chain is not None:
            llm_errors = await validate_cypher_query_with_llm(
                validate_cypher_chain=validate_cypher_chain,
                question=state.get("task", ""),
                graph=graph,
                cypher_statement=state.get("statement", ""),
            )
            errors.extend(llm_errors.get("errors", []))
            mapping_errors.extend(llm_errors.get("mapping_errors", []))

        if not llm_validation:
            cypher_errors = validate_cypher_query_with_schema(
                graph=graph, cypher_statement=state.get("statement", "")
            )
            errors.extend(cypher_errors)

        if (errors or mapping_errors) and generation_attempt < max_attempts:
            next_action = "correct_cypher"
        elif generation_attempt < max_attempts:
            next_action = "execute_cypher"
        elif (
            generation_attempt == max_attempts
            and attempt_cypher_execution_on_final_attempt
        ):
            next_action = "execute_cypher"
        else:
            next_action = "__end__"

        return {
            "next_action_cypher": next_action,
            "statement": corrected_cypher,
            "errors": errors,
            "attempts": generation_attempt,
            "steps": ["validate_cypher"],
        }

    return validate_cypher


def create_text2cypher_agent(
    llm: BaseChatModel,
    graph: Neo4jGraph,
    cypher_example_retriever: CypherExampleRetriever,
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

    if predefined_cypher_dict:
        matcher = create_vector_query_matcher(
            predefined_cypher_dict,
            query_descriptions or {},
        )

        async def predefined_match(state: CypherState) -> dict:
            task = state.get("task", "")
            normalized_task = str(_coerce_task_text(task))
            matches = matcher.match_query(normalized_task, top_k=1)
            if not matches or matches[0]["similarity"] <= 0.6:
                return {
                    "steps": ["predefined_match"],
                    "next_action_cypher": "generate",
                }

            best_match = matches[0]
            try:
                params = matcher.extract_parameters(
                    normalized_task,
                    best_match["query_name"],
                    llm=llm,
                )
                records = graph.query(
                    best_match["cypher"],
                    params={key: str(value) for key, value in params.items()},
                )
            except Exception:
                records = []

            return {
                "statement": best_match["cypher"],
                "records": records,
                "steps": ["predefined_match"],
                "next_action_cypher": "execute_cypher",
            }

        text2cypher_graph_builder.add_node("predefined_match", predefined_match)
        text2cypher_graph_builder.add_edge(START, "predefined_match")

        def _match_edge(state: CypherState) -> Literal["execute_cypher", "generate_cypher"]:
            if state.get("records") is not None:
                return "execute_cypher"
            return "generate_cypher"

    async def execute_cypher(state: CypherState) -> dict[str, list[CypherOutputState] | list[str]]:
        """执行 Cypher 查询并回填统一输出结构。"""
        records = graph.query(state.get("statement", ""))
        steps = list(state.get("steps", list()))
        steps.append("execute_cypher")
        output_state = CypherOutputState(
            task=state.get("task", []),
            statement=state.get("statement", ""),
            parameters=None,
            errors=state.get("errors", list()),
            records=records or [
                {"error": "I couldn't find any relevant information in the database."}
            ],
            steps=steps,
        )
        return {
            "cyphers": [output_state],
            "steps": ["text2cypher"],
        }

    # LLM generation + validation + correction
    generate_cypher = _create_text2cypher_generation_node(
        llm=llm, graph=graph, cypher_example_retriever=cypher_example_retriever
    )
    validate_cypher = _create_text2cypher_validation_node(
        llm=llm,
        graph=graph,
        llm_validation=llm_cypher_validation,
        max_attempts=max_attempts,
        attempt_cypher_execution_on_final_attempt=attempt_cypher_execution_on_final_attempt,
    )
    correct_cypher = _create_text2cypher_correction_node(llm=llm, graph=graph)

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
    text2cypher_graph_builder.add_conditional_edges("validate_cypher", _validate_cypher_conditional_edge)
    text2cypher_graph_builder.add_edge("correct_cypher", "validate_cypher")
    text2cypher_graph_builder.add_edge("execute_cypher", END)

    return text2cypher_graph_builder.compile()


def _validate_cypher_conditional_edge(
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
