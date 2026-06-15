"""Text2Cypher schema 校验纯规则。

这个模块负责：
- 按属性类型拆分校验任务
- 执行属性名、字符串枚举值、数值范围三类纯规则校验
- 生成稳定的错误文案，供 `validators.py` 统一编排

这个模块不负责：
- 调用 LLM
- 调用 Neo4j EXPLAIN 或关系方向修正
- 解析 Cypher 文本
"""

from typing import Dict, List, Literal, Set

from .models import CypherValidationTask, Neo4jStructuredSchemaPropertyNumber


def validate_property_names_with_enum(
    enum_dict: Dict[str, Set[str]],
    tasks: List[CypherValidationTask],
    node_or_rel: Literal["Node", "Relationship"],
) -> List[str]:
    """验证属性名是否存在于 schema 枚举中。"""
    assert node_or_rel in {"Node", "Relationship"}, (
        f"Invalid `node_or_rel`: {node_or_rel}"
    )

    errors: list[str] = []
    for task in tasks:
        labels_or_types = task.parsed_labels_or_types
        if len(labels_or_types) > 1:
            raise ValueError(
                f"Invalid combination of `labels_or_types` and `and_or`: {labels_or_types} | None"
            )

        invalid_labels_or_types: list[str] = []
        for label_or_type in labels_or_types:
            enum = enum_dict.get(label_or_type)
            if enum is None:
                continue
            if task.property_name not in enum:
                invalid_labels_or_types.append(label_or_type)

        if invalid_labels_or_types:
            errors.append(
                f"{node_or_rel} {labels_or_types} does not have the property "
                f"{task.property_name} in the graph database."
            )
    return errors


def validate_property_values_with_enum(
    enum_dict: Dict[str, Dict[str, Set[str]]],
    tasks: List[CypherValidationTask],
    node_or_rel: Literal["Node", "Relationship"],
) -> List[str]:
    """验证字符串属性值是否落在 schema 枚举范围内。"""
    assert node_or_rel in {"Node", "Relationship"}, (
        f"Invalid `node_or_rel`: {node_or_rel}"
    )

    errors: list[str] = []
    for task in tasks:
        labels_or_types = task.parsed_labels_or_types
        if len(labels_or_types) > 1:
            raise ValueError(
                f"Invalid combination of `labels_or_types` and `and_or`: {labels_or_types} | None"
            )

        invalid_labels_or_types: list[str] = []
        for label_or_type in labels_or_types:
            props = enum_dict.get(label_or_type)
            if props is None:
                continue

            enum = props.get(task.property_name)
            if enum is None:
                continue

            if str(task.property_value) not in enum:
                invalid_labels_or_types.append(label_or_type)

        if invalid_labels_or_types:
            errors.append(
                f"{node_or_rel} {labels_or_types} with property {task.property_name} = "
                f"{task.property_value} not found in graph database."
            )
    return errors


def validate_property_values_with_range(
    enum_dict: Dict[str, Dict[str, Neo4jStructuredSchemaPropertyNumber]],
    tasks: List[CypherValidationTask],
    node_or_rel: Literal["Node", "Relationship"],
) -> List[str]:
    """验证数值属性是否落在 schema 范围内。"""
    assert node_or_rel in {"Node", "Relationship"}, (
        f"Invalid `node_or_rel`: {node_or_rel}"
    )

    errors: list[str] = []
    for task in tasks:
        invalid_labels_or_types: list[tuple[str, Neo4jStructuredSchemaPropertyNumber]] = []
        labels_or_types = task.parsed_labels_or_types

        for label_or_type in labels_or_types:
            props = enum_dict.get(label_or_type)
            if props is None:
                continue

            prop_range = props.get(task.property_name)
            if prop_range is None:
                continue

            if (
                float(task.property_value) < prop_range.min
                or float(task.property_value) > prop_range.max
            ):
                invalid_labels_or_types.append((label_or_type, prop_range))

        if invalid_labels_or_types:
            example_label, example_prop = invalid_labels_or_types[0]
            errors.append(
                f"{node_or_rel} {example_label} has property {task.property_name} = "
                f"{task.property_value} which is out of range "
                f"{example_prop.min} to {example_prop.max} in graph database."
            )
    return errors
