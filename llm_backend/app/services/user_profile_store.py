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

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.memory.profile_utils import (
    build_user_profile_facts,
    has_profile_fields,
    iter_profile_facts,
)
from app.memory.schemas import UserProfileData, UserProfilePayload
from app.services.user_profile_store_support import (
    load_active_fact_rows,
    load_profile_row,
    upsert_fact_in_db,
    upsert_profile_fields_in_db,
)
from app.services.user_profile_store_utils import (
    empty_user_profile,
    merge_profile_row,
)


async def query_profile_from_db(user_id: int) -> UserProfilePayload:
    """从 MySQL 读取用户画像和激活中的 facts。"""
    profile = empty_user_profile(user_id)

    async with AsyncSessionLocal() as db:
        merge_profile_row(profile, await load_profile_row(db, user_id))
        profile["facts"] = build_user_profile_facts(
            await load_active_fact_rows(db, user_id)
        )

    return profile

async def upsert_profile_data_in_db(
    db: AsyncSession,
    *,
    user_id: int,
    profile: UserProfileData,
) -> bool:
    """在单个事务里批量回写画像字段和结构化 facts。"""
    data_changed = False

    if has_profile_fields(profile):
        profile_changed = await upsert_profile_fields_in_db(
            db,
            user_id=user_id,
            preferred_brand=profile.get("preferred_brand"),
            budget_range=profile.get("budget_range"),
            preferred_category=profile.get("preferred_category"),
            tags=profile.get("tags"),
        )
        data_changed = data_changed or profile_changed

    for fact_key, fact_value in iter_profile_facts(profile):
        fact_changed = await upsert_fact_in_db(
            db,
            user_id=user_id,
            fact_key=fact_key,
            fact_value=fact_value,
        )
        data_changed = data_changed or fact_changed

    return data_changed


__all__ = [
    "empty_user_profile",
    "query_profile_from_db",
    "upsert_profile_data_in_db",
]
