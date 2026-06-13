"""用户画像 store 的纯 helper。

这个模块负责：
- 构造空画像 payload
- 构造 `user_profiles` 的 upsert SQL
- 合并单行画像查询结果
- 归一化待写入字段并构造 SQL 参数

这个模块不负责：
- 发起数据库查询
- 控制事务提交
- 处理 facts 版本链
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeAlias

from app.memory.profile_utils import (
    build_profile_field_values,
    build_user_profile_payload,
    decode_profile_tags_json,
    normalize_optional_text,
)
from app.memory.schemas import UserProfilePayload

ProfileRow: TypeAlias = Mapping[str, Any]


def empty_user_profile(user_id: int) -> UserProfilePayload:
    """返回默认空画像结构。"""
    return build_user_profile_payload(
        user_id=user_id,
        preferred_brand=None,
        budget_range=None,
        preferred_category=None,
        tags=[],
        facts=[],
    )


def build_profile_upsert_sql(columns: list[str], assignments: list[str]) -> str:
    """根据待更新字段构造 `user_profiles` 的 upsert SQL。"""
    placeholders = [f":{column}" for column in columns]
    return (
        f"INSERT INTO user_profiles (user_id, {', '.join(columns)}) "
        f"VALUES (:uid, {', '.join(placeholders)}) "
        f"ON DUPLICATE KEY UPDATE {', '.join(assignments)}"
    )


def merge_profile_row(
    profile: UserProfilePayload,
    row: ProfileRow | None,
) -> None:
    """把 `user_profiles` 表中的单行结果合并进画像结构。"""
    if not row:
        return
    profile["preferred_brand"] = normalize_optional_text(row.get("preferred_brand"))
    profile["budget_range"] = normalize_optional_text(row.get("budget_range"))
    profile["preferred_category"] = normalize_optional_text(
        row.get("preferred_category")
    )
    profile["tags"] = decode_profile_tags_json(row.get("tags"))


def build_profile_update_parts(
    *,
    preferred_brand: str | None,
    budget_range: str | None,
    preferred_category: str | None,
    tags: list[str] | None,
    user_id: int,
) -> tuple[list[str], list[str], dict[str, Any]]:
    """根据传入字段构造 upsert 所需的列、赋值语句和参数。"""
    params: dict[str, Any] = {"uid": user_id}
    field_values = build_profile_field_values(
        preferred_brand=preferred_brand,
        budget_range=budget_range,
        preferred_category=preferred_category,
        tags=tags,
    )

    columns: list[str] = []
    assignments: list[str] = []
    for field_name, value in field_values.items():
        columns.append(field_name)
        assignments.append(f"{field_name} = :{field_name}")
        params[field_name] = value

    return columns, assignments, params
