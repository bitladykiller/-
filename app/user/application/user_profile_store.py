"""用户画像数据访问层（已废弃）。

**已迁移至**: `app.user.infrastructure.repository.user_profile_repository`

此模块仅作为兼容层保留，所有函数已委托给 `UserProfileRepository` 实例方法。
新代码请直接使用 `user_profile_repository`。
"""

from __future__ import annotations

import warnings

from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.domain.schemas import UserProfileData, UserProfilePayload
from app.user.infrastructure.repository.user_profile_repository import (
    user_profile_repository,
)

warnings.warn(
    "app.user.application.user_profile_store 已废弃，请使用 app.user.infrastructure.repository.user_profile_repository",
    DeprecationWarning,
    stacklevel=2,
)


def empty_user_profile(user_id: int) -> UserProfilePayload:
    """返回默认空画像结构（兼容层，已废弃）。"""
    return user_profile_repository.empty_profile(user_id)


async def query_profile_from_db(user_id: int) -> UserProfilePayload:
    """从 MySQL 读取用户画像和激活中的 facts（兼容层，已废弃）。"""
    from app.shared.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        return await user_profile_repository.get_profile(db, user_id)


async def upsert_profile_fields_in_db(
    db: AsyncSession,
    *,
    user_id: int,
    preferred_brand: str | None,
    budget_range: str | None,
    preferred_category: str | None,
    tags: list[str] | None,
) -> bool:
    """根据传入画像字段执行 upsert（兼容层，已废弃）。"""
    return await user_profile_repository.upsert_profile_fields(
        db,
        user_id=user_id,
        preferred_brand=preferred_brand,
        budget_range=budget_range,
        preferred_category=preferred_category,
        tags=tags,
    )


async def upsert_fact_in_db(
    db: AsyncSession,
    *,
    user_id: int,
    fact_key: str,
    fact_value: str,
) -> bool:
    """在单个事务内完成事实更新（兼容层，已废弃）。"""
    return await user_profile_repository.upsert_fact(
        db,
        user_id=user_id,
        fact_key=fact_key,
        fact_value=fact_value,
    )


async def upsert_profile_data_in_db(
    db: AsyncSession,
    *,
    user_id: int,
    profile: UserProfileData,
) -> bool:
    """在单个事务里批量回写画像字段和结构化 facts（兼容层，已废弃）。"""
    return await user_profile_repository.upsert_profile_data(
        db,
        user_id=user_id,
        profile=profile,
    )


def decode_profile_tags_json(raw_value: str | None) -> list[str]:
    """把数据库中的 tags JSON 安全解码为标签列表（兼容层，已废弃）。"""
    from app.user.infrastructure.repository.user_profile_repository import _decode_profile_tags_json

    return _decode_profile_tags_json(raw_value)


def build_user_profile_facts(rows) -> list:
    """把数据库 facts 行转换为统一的数组（兼容层，已废弃）。"""
    from app.user.infrastructure.repository.user_profile_repository import _build_user_profile_facts

    return _build_user_profile_facts(rows)


def build_profile_field_values(
    *,
    preferred_brand: str | None,
    budget_range: str | None,
    preferred_category: str | None,
    tags: list[str] | None,
) -> dict[str, str]:
    """构造可直接写库的规范化字段值映射（兼容层，已废弃）。"""
    from app.user.infrastructure.repository.user_profile_repository import _build_profile_field_values

    return _build_profile_field_values(
        preferred_brand=preferred_brand,
        budget_range=budget_range,
        preferred_category=preferred_category,
        tags=tags,
    )


__all__ = [
    "build_profile_field_values",
    "build_user_profile_facts",
    "decode_profile_tags_json",
    "empty_user_profile",
    "query_profile_from_db",
    "upsert_fact_in_db",
    "upsert_profile_fields_in_db",
    "upsert_profile_data_in_db",
]
