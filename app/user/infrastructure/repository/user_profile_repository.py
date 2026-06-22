"""用户画像 Repository 层。

负责：
- 封装所有用户画像相关的数据库操作
- 接收 AsyncSession 作为参数，由 Service 层控制事务边界
- 返回领域模型或原始数据结构

不负责：
- Redis 缓存逻辑
- 业务规则校验
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.domain.schemas import UserProfileData, UserProfileFact, UserProfilePayload
from app.knowledge.infrastructure.profile.profile_payload_support import (
    PROFILE_FIELD_NAMES,
    normalize_optional_text,
    normalize_profile_tags,
)

_PROFILE_QUERY_SQL = text(
    "SELECT preferred_brand, budget_range, preferred_category, tags "
    "FROM user_profiles WHERE user_id = :uid"
)
_ACTIVE_FACTS_QUERY_SQL = text(
    "SELECT fact_key, fact_value FROM user_facts "
    "WHERE user_id = :uid AND is_active = TRUE"
)
_CURRENT_FACT_VERSION_SQL = text(
    "SELECT id, fact_value, version FROM user_facts "
    "WHERE user_id = :uid AND fact_key = :key AND is_active = TRUE"
)
_DEACTIVATE_FACT_SQL = text(
    "UPDATE user_facts SET is_active = FALSE, superseded_by = NULL WHERE id = :id"
)
_INSERT_VERSIONED_FACT_SQL = text(
    "INSERT INTO user_facts (user_id, fact_key, fact_value, version) "
    "VALUES (:uid, :key, :val, :ver)"
)
_INSERT_FACT_SQL = text(
    "INSERT INTO user_facts (user_id, fact_key, fact_value) "
    "VALUES (:uid, :key, :val)"
)
_LINK_SUPERSEDED_FACT_SQL = text(
    "UPDATE user_facts SET superseded_by = :new_id WHERE id = :old_id"
)
_LAST_INSERT_ID_SQL = text("SELECT LAST_INSERT_ID()")


def _decode_profile_tags_json(raw_value: str | None) -> list[str]:
    """把数据库中的 tags JSON 安全解码为标签列表。"""
    if not raw_value:
        return []
    try:
        decoded = json.loads(raw_value)
    except (TypeError, ValueError):
        return []
    return normalize_profile_tags(decoded)


def _build_user_profile_facts(rows: Iterable[Mapping[str, Any]]) -> list[UserProfileFact]:
    """把数据库 facts 行转换为统一的 `{key, value}` 数组。"""
    facts: list[UserProfileFact] = []
    for row in rows:
        key = normalize_optional_text(row.get("fact_key"))
        value = normalize_optional_text(row.get("fact_value"))
        if not key or not value:
            continue
        facts.append({"key": key, "value": value})
    return facts


def _build_profile_field_values(
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


class UserProfileRepository:
    """用户画像数据访问 Repository。"""

    @staticmethod
    def empty_profile(user_id: int) -> UserProfilePayload:
        """返回默认空画像结构。"""
        return {
            "user_id": user_id,
            "preferred_brand": None,
            "budget_range": None,
            "preferred_category": None,
            "tags": [],
            "facts": [],
        }

    async def get_profile(self, db: AsyncSession, user_id: int) -> UserProfilePayload:
        """从数据库读取用户画像和激活中的 facts。"""
        profile = self.empty_profile(user_id)

        profile_row = (await db.execute(_PROFILE_QUERY_SQL, {"uid": user_id})).mappings().first()
        if profile_row:
            profile["preferred_brand"] = normalize_optional_text(profile_row.get("preferred_brand"))
            profile["budget_range"] = normalize_optional_text(profile_row.get("budget_range"))
            profile["preferred_category"] = normalize_optional_text(profile_row.get("preferred_category"))
            profile["tags"] = _decode_profile_tags_json(profile_row.get("tags"))

        fact_rows = (await db.execute(_ACTIVE_FACTS_QUERY_SQL, {"uid": user_id})).mappings().all()
        profile["facts"] = _build_user_profile_facts(fact_rows)

        return profile

    async def upsert_profile_fields(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        preferred_brand: str | None,
        budget_range: str | None,
        preferred_category: str | None,
        tags: list[str] | None,
    ) -> bool:
        """根据传入画像字段执行 upsert。无有效字段时返回 False。"""
        field_values = _build_profile_field_values(
            preferred_brand=preferred_brand,
            budget_range=budget_range,
            preferred_category=preferred_category,
            tags=tags,
        )
        if not field_values:
            return False

        columns = list(field_values)
        placeholders = [f":{column}" for column in columns]
        assignments = [f"{field_name} = :{field_name}" for field_name in columns]
        await db.execute(
            text(
                "INSERT INTO user_profiles "
                f"(user_id, {', '.join(columns)}) "
                f"VALUES (:uid, {', '.join(placeholders)}) "
                f"ON DUPLICATE KEY UPDATE {', '.join(assignments)}"
            ),
            {"uid": user_id, **field_values},
        )
        return True

    async def upsert_fact(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        fact_key: str,
        fact_value: str,
    ) -> bool:
        """在单个事务内完成事实更新，返回本次是否真的改动了数据。"""
        row = (
            await db.execute(_CURRENT_FACT_VERSION_SQL, {"uid": user_id, "key": fact_key})
        ).mappings().first()
        if row:
            if row["fact_value"] == fact_value:
                return False

            await db.execute(_DEACTIVATE_FACT_SQL, {"id": row["id"]})
            await db.execute(
                _INSERT_VERSIONED_FACT_SQL,
                {
                    "uid": user_id,
                    "key": fact_key,
                    "val": fact_value,
                    "ver": row["version"] + 1,
                },
            )
            new_id = (await db.execute(_LAST_INSERT_ID_SQL)).scalar()
            await db.execute(
                _LINK_SUPERSEDED_FACT_SQL,
                {"new_id": new_id, "old_id": row["id"]},
            )
            return True

        await db.execute(
            _INSERT_FACT_SQL,
            {"uid": user_id, "key": fact_key, "val": fact_value},
        )
        return True

    async def upsert_profile_data(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        profile: UserProfileData,
    ) -> bool:
        """在单个事务里批量回写画像字段和结构化 facts。"""
        data_changed = False

        if any(profile.get(field_name) for field_name in PROFILE_FIELD_NAMES) or bool(
            profile.get("tags")
        ):
            profile_changed = await self.upsert_profile_fields(
                db,
                user_id=user_id,
                preferred_brand=profile.get("preferred_brand"),
                budget_range=profile.get("budget_range"),
                preferred_category=profile.get("preferred_category"),
                tags=profile.get("tags"),
            )
            data_changed = data_changed or profile_changed

        for fact in profile.get("facts") or []:
            fact_key = fact.get("key")
            fact_value = fact.get("value")
            if not (
                isinstance(fact_key, str)
                and fact_key
                and isinstance(fact_value, str)
                and fact_value
            ):
                continue
            fact_changed = await self.upsert_fact(
                db,
                user_id=user_id,
                fact_key=fact_key,
                fact_value=fact_value,
            )
            data_changed = data_changed or fact_changed

        return data_changed


user_profile_repository = UserProfileRepository()

__all__ = ["UserProfileRepository", "user_profile_repository"]
