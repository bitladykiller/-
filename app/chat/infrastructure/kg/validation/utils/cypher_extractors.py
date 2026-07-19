from typing import Any

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
) -> dict[str, list[CypherValidationTask]]:
    extracted: dict[str, list[CypherValidationTask]] = {}

    for key, entity_pattern, variable_pattern, label_or_type_pattern in (
        ("nodes", _NODE_PATTERN, _NODE_VARIABLE_PATTERN, _NODE_LABEL_PATTERN),
        (
            "relationships",
            _RELATIONSHIP_PATTERN,
            _RELATIONSHIP_VARIABLE_PATTERN,
            _RELATIONSHIP_TYPE_PATTERN,
        ),
    ):
        tasks: list[dict[str, Any]] = []
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
                match_props_parsed: list[dict[str, Any]] = []
                for part in match_props.split(","):
                    k_and_v = part.split(":")
                    if len(k_and_v) != 2:
                        continue
                    k, v = k_and_v
                    match_props_parsed.append(
                        {
                            "property_name": k.strip().strip("{"),
                            "property_value": v.strip()
                            .strip("}")
                            .replace('"', "")
                            .replace("'", ""),
                        }
                    )
                [
                    e.update({"labels_or_types": label_or_type, "operator": "="})
                    for e in match_props_parsed
                ]
                tasks.extend(match_props_parsed)

            if variable is not None and variable not in used_variables:
                filters = [
                    {
                        "property_name": property_name.strip().strip("{"),
                        "operator": operator.strip(),
                        "property_value": property_value.strip()
                        .strip("}")
                        .replace('"', "")
                        .replace("'", ""),
                    }
                    for property_name, operator, property_value in re.findall(
                        get_variable_operator_property_pattern(variable=variable),
                        cypher_statement,
                    )
                ]
                [e.update({"labels_or_types": label_or_type}) for e in filters]
                tasks.extend(filters)

            used_variables.add(variable)

        extracted[key] = [
            CypherValidationTask.model_validate(task)
            for task in tasks
        ]

    return extracted
