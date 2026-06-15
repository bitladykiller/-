"""Text2Cypher 单 Agent 图组装。

这个模块负责：
- 组装“预定义模板快速路径 + LLM 生成兜底路径”的 LangGraph 图
- 把模板匹配策略和 Cypher 生成节点串成统一入口

这个模块不负责：
- 具体模板匹配实现
- Cypher 校验规则定义
- Neo4j 连接管理
"""

from collections.abc import Callable
from operator import add
from typing import Annotated, Any

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_neo4j import Neo4jGraph
from langgraph.constants import END, START
from langgraph.graph.state import CompiledStateGraph, StateGraph
from typing_extensions import TypedDict

from ...components.predefined_cypher.utils import _VectorQueryMatcher
from ...components.text2cypher.validation.models import ValidateCypherOutput
from ...components.text2cypher.validation.validators import (
    correct_cypher_query_relationship_direction,
    validate_cypher_query_syntax,
    validate_cypher_query_with_llm,
    validate_cypher_query_with_schema,
    validate_no_writes_in_cypher_query,
)


class CypherState(TypedDict):
    task: str
    params: dict[str, str]
    statement: str
    errors: list[str]
    next_action_cypher: str
    attempts: int
    steps: Annotated[list[str], add]


class OverallState(TypedDict):
    cyphers: Annotated[list[dict[str, Any]], add]
    steps: Annotated[list[str], add]


def create_text2cypher_agent(
    llm: BaseChatModel,
    graph: Neo4jGraph,
    get_examples: Callable[[str, int], str],
    predefined_cypher_dict: dict[str, str],
    query_descriptions: dict[str, str],
    llm_cypher_validation: bool = True,
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
    generation_limit = 3
    text2cypher_graph_builder = StateGraph(
        CypherState,
        output_schema=OverallState,
    )
    generation_prompt = ChatPromptTemplate.from_messages(
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
    correction_prompt = ChatPromptTemplate.from_messages(
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
    validation_prompt = ChatPromptTemplate.from_messages(
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

    matcher = _VectorQueryMatcher(
        predefined_cypher_dict,
        query_descriptions,
    )

    async def predefined_match(state: CypherState) -> dict:
        normalized_task = state["task"]
        matches = matcher.match_query(normalized_task, top_k=1)
        if not matches or matches[0]["similarity"] <= 0.6:
            return {
                "steps": ["predefined_match"],
                "next_action_cypher": "generate_cypher",
            }

        best_match = matches[0]
        params = matcher.extract_parameters(
            normalized_task,
            best_match["query_name"],
            llm=llm,
        )

        return {
            "params": {key: str(value) for key, value in params.items()},
            "statement": best_match["cypher"],
            "errors": [],
            "steps": ["predefined_match"],
            "next_action_cypher": "execute_cypher",
        }

    text2cypher_graph_builder.add_node("predefined_match", predefined_match)
    text2cypher_graph_builder.add_edge(START, "predefined_match")

    async def execute_cypher(state: CypherState) -> dict[str, list[dict[str, Any]] | list[str]]:
        """执行 Cypher 查询并回填统一输出结构。"""
        records = graph.query(
            state["statement"],
            params=state["params"],
        )
        steps = list(state["steps"])
        steps.append("execute_cypher")
        output_state = {
            "task": state["task"],
            "statement": state["statement"],
            "errors": state["errors"],
            "records": records or [
                {"error": "I couldn't find any relevant information in the database."}
            ],
            "steps": steps,
        }
        return {
            "cyphers": [output_state],
            "steps": ["text2cypher"],
        }

    # LLM generation + validation + correction
    async def generate_cypher(state: CypherState) -> dict[str, Any]:
        text2cypher_chain = generation_prompt | llm | StrOutputParser()
        examples = get_examples(state["task"], 3)
        generated_cypher = await text2cypher_chain.ainvoke(
            {
                "question": state["task"],
                "fewshot_examples": examples,
                "schema": graph.schema,
            }
        )
        return {
            "attempts": 0,
            "params": {},
            "statement": generated_cypher,
            "steps": ["generate_cypher"],
        }

    async def validate_cypher(state: CypherState) -> dict[str, Any]:
        generation_attempt = state["attempts"] + 1
        errors: list[str] = []
        mapping_errors: list[str] = []

        syntax_error = validate_cypher_query_syntax(
            graph=graph, cypher_statement=state["statement"]
        )
        errors.extend(syntax_error)

        write_errors = validate_no_writes_in_cypher_query(state["statement"])
        errors.extend(write_errors)

        corrected_cypher = correct_cypher_query_relationship_direction(
            graph=graph, cypher_statement=state["statement"]
        )

        if llm_cypher_validation:
            validate_cypher_chain = validation_prompt | llm.with_structured_output(
                ValidateCypherOutput
            )
            llm_errors = await validate_cypher_query_with_llm(
                validate_cypher_chain=validate_cypher_chain,
                question=state["task"],
                graph=graph,
                cypher_statement=state["statement"],
            )
            errors.extend(llm_errors["errors"])
            mapping_errors.extend(llm_errors["mapping_errors"])
        else:
            cypher_errors = validate_cypher_query_with_schema(
                graph=graph, cypher_statement=state["statement"]
            )
            errors.extend(cypher_errors)

        if (errors or mapping_errors) and generation_attempt < generation_limit:
            next_action = "correct_cypher"
        elif generation_attempt < generation_limit:
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

    async def correct_cypher(state: CypherState) -> dict[str, Any]:
        correct_cypher_chain = correction_prompt | llm | StrOutputParser()
        corrected_cypher = await correct_cypher_chain.ainvoke(
            {
                "question": state["task"],
                "errors": state["errors"],
                "cypher": state["statement"],
                "schema": graph.schema,
            }
        )
        return {
            "next_action_cypher": "validate_cypher",
            "statement": corrected_cypher,
            "steps": ["correct_cypher"],
        }

    text2cypher_graph_builder.add_node("generate_cypher", generate_cypher)
    text2cypher_graph_builder.add_node("validate_cypher", validate_cypher)
    text2cypher_graph_builder.add_node("correct_cypher", correct_cypher)
    text2cypher_graph_builder.add_node("execute_cypher", execute_cypher)

    text2cypher_graph_builder.add_conditional_edges(
        "predefined_match",
        lambda state: state["next_action_cypher"],
        {
            "execute_cypher": "execute_cypher",
            "generate_cypher": "generate_cypher",
        },
    )

    text2cypher_graph_builder.add_edge("generate_cypher", "validate_cypher")
    text2cypher_graph_builder.add_conditional_edges(
        "validate_cypher",
        lambda state: state["next_action_cypher"],
    )
    text2cypher_graph_builder.add_edge("correct_cypher", "validate_cypher")
    text2cypher_graph_builder.add_edge("execute_cypher", END)

    return text2cypher_graph_builder.compile()
