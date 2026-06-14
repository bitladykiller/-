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
    nodes = _extract_entities_from_cypher_statement(
        cypher_statement,
        entity_pattern=_NODE_PATTERN,
        variable_pattern=_NODE_VARIABLE_PATTERN,
        label_or_type_pattern=_NODE_LABEL_PATTERN,
    )
    rels = _extract_entities_from_cypher_statement(
        cypher_statement,
        entity_pattern=_RELATIONSHIP_PATTERN,
        variable_pattern=_RELATIONSHIP_VARIABLE_PATTERN,
        label_or_type_pattern=_RELATIONSHIP_TYPE_PATTERN,
    )

    return {"nodes": nodes, "relationships": rels}


def _extract_entities_from_cypher_statement(
    cypher_statement: str,
    *,
    entity_pattern: re.Pattern,
    variable_pattern: re.Pattern,
    label_or_type_pattern: re.Pattern,
) -> List[CypherValidationTask]:
    """提取节点或关系的属性校验任务。"""
    tasks: List[Dict[str, Any]] = []
    entities = re.findall(entity_pattern, cypher_statement)
    used_variables: set[str | None] = set()

    for entity in entities:
        variables = re.findall(variable_pattern, entity)
        labels_or_types = [
            label_or_type.strip()
            for label_or_type in re.findall(label_or_type_pattern, entity)
        ]

        variable = variables[0] if variables and variables[0] else None
        label_or_type = labels_or_types[0] if labels_or_types else None
        match_props = re.findall(_PROPERTY_PATTERN, entity)
        match_props = match_props[0] if match_props and match_props[0] else None

        if match_props is not None:
            match_props_parsed = _process_match_clause_property_ids(match_props)
            [
                e.update({"labels_or_types": label_or_type, "operator": "="})
                for e in match_props_parsed
            ]
            tasks.extend(match_props_parsed)

        if variable is not None and variable not in used_variables:
            filters: List[Dict[str, Any]] = _find_all_filters(
                variable=variable, cypher_statement=cypher_statement
            )
            [e.update({"labels_or_types": label_or_type}) for e in filters]
            tasks.extend(filters)

        used_variables.add(variable)

    return [CypherValidationTask.model_validate(task) for task in tasks]


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
