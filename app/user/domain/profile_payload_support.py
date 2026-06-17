"""用户画像结构收口与 payload 构造 helper。"""

from __future__ import annotations

from typing import Any

from app.user.domain.schemas import (
    UserProfileData,
    UserProfileFact,
    UserProfilePayload,
)

PROFILE_FIELD_NAMES = (
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
    for field_name in PROFILE_FIELD_NAMES:
        field_value = normalize_optional_text(raw_profile.get(field_name))
        if field_value is not None:
            normalized_profile[field_name] = field_value  # type: ignore[literal-required]

    tags = normalize_profile_tags(raw_profile.get("tags"))
    if tags:
        normalized_profile["tags"] = tags

    facts = normalize_profile_facts(raw_profile.get("facts"))
    if facts:
        normalized_profile["facts"] = facts

    return normalized_profile


def coerce_user_profile_payload(
    user_id: int,
    raw_profile: Any,
) -> UserProfilePayload:
    """把缓存或外部传入的画像收口成带 `user_id` 的完整 payload。"""
    normalized_profile = normalize_profile_data(raw_profile)
    return {
        "user_id": user_id,
        "preferred_brand": normalized_profile.get("preferred_brand"),
        "budget_range": normalized_profile.get("budget_range"),
        "preferred_category": normalized_profile.get("preferred_category"),
        "tags": normalized_profile.get("tags", []),
        "facts": normalized_profile.get("facts", []),
    }


__all__ = [
    "PROFILE_FIELD_NAMES",
    "coerce_user_profile_payload",
    "normalize_profile_data",
    "normalize_optional_text",
    "normalize_profile_facts",
    "normalize_profile_tags",
]
