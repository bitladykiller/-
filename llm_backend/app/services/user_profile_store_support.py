"""`user_profile_store.py` 共享的数据库 helper。

职责：
- 收敛 `user_profiles` / `user_facts` 查询与更新用到的 SQL 样板
- 承接 facts 版本链更新的数据库操作细节
- 承接画像主字段 upsert 的执行 helper

边界：
- 不负责 Redis 缓存
- 不负责服务层对外入口
- 不负责空画像 payload 构造和字段规整
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.user_profile_store_utils import (
    ProfileRow,
    build_profile_upsert_sql,
    build_profile_update_parts,
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


async def load_profile_row(
    db: AsyncSession,
    user_id: int,
) -> ProfileRow | None:
    """读取 `user_profiles` 的单行画像结果。"""
    return (
        await db.execute(_PROFILE_QUERY_SQL, {"uid": user_id})
    ).mappings().first()


async def load_active_fact_rows(
    db: AsyncSession,
    user_id: int,
) -> list[ProfileRow]:
    """读取当前激活的全部用户 facts。"""
    return (
        await db.execute(_ACTIVE_FACTS_QUERY_SQL, {"uid": user_id})
    ).mappings().all()


async def fetch_current_fact_version(
    db: AsyncSession,
    user_id: int,
    fact_key: str,
) -> ProfileRow | None:
    """读取某个事实 key 当前激活的版本记录。"""
    return (
        await db.execute(
            _CURRENT_FACT_VERSION_SQL,
            {"uid": user_id, "key": fact_key},
        )
    ).mappings().first()


async def insert_fact_version(
    db: AsyncSession,
    *,
    user_id: int,
    fact_key: str,
    fact_value: str,
    version: int,
) -> int | None:
    """插入新版本事实并返回新记录 ID。"""
    await db.execute(
        _INSERT_VERSIONED_FACT_SQL,
        {
            "uid": user_id,
            "key": fact_key,
            "val": fact_value,
            "ver": version,
        },
    )
    return (await db.execute(_LAST_INSERT_ID_SQL)).scalar()


async def replace_existing_fact(
    db: AsyncSession,
    *,
    old_id: int,
    old_version: int,
    user_id: int,
    fact_key: str,
    fact_value: str,
) -> None:
    """失活旧事实，插入新版本，并回填 superseded_by。"""
    await db.execute(_DEACTIVATE_FACT_SQL, {"id": old_id})
    new_id = await insert_fact_version(
        db,
        user_id=user_id,
        fact_key=fact_key,
        fact_value=fact_value,
        version=old_version + 1,
    )
    await db.execute(
        _LINK_SUPERSEDED_FACT_SQL,
        {"new_id": new_id, "old_id": old_id},
    )


async def insert_new_fact(
    db: AsyncSession,
    user_id: int,
    fact_key: str,
    fact_value: str,
) -> None:
    """插入首个版本的事实记录。"""
    await db.execute(
        _INSERT_FACT_SQL,
        {"uid": user_id, "key": fact_key, "val": fact_value},
    )


async def upsert_fact_in_db(
    db: AsyncSession,
    *,
    user_id: int,
    fact_key: str,
    fact_value: str,
) -> bool:
    """在单个事务内完成事实更新，返回本次是否真的改动了数据。"""
    row = await fetch_current_fact_version(db, user_id, fact_key)
    if row:
        if row["fact_value"] == fact_value:
            return False

        await replace_existing_fact(
            db,
            old_id=row["id"],
            old_version=row["version"],
            user_id=user_id,
            fact_key=fact_key,
            fact_value=fact_value,
        )
        return True

    await insert_new_fact(db, user_id, fact_key, fact_value)
    return True


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
    columns, assignments, params = build_profile_update_parts(
        preferred_brand=preferred_brand,
        budget_range=budget_range,
        preferred_category=preferred_category,
        tags=tags,
        user_id=user_id,
    )
    if not assignments:
        return False

    await db.execute(
        text(build_profile_upsert_sql(columns, assignments)),
        params,
    )
    return True


__all__ = [
    "fetch_current_fact_version",
    "insert_fact_version",
    "insert_new_fact",
    "load_active_fact_rows",
    "load_profile_row",
    "replace_existing_fact",
    "upsert_fact_in_db",
    "upsert_profile_fields_in_db",
]
