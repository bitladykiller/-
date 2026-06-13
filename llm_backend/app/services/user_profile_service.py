"""用户画像服务。

负责：
- 用户画像读取入口
- Redis 缓存命中、回填和失效
- 用户画像写操作的事务提交与缓存失效编排

设计约束：
- MySQL 读写细节下沉到 `user_profile_store.py`
- 服务层只保留对外 API 和缓存编排
"""

from __future__ import annotations

from app.core.database import AsyncSessionLocal
from app.memory.schemas import UserProfileData, UserProfilePayload
from app.services.user_profile_service_support import (
    PROFILE_CACHE_PREFIX,
    PROFILE_CACHE_TTL,
    ProfileCache,
    cache_profile,
    load_cached_profile,
    run_write_operation,
)
from app.services.user_profile_store import (
    empty_user_profile,
    query_profile_from_db,
    upsert_profile_data_in_db,
)


class UserProfileService:
    """用户画像 CRUD + Redis 缓存。"""

    CACHE_TTL = PROFILE_CACHE_TTL
    CACHE_PREFIX = PROFILE_CACHE_PREFIX

    @staticmethod
    async def get_profile(
        user_id: int,
        redis_client: ProfileCache | None = None,
    ) -> UserProfilePayload:
        """获取用户画像，优先读 Redis，未命中再查 MySQL。"""
        cached_profile = await load_cached_profile(
            redis_client,
            user_id,
            cache_prefix=UserProfileService.CACHE_PREFIX,
        )
        if cached_profile is not None:
            return cached_profile

        try:
            profile = await query_profile_from_db(user_id)
            await cache_profile(
                redis_client,
                profile,
                cache_prefix=UserProfileService.CACHE_PREFIX,
                cache_ttl=UserProfileService.CACHE_TTL,
            )
            return profile
        except Exception:
            return empty_user_profile(user_id)

    @staticmethod
    async def upsert_profile_data(
        user_id: int,
        profile: UserProfileData,
        redis_client: ProfileCache | None = None,
    ) -> bool:
        """批量回写结构化画像，统一处理主字段和 facts。"""
        if not profile:
            return True

        return await run_write_operation(
            session_factory=AsyncSessionLocal,
            cache_user_id=user_id,
            redis_client=redis_client,
            operation=upsert_profile_data_in_db,
            cache_prefix=UserProfileService.CACHE_PREFIX,
            user_id=user_id,
            profile=profile,
        )
