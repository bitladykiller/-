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
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Set, Union

from .models import CypherValidationTask, Neo4jStructuredSchemaPropertyNumber

ValidationScope = Literal["Node", "Relationship"]


@dataclass(frozen=True)
class ValidationTaskGroups:
    """按属性类型拆好的校验任务集合。"""

    name_checks: list[CypherValidationTask]
    enum_value_checks: list[CypherValidationTask]
    range_value_checks: list[CypherValidationTask]


def build_validation_task_groups(tasks: List[CypherValidationTask]) -> ValidationTaskGroups:
    """把任务拆成“属性名 / 字符串值 / 数值范围”三组。"""
    return ValidationTaskGroups(
        name_checks=list(tasks),
        enum_value_checks=[task for task in tasks if task.property_type == "STRING"],
        range_value_checks=[
            task
            for task in tasks
            if task.property_type in {"INTEGER", "FLOAT"}
        ],
    )


def validate_property_names_with_enum(
    enum_dict: Dict[str, Set[str]],
    tasks: List[CypherValidationTask],
    node_or_rel: ValidationScope,
) -> List[str]:
    """验证属性名是否存在于 schema 枚举中。"""
    errors: list[str] = []
    for task in tasks:
        error = _validate_property_with_enum(
            enum_dict=enum_dict,
            labels_or_types=task.parsed_labels_or_types,
            node_or_rel=node_or_rel,
            property_name=task.property_name,
        )
        if error:
            errors.append(error)
    return errors


def validate_property_values_with_enum(
    enum_dict: Dict[str, Dict[str, Set[str]]],
    tasks: List[CypherValidationTask],
    node_or_rel: ValidationScope,
) -> List[str]:
    """验证字符串属性值是否落在 schema 枚举范围内。"""
    errors: list[str] = []
    for task in tasks:
        error = _validate_property_value_with_enum(
            enum_dict=enum_dict,
            labels_or_types=task.parsed_labels_or_types,
            node_or_rel=node_or_rel,
            property_name=task.property_name,
            property_value=str(task.property_value),
        )
        if error:
            errors.append(error)
    return errors


def validate_property_values_with_range(
    enum_dict: Dict[str, Dict[str, Neo4jStructuredSchemaPropertyNumber]],
    tasks: List[CypherValidationTask],
    node_or_rel: ValidationScope,
) -> List[str]:
    """验证数值属性是否落在 schema 范围内。"""
    errors: list[str] = []
    for task in tasks:
        error = _validate_property_value_with_range(
            enum_dict=enum_dict,
            labels_or_types=task.parsed_labels_or_types,
            node_or_rel=node_or_rel,
            property_name=task.property_name,
            property_value=task.property_value,
        )
        if error:
            errors.append(error)
    return errors


def _validate_property_value_with_enum(
    enum_dict: Dict[str, Dict[str, Set[str]]],
    labels_or_types: List[str],
    property_name: str,
    node_or_rel: ValidationScope,
    property_value: str,
    and_or: Optional[Literal["and", "or"]] = None,
) -> Optional[str]:
    """验证属性值是否存在于 schema 提供的字符串枚举中。"""
    _assert_validation_scope(node_or_rel)

    if and_or is None and len(labels_or_types) > 1:
        raise ValueError(
            f"Invalid combination of `labels_or_types` and `and_or`: {labels_or_types} | {and_or}"
        )

    invalid_labels_or_types: list[str] = []
    for label_or_type in labels_or_types:
        props = enum_dict.get(label_or_type)
        if props is None:
            continue

        enum = props.get(property_name)
        if enum is None:
            continue

        if property_value not in enum:
            invalid_labels_or_types.append(label_or_type)

    if and_or is None and invalid_labels_or_types:
        return (
            f"{node_or_rel} {labels_or_types} with property {property_name} = "
            f"{property_value} not found in graph database."
        )
    if (
        and_or == "and"
        and 0 < len(invalid_labels_or_types) <= len(labels_or_types)
    ):
        return (
            f"{node_or_rel}(s) {invalid_labels_or_types} with property {property_name} = "
            f"{property_value} not found in graph database."
        )
    if and_or == "or" and len(invalid_labels_or_types) == len(labels_or_types):
        return (
            f"None of {node_or_rel}s {labels_or_types} have property {property_name} = "
            f"{property_value} in graph database."
        )
    return None


def _validate_property_value_with_range(
    enum_dict: Dict[str, Dict[str, Neo4jStructuredSchemaPropertyNumber]],
    labels_or_types: List[str],
    property_name: str,
    node_or_rel: ValidationScope,
    property_value: Union[int, float],
    and_or: Optional[Literal["and", "or"]] = None,
) -> Optional[str]:
    """验证属性值是否落在 schema 给出的数值范围内。"""
    _assert_validation_scope(node_or_rel)

    invalid_labels_or_types: list[tuple[str, Neo4jStructuredSchemaPropertyNumber]] = []
    error_message_invalid_labels_or_types: list[str] = []

    for label_or_type in labels_or_types:
        props = enum_dict.get(label_or_type)
        if props is None:
            continue

        prop_range = props.get(property_name)
        if prop_range is None:
            continue

        if float(property_value) < prop_range.min or float(property_value) > prop_range.max:
            invalid_labels_or_types.append((label_or_type, prop_range))

    for label_or_type, prop_range in invalid_labels_or_types:
        error_message_invalid_labels_or_types.append(
            f"{label_or_type} with property {prop_range.property} range "
            f"{prop_range.min} to {prop_range.max}"
        )

    if and_or is None and invalid_labels_or_types:
        example_label, example_prop = invalid_labels_or_types[0]
        return (
            f"{node_or_rel} {example_label} has property {property_name} = {property_value} "
            f"which is out of range {example_prop.min} to {example_prop.max} in graph database."
        )
    if (
        and_or == "and"
        and 0 < len(invalid_labels_or_types) <= len(labels_or_types)
    ):
        return (
            f"{node_or_rel}(s) {', '.join(error_message_invalid_labels_or_types)} have "
            f"property {property_name} = {property_value} which is out of range in graph database."
        )
    if and_or == "or" and len(invalid_labels_or_types) == len(labels_or_types):
        return (
            f"All of {node_or_rel}s {', '.join(error_message_invalid_labels_or_types)} have "
            f"property {property_name} = {property_value} which is out of range in graph database."
        )
    return None


def _validate_property_with_enum(
    enum_dict: Dict[str, Set[str]],
    labels_or_types: List[str],
    property_name: str,
    node_or_rel: ValidationScope,
    and_or: Optional[Literal["and", "or"]] = None,
) -> Optional[str]:
    """验证属性名是否存在于 schema 提供的属性集合中。"""
    _assert_validation_scope(node_or_rel)

    if and_or is None and len(labels_or_types) > 1:
        raise ValueError(
            f"Invalid combination of `labels_or_types` and `and_or`: {labels_or_types} | {and_or}"
        )

    invalid_labels_or_types: list[str] = []
    for label_or_type in labels_or_types:
        enum = enum_dict.get(label_or_type)
        if enum is None:
            continue
        if property_name not in enum:
            invalid_labels_or_types.append(label_or_type)

    if and_or is None and invalid_labels_or_types:
        return (
            f"{node_or_rel} {labels_or_types} does not have the property "
            f"{property_name} in the graph database."
        )
    if (
        and_or == "and"
        and 0 < len(invalid_labels_or_types) <= len(labels_or_types)
    ):
        return (
            f"{node_or_rel}(s) {invalid_labels_or_types} do(es) not have the property "
            f"{property_name} in the graph database."
        )
    if and_or == "or" and len(invalid_labels_or_types) == len(labels_or_types):
        return (
            f"None of {node_or_rel}s {labels_or_types} have the property "
            f"{property_name} in the graph database."
        )
    return None


def _assert_validation_scope(node_or_rel: str) -> None:
    """防止调用方传入无效作用域，避免错误文案混乱。"""
    assert node_or_rel in {"Node", "Relationship"}, (
        f"Invalid `node_or_rel`: {node_or_rel}"
    )
