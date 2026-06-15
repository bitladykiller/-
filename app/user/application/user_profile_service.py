"""用户画像服务。

负责：
- 用户画像读取入口
- Redis 缓存命中、回填和失效
- 用户画像写操作的事务提交与缓存失效编排

设计约束：
- MySQL 读写细节下沉到 `user_profile_store.py`
- 服务层只保留对外 API 和缓存编排
"""

import json
from typing import Any

from app.knowledge.domain.schemas import UserProfileData
from app.knowledge.infrastructure.profile.profile_payload_support import (
    coerce_user_profile_data,
)
from app.shared.core.database import AsyncSessionLocal
from app.user.application.user_profile_store import (
    query_profile_from_db,
    upsert_profile_data_in_db,
)


async def get_profile(
    user_id: int,
    redis_client: Any | None = None,
) -> UserProfileData:
    """获取用户画像，优先读 Redis，未命中再查 MySQL。"""
    cache_key = f"user:profile:{user_id}"
    if redis_client is not None:
        cached = await redis_client.get(cache_key)
        if cached:
            try:
                return coerce_user_profile_data(json.loads(cached))
            except (TypeError, ValueError):
                pass

    try:
        profile = await query_profile_from_db(user_id)
        if redis_client is not None:
            await redis_client.setex(
                cache_key,
                1800,
                json.dumps(profile, ensure_ascii=False),
            )
        return profile
    except Exception:
        return coerce_user_profile_data({})


async def upsert_profile_data(
    user_id: int,
    profile: UserProfileData,
    redis_client: Any | None = None,
) -> bool:
    """批量回写结构化画像，统一处理主字段和 facts。"""
    if not profile:
        return True

    data_changed = False
    try:
        async with AsyncSessionLocal() as db:
            data_changed = await upsert_profile_data_in_db(
                db,
                user_id=user_id,
                profile=profile,
            )
            if data_changed:
                await db.commit()
    except Exception:
        return False

    if data_changed and redis_client is not None:
        await redis_client.delete(f"user:profile:{user_id}")
    return True
