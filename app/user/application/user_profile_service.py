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

import json
from typing import Any, Protocol

from app.shared.core.database import AsyncSessionLocal
from app.knowledge.domain.schemas import UserProfileData, UserProfilePayload
from app.knowledge.infrastructure.profile.profile_payload_support import (
    coerce_user_profile_payload,
)
from app.user.application.user_profile_store import (
    empty_user_profile,
    query_profile_from_db,
    upsert_profile_data_in_db,
)
from app.platform.config.app_config import app_config

# 缓存 TTL 从统一配置读取
_PROFILE_CACHE_TTL = app_config.memory.user_profile_cache_ttl
_PROFILE_CACHE_PREFIX = "user:profile"


class ProfileCache(Protocol):
    """用户画像缓存客户端的最小接口。"""

    async def get(self, key: str) -> str | bytes | None: ...

    async def setex(self, key: str, ttl: int, value: str) -> Any: ...

    async def delete(self, key: str) -> Any: ...


class UserProfileService:
    """用户画像 CRUD + Redis 缓存。"""

    CACHE_TTL = _PROFILE_CACHE_TTL
    CACHE_PREFIX = _PROFILE_CACHE_PREFIX

    @staticmethod
    async def get_profile(
        user_id: int,
        redis_client: ProfileCache | None = None,
    ) -> UserProfilePayload:
        """获取用户画像，优先读 Redis，未命中再查 MySQL。"""
        cache_key = f"{UserProfileService.CACHE_PREFIX}:{user_id}"
        if redis_client is not None:
            cached = await redis_client.get(cache_key)
            if cached:
                try:
                    return coerce_user_profile_payload(user_id, json.loads(cached))
                except (TypeError, ValueError):
                    pass

        try:
            profile = await query_profile_from_db(user_id)
            if redis_client is not None:
                await redis_client.setex(
                    cache_key,
                    UserProfileService.CACHE_TTL,
                    json.dumps(profile, ensure_ascii=False),
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
            await redis_client.delete(f"{UserProfileService.CACHE_PREFIX}:{user_id}")
        return True
