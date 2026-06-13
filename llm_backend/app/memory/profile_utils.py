"""用户画像共享工具。

这个模块只放跨记忆模块复用的画像辅助逻辑：
- 文本 / 标签 / facts 的标准化
- 松散字典到 `UserProfileData` 的收口
- 对外 payload 的稳定构造
- tags JSON 解码与数据库行到 facts 的转换
- 判断画像是否包含可回写字段
- 遍历有效 facts

这样 `memory_extractor.py`、`memory_middleware.py`、`user_profile_service.py`
可以共享同一套规则，避免同一份过滤逻辑在多处漂移。
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from typing import Any

from app.memory.schemas import UserProfileData, UserProfileFact, UserProfilePayload

_PROFILE_FIELD_NAMES = (
    "preferred_brand",
    "budget_range",
    "preferred_category",
)


def normalize_optional_text(value: Any) -> str | None:
    """把可选文本统一收口为 `非空字符串 | None`。"""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def normalize_profile_tags(raw_tags: Any) -> list[str]:
    """过滤空标签、非字符串标签和重复标签，保持原始顺序。"""
    if not isinstance(raw_tags, list):
        return []

    normalized_tags: list[str] = []
    seen: set[str] = set()
    for item in raw_tags:
        tag = normalize_optional_text(item)
        if not tag or tag in seen:
            continue
        normalized_tags.append(tag)
        seen.add(tag)
    return normalized_tags


def normalize_profile_facts(raw_facts: Any) -> list[UserProfileFact]:
    """把 facts 收口成稳定的 `{key, value}` 数组。"""
    if not isinstance(raw_facts, list):
        return []

    facts: list[UserProfileFact] = []
    for item in raw_facts:
        if not isinstance(item, dict):
            continue
        key = normalize_optional_text(item.get("key"))
        value = normalize_optional_text(item.get("value"))
        if not key or not value:
            continue
        facts.append({"key": key, "value": value})
    return facts


def normalize_profile_data(raw_profile: Any) -> UserProfileData:
    """把松散画像字典裁剪成记忆模块内部使用的稳定结构。"""
    if not isinstance(raw_profile, dict):
        return {}

    normalized_profile: UserProfileData = {}
    for field_name in _PROFILE_FIELD_NAMES:
        field_value = normalize_optional_text(raw_profile.get(field_name))
        if field_value is not None:
            normalized_profile[field_name] = field_value

    tags = normalize_profile_tags(raw_profile.get("tags"))
    if tags:
        normalized_profile["tags"] = tags

    facts = normalize_profile_facts(raw_profile.get("facts"))
    if facts:
        normalized_profile["facts"] = facts

    return normalized_profile


def build_user_profile_payload(
    *,
    user_id: int,
    preferred_brand: str | None,
    budget_range: str | None,
    preferred_category: str | None,
    tags: list[str],
    facts: list[UserProfileFact],
) -> UserProfilePayload:
    """构造稳定的用户画像完整返回结构。"""
    return {
        "user_id": user_id,
        "preferred_brand": preferred_brand,
        "budget_range": budget_range,
        "preferred_category": preferred_category,
        "tags": tags,
        "facts": facts,
    }


def coerce_user_profile_payload(
    user_id: int,
    raw_profile: Any,
) -> UserProfilePayload:
    """把缓存或外部传入的画像收口成带 `user_id` 的完整 payload。"""
    normalized_profile = normalize_profile_data(raw_profile)
    return build_user_profile_payload(
        user_id=user_id,
        preferred_brand=normalized_profile.get("preferred_brand"),
        budget_range=normalized_profile.get("budget_range"),
        preferred_category=normalized_profile.get("preferred_category"),
        tags=normalized_profile.get("tags", []),
        facts=normalized_profile.get("facts", []),
    )


def decode_profile_tags_json(raw_value: str | None) -> list[str]:
    """把数据库中的 tags JSON 安全解码为标签列表。"""
    if not raw_value:
        return []
    try:
        decoded = json.loads(raw_value)
    except (TypeError, ValueError):
        return []
    return normalize_profile_tags(decoded)


def build_user_profile_facts(
    rows: Iterable[Mapping[str, Any]],
) -> list[UserProfileFact]:
    """把数据库 facts 行转换为统一的 `{key, value}` 数组。"""
    facts: list[UserProfileFact] = []
    for row in rows:
        key = normalize_optional_text(row.get("fact_key"))
        value = normalize_optional_text(row.get("fact_value"))
        if not key or not value:
            continue
        facts.append({"key": key, "value": value})
    return facts


def build_profile_field_values(
    *,
    preferred_brand: str | None,
    budget_range: str | None,
    preferred_category: str | None,
    tags: list[str] | None,
) -> dict[str, str]:
    """构造 `user_profiles` 可直接写库的规范化字段值映射。"""
    field_values: dict[str, str] = {}
    normalized_tags = normalize_profile_tags(tags) if tags is not None else None

    text_fields = {
        "preferred_brand": preferred_brand,
        "budget_range": budget_range,
        "preferred_category": preferred_category,
    }
    for field_name, raw_value in text_fields.items():
        normalized_value = normalize_optional_text(raw_value)
        if normalized_value is not None:
            field_values[field_name] = normalized_value

    if normalized_tags is not None:
        field_values["tags"] = json.dumps(normalized_tags, ensure_ascii=False)

    return field_values


def has_profile_fields(profile: UserProfileData) -> bool:
    """判断画像中是否存在需要写入 `user_profiles` 表的主字段。"""
    return any(profile.get(field_name) for field_name in _PROFILE_FIELD_NAMES) or bool(
        profile.get("tags")
    )


def iter_profile_facts(profile: UserProfileData) -> Iterator[tuple[str, str]]:
    """遍历画像中的有效 facts，供回写层逐条消费。"""
    for fact in profile.get("facts") or []:
        key = fact.get("key")
        value = fact.get("value")
        if isinstance(key, str) and key and isinstance(value, str) and value:
            yield key, value
