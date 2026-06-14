"""Text2Cypher 校验入口。

职责：
- 提供 EXPLAIN 语法校验、关系方向修正、LLM 校验和 schema 校验四类对外入口
- 统一编排 Cypher 校验阶段会调用的底层 helper

边界：
- 这里只保留“入口函数”和流程拼装
- 纯 schema 规则已下沉到 `schema_validation_rules.py`
"""

from typing import Any, Dict, List

import regex as re

from langchain_core.runnables.base import Runnable
from langchain_neo4j import Neo4jGraph
from langchain_neo4j.chains.graph_qa.cypher_utils import CypherQueryCorrector, Schema
from neo4j.exceptions import CypherSyntaxError

from .models import (
    CypherValidationTask,
    Neo4jStructuredSchema,
    ValidateCypherOutput,
)
from .schema_validation_rules import (
    build_validation_task_groups,
    validate_property_names_with_enum,
    validate_property_values_with_enum,
    validate_property_values_with_range,
)
from .utils.cypher_extractors import extract_entities_for_validation

_WRITE_CLAUSES = {
    "CREATE",
    "DELETE",
    "DETACH DELETE",
    "SET",
    "REMOVE",
    "FOREACH",
    "MERGE",
}


def _cypher_query_node_graph_schema() -> str:
    """匹配以 '- **CypherQuery**' 开始的段落，直到 Relationship properties 或下一节。"""
    return r"^(- \*\*CypherQuery\*\*[\s\S]+?)(^Relationship properties|- \*)"


def retrieve_and_parse_schema_from_graph_for_prompts(graph: Neo4jGraph) -> str:
    """提取并规整 Neo4j schema，供 Prompt 注入使用。"""
    schema: str = graph.get_schema

    if "CypherQuery" in schema:
        schema = re.sub(
            _cypher_query_node_graph_schema(), r"\2", schema, flags=re.MULTILINE
        )

    return schema.replace("{", "[").replace("}", "]")


def update_task_list_with_property_type(
    tasks: List[CypherValidationTask],
    structure_graph_schema: Neo4jStructuredSchema,
    node_or_rel: str,
) -> List[CypherValidationTask]:
    """为任务列表中的每个条目分配 Neo4j 属性类型。"""
    schema = (
        structure_graph_schema.node_props
        if node_or_rel == "node"
        else structure_graph_schema.rel_props
    )

    for task in tasks:
        found_types = {
            {d.property: d.type for d in schema.get(label_or_type, list())}.get(
                task.property_name
            )
            for label_or_type in task.parsed_labels_or_types
        }
        found_types.discard(None)
        if found_types:
            task.property_type = next(iter(found_types))

    return tasks


def validate_cypher_query_syntax(graph: Neo4jGraph, cypher_statement: str) -> List[str]:
    """
    Validate the Cypher statement syntax by running an EXPLAIN query.

    Parameters
    ----------
    graph : Neo4jGraph
        The Neo4j graph wrapper.
    cypher_statement : str
        The Cypher statement to validate.

    Returns
    -------
    List[str]
        If the statement contains invalid syntax, return an error message in a list
    """
    errors = list()
    try:
        graph.query(f"EXPLAIN {cypher_statement}")
    except CypherSyntaxError as e:
        errors.append(str(e.message))
    return errors


def correct_cypher_query_relationship_direction(
    graph: Neo4jGraph, cypher_statement: str
) -> str:
    """
    Correct Relationship directions in the Cypher statement with LangChain's `CypherQueryCorrector`.

    Parameters
    ----------
    graph : Neo4jGraph
        The Neo4j graph wrapper.
    cypher_statement : str
        The Cypher statement to validate.

    Returns
    -------
    str
        The Cypher statement with corrected Relationship directions.
    """
    # Cypher query corrector is experimental
    corrector_schema = [
        Schema(el["start"], el["type"], el["end"])
        for el in graph.structured_schema.get("relationships", list())
    ]
    cypher_query_corrector = CypherQueryCorrector(corrector_schema)

    corrected_cypher: str = cypher_query_corrector(cypher_statement)

    return corrected_cypher


async def validate_cypher_query_with_llm(
    validate_cypher_chain: Runnable[Dict[str, Any], Any],
    question: str,
    graph: Neo4jGraph,
    cypher_statement: str,
) -> Dict[str, List[str]]:
    """
    Validate the Cypher statement with an LLM.
    Use declared LLM to find Node and Property pairs to validate.
    Validate Node and Property pairs against the Neo4j graph.

    Parameters
    ----------
    validate_cypher_chain : RunnableSerializable
        The LangChain LLM to perform processing.
    question : str
        The question associated with the Cypher statement.
    graph : Neo4jGraph
        The Neo4j graph wrapper.
    cypher_statement : str
        The Cypher statement to validate.

    Returns
    -------
    Dict[str, List[str]]
        A Python dictionary with keys `errors` and `mapping_errors`, each with a list of found errors.
    """

    errors: List[str] = []
    mapping_errors: List[str] = []

    llm_output: ValidateCypherOutput = await validate_cypher_chain.ainvoke(
        {
            "question": question,
            "schema": retrieve_and_parse_schema_from_graph_for_prompts(graph),
            "cypher": cypher_statement,
        }
    )
    if llm_output.errors:
        errors.extend(llm_output.errors)
    if llm_output.filters:
        for filter in llm_output.filters:
            # Do mapping only for string values
            if (
                not [
                    prop
                    for prop in graph.structured_schema["node_props"][filter.node_label]
                    if prop["property"] == filter.property_key
                ][0]["type"]
                == "STRING"
            ):
                continue
            mapping = graph.query(
                f"MATCH (n:{filter.node_label}) WHERE toLower(n.`{filter.property_key}`) = toLower($value) RETURN 'yes' LIMIT 1",
                {"value": filter.property_value},
            )
            if not mapping:
                mapping_error = f"Missing value mapping for {filter.node_label} on property {filter.property_key} with value {filter.property_value}"
                mapping_errors.append(mapping_error)
    return {"errors": errors, "mapping_errors": mapping_errors}


def validate_cypher_query_with_schema(
    graph: Neo4jGraph, cypher_statement: str
) -> List[str]:
    """
    Validate the provided Cypher statement using the schema retrieved from the graph.
    This will ensure the existance of names nodes, relationships and properties.
    This will validate property values with enums and number ranges, if available.
    This method does not use an LLM.

    Parameters
    ----------
    graph : Neo4jGraph
        The Neo4j graph wrapper.
    cypher_statement : str
        The Cypher to be validated.

    Returns
    -------
    List[str]
        A list of any found errors.
    """

    schema: Neo4jStructuredSchema = Neo4jStructuredSchema.model_validate(
        graph.get_structured_schema
    )
    nodes_and_rels = extract_entities_for_validation(cypher_statement=cypher_statement)

    node_tasks = update_task_list_with_property_type(
        nodes_and_rels.get("nodes", list()), schema, "node"
    )
    rel_tasks = update_task_list_with_property_type(
        nodes_and_rels.get("relationships", list()), schema, "rel"
    )
    node_groups = build_validation_task_groups(node_tasks)
    rel_groups = build_validation_task_groups(rel_tasks)

    errors: List[str] = list()

    errors.extend(
        validate_property_names_with_enum(
            schema.get_node_properties_enum(),
            node_groups.name_checks,
            "Node",
        )
    )
    errors.extend(
        validate_property_values_with_enum(
            schema.get_node_property_values_enum(),
            node_groups.enum_value_checks,
            "Node",
        )
    )
    errors.extend(
        validate_property_values_with_range(
            schema.get_node_property_values_range(),
            node_groups.range_value_checks,
            "Node",
        )
    )

    errors.extend(
        validate_property_names_with_enum(
            schema.get_relationship_properties_enum(),
            rel_groups.name_checks,
            "Relationship",
        )
    )
    errors.extend(
        validate_property_values_with_enum(
            schema.get_relationship_property_values_enum(),
            rel_groups.enum_value_checks,
            "Relationship",
        )
    )
    errors.extend(
        validate_property_values_with_range(
            schema.get_relationship_property_values_range(),
            rel_groups.range_value_checks,
            "Relationship",
        )
    )

    return errors


def validate_no_writes_in_cypher_query(cypher_statement: str) -> List[str]:
    """
    Validate whether the provided Cypher contains any write clauses.

    Parameters
    ----------
    cypher_statement : str
        The Cypher statement to validate.

    Returns
    -------
    List[str]
        A list of any found errors.
    """
    errors: List[str] = list()

    for wc in _WRITE_CLAUSES:
        if wc in cypher_statement.upper():
            errors.append(f"Cypher contains write clause: {wc}")

    return errors
