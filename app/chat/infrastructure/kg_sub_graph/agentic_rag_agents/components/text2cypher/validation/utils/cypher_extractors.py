from typing import Any, Dict, List, Tuple

import regex as re

from ..models import CypherValidationTask

_PROPERTY_PATTERN = re.compile(r"\{.+?\}")
_NODE_VARIABLE_PATTERN = re.compile(r"^\(([a-zA-Z\_\d]*)(?:[\s]{0,1}[:\{])")
_RELATIONSHIP_VARIABLE_PATTERN = re.compile(r"^([\w\d]*):?")
_RELATIONSHIP_PATTERN = re.compile(r"-\[([\w\:\{\s\"\'\}\,\|\&]+)\]-")
_NODE_PATTERN = re.compile(r"(\([\w\:\{\s\"\'\}\,\|\&]+\))")
_NODE_LABEL_PATTERN = re.compile(r"\([^:\{]*:\`?([a-zA-Z\_\d\s\|\&]*)\`?[\s\_\{\)]")
_RELATIONSHIP_TYPE_PATTERN = re.compile(r":([\w\|\&\:]+?)[\]\s\{]+")

def get_variable_operator_property_pattern(variable: str) -> re.Pattern:
    """
    Should be run on an entire Cypher Statement. The variable parameter must be gathered in a prior step.

    Parameters
    ----------
    variable : str
        The variable of interest.

    Returns
    -------
    re.Pattern
        The regex pattern.
    """
    return (
        re.escape(variable)
        + r"\.(?P<property_name>[^\s]*)\s(?P<operator>contains|CONTAINS|[><=]{0,2}|starts with|STARTS WITH|ends with|ENDS WITH)\s\"?\'?(?P<property_value>[\w\s]+\"|[\d]+)\"?\'?"
    )


def extract_entities_for_validation(
    cypher_statement: str,
) -> Dict[str, List[CypherValidationTask]]:
    nodes = _extract_nodes_and_properties_from_cypher_statement(cypher_statement)
    rels = _extract_relationships_and_properties_from_cypher_statement(cypher_statement)

    return {"nodes": nodes, "relationships": rels}


def _extract_nodes_and_properties_from_cypher_statement(
    cypher_statement: str,
) -> List[CypherValidationTask]:
    """
    Extract Node and Property pairs from the Cypher statement.

    Parameters
    ----------
    cypher_statement : str
        The statement.

    Returns
    -------
    List[CypherValidationTask]
        A List of CypherValidationTasks with keys `labels`, `operator`, `property_name` and `property_value`.
    """
    tasks = list()

    nodes = re.findall(_NODE_PATTERN, cypher_statement)
    used_variables = set()
    # find all variable assignments and process match clauses
    for n in nodes:
        variables = re.findall(_NODE_VARIABLE_PATTERN, n)
        labels = [label.strip() for label in re.findall(_NODE_LABEL_PATTERN, n)]

        k = variables[0] if variables and variables[0] else None
        label = labels[0] if len(labels) > 0 else None
        match_props = re.findall(_PROPERTY_PATTERN, n)
        match_props = match_props[0] if match_props and match_props[0] else None
        # process ids in the MATCH clause
        if match_props is not None:
            match_props_parsed: List[Dict[str, Any]] = (
                _process_match_clause_property_ids(match_props)
            )
            [
                e.update({"labels_or_types": label, "operator": "="})
                for e in match_props_parsed
            ]
            tasks.extend(match_props_parsed)

        # find and process property filters based on variables
        if k is not None and k not in used_variables:
            filters: List[Dict[str, Any]] = _find_all_filters(
                variable=k, cypher_statement=cypher_statement
            )
            [e.update({"labels_or_types": label}) for e in filters]
            tasks.extend(filters)

        used_variables.add(k)

    # validate all found tasks
    validated_tasks = [CypherValidationTask.model_validate(task) for task in tasks]
    return validated_tasks


def _extract_relationships_and_properties_from_cypher_statement(
    cypher_statement: str,
) -> List[CypherValidationTask]:
    """
    Extract Relationship and Property pairs from the Cypher statement.

    Parameters
    ----------
    cypher_statement : str
        The statement.

    Returns
    -------
    List[CypherValidationTask]
        A List of CypherValidationTasks with keys `rel_types`, `operator`, `property_name` and `property_value`.
    """
    tasks = list()

    rels = re.findall(_RELATIONSHIP_PATTERN, cypher_statement)
    used_variables = set()

    # find all variable assignments and process match clauses
    for n in rels:
        variables = re.findall(_RELATIONSHIP_VARIABLE_PATTERN, n)
        rel_types = [
            relationship_type.strip()
            for relationship_type in re.findall(_RELATIONSHIP_TYPE_PATTERN, n)
        ]

        rel_type = rel_types[0] if len(rel_types) > 0 else None
        k = variables[0] if variables and variables[0] else None

        match_props = re.findall(_PROPERTY_PATTERN, n)
        match_props = match_props[0] if match_props and match_props[0] else None
        # process ids in the MATCH clause
        if match_props is not None:
            match_props_parsed: List[Dict[str, Any]] = (
                _process_match_clause_property_ids(match_props)
            )
            [
                e.update({"labels_or_types": rel_type, "operator": "="})
                for e in match_props_parsed
            ]
            tasks.extend(match_props_parsed)

        # find and process property filters based on variables
        if k is not None and k not in used_variables:
            filters: List[Dict[str, Any]] = _find_all_filters(
                variable=k, cypher_statement=cypher_statement
            )
            [e.update({"labels_or_types": rel_type}) for e in filters]
            tasks.extend(filters)
        used_variables.add(k)

    # validate all found tasks
    validated_tasks = [CypherValidationTask.model_validate(task) for task in tasks]

    return validated_tasks


def _process_match_clause_property_ids(
    match_clause_section: str,
) -> List[Dict[str, Any]]:
    parts = match_clause_section.split(",")
    result = list()
    for part in parts:
        k_and_v = part.split(":")
        if len(k_and_v) == 2:
            k, v = k_and_v
        else:
            continue
        result.append(
            {
                "property_name": k.strip().strip("{"),
                "property_value": v.strip().strip("}").replace('"', "").replace("'", ""),
            }
        )
    return result


def _find_all_filters(variable: str, cypher_statement: str) -> List[Dict[str, Any]]:
    res: List[Tuple[str, str, Any]] = re.findall(
        get_variable_operator_property_pattern(variable=variable), cypher_statement
    )

    return [
        {
            "property_name": n[0].strip().strip("{"),
            "operator": n[1].strip(),
            "property_value": n[2].strip().strip("}").replace('"', "").replace("'", ""),
        }
        for n in res
    ]
