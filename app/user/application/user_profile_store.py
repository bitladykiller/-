"""用户画像数据访问层。

负责：
- 从 MySQL 读取结构化画像和激活中的用户事实
- 在事务中回写画像字段与版本化 facts
- 编排 `user_profiles` / `user_facts` 的读写主流程

不负责：
- Redis 缓存
- 服务层对外接口
- HTTP / Agent 编排
- 纯 payload 构造和 facts 版本链细节
"""

import json

from app.knowledge.domain.schemas import (
    UserProfileData,
    UserProfileFact,
)
from app.knowledge.infrastructure.profile.profile_payload_support import (
    PROFILE_FIELD_NAMES,
    coerce_user_profile_data,
    normalize_optional_text,
    normalize_profile_tags,
)
from app.shared.core.database import AsyncSessionLocal
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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


async def upsert_profile_fields_in_db(
    db: AsyncSession,
    *,
    user_id: int,
    preferred_brand: str | None,
    budget_range: str | None,
    preferred_category: str | None,
    tags: list[str] | None,
) -> bool:
    """根据传入画像字段执行 upsert。无有效字段时返回 False。"""
    field_values: dict[str, str] = {}
    text_fields = {
        "preferred_brand": preferred_brand,
        "budget_range": budget_range,
        "preferred_category": preferred_category,
    }
    for field_name, raw_value in text_fields.items():
        normalized_value = normalize_optional_text(raw_value)
        if normalized_value is not None:
            field_values[field_name] = normalized_value

    if tags is not None:
        field_values["tags"] = json.dumps(
            normalize_profile_tags(tags),
            ensure_ascii=False,
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


async def upsert_fact_in_db(
    db: AsyncSession,
    *,
    user_id: int,
    fact_key: str,
    fact_value: str,
) -> bool:
    """在单个事务内完成事实更新，返回本次是否真的改动了数据。"""
    row = (
        await db.execute(
            _CURRENT_FACT_VERSION_SQL,
            {"uid": user_id, "key": fact_key},
        )
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


async def query_profile_from_db(user_id: int) -> UserProfileData:
    """从 MySQL 读取用户画像和激活中的 facts。"""
    profile = coerce_user_profile_data({})

    async with AsyncSessionLocal() as db:
        profile_row = (
            await db.execute(_PROFILE_QUERY_SQL, {"uid": user_id})
        ).mappings().first()
        if profile_row:
            profile["preferred_brand"] = normalize_optional_text(
                profile_row.get("preferred_brand")
            )
            profile["budget_range"] = normalize_optional_text(
                profile_row.get("budget_range")
            )
            profile["preferred_category"] = normalize_optional_text(
                profile_row.get("preferred_category")
            )
            raw_tags = profile_row.get("tags")
            if raw_tags:
                try:
                    profile["tags"] = normalize_profile_tags(json.loads(raw_tags))
                except (TypeError, ValueError):
                    profile["tags"] = []

        fact_rows = (
            await db.execute(_ACTIVE_FACTS_QUERY_SQL, {"uid": user_id})
        ).mappings().all()
        facts: list[UserProfileFact] = []
        for row in fact_rows:
            key = normalize_optional_text(row.get("fact_key"))
            value = normalize_optional_text(row.get("fact_value"))
            if not key or not value:
                continue
            facts.append({"key": key, "value": value})
        profile["facts"] = facts

    return profile


async def upsert_profile_data_in_db(
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
        profile_changed = await upsert_profile_fields_in_db(
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
        fact_changed = await upsert_fact_in_db(
            db,
            user_id=user_id,
            fact_key=fact_key,
            fact_value=fact_value,
        )
        data_changed = data_changed or fact_changed

    return data_changed
