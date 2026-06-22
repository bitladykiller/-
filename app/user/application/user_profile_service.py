"""用户画像服务。

负责：
- 用户画像读取入口
- Redis 缓存命中、回填和失效
- 用户画像写操作的事务提交与缓存失效编排

设计约束：
- MySQL 读写细节下沉到 `user_profile_repository.py`
- 服务层只保留对外 API 和缓存编排
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from app.shared.core.database import AsyncSessionLocal
from app.shared.core.logger import get_logger
from app.knowledge.domain.schemas import UserProfileData, UserProfilePayload
from app.knowledge.infrastructure.profile.profile_payload_support import (
    coerce_user_profile_payload,
)
from app.user.infrastructure.repository.user_profile_repository import (
    user_profile_repository,
)
from app.shared.core.config import settings

logger = get_logger(__name__)

_PROFILE_CACHE_TTL = settings.app_config.memory.user_profile_cache_ttl
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

    def __init__(self, repository: Any = None):
        """初始化服务，可注入自定义 Repository（用于测试）。"""
        self._repository = repository or user_profile_repository

    async def get_profile(
        self,
        user_id: int,
        redis_client: ProfileCache | None = None,
    ) -> UserProfilePayload:
        """获取用户画像，优先读 Redis，未命中再查 MySQL。"""
        cache_key = f"{self.CACHE_PREFIX}:{user_id}"
        if redis_client is not None:
            cached = await redis_client.get(cache_key)
            if cached:
                try:
                    return coerce_user_profile_payload(user_id, json.loads(cached))
                except (TypeError, ValueError):
                    pass

        try:
            async with AsyncSessionLocal() as db:
                profile = await self._repository.get_profile(db, user_id)
            if redis_client is not None:
                await redis_client.setex(
                    cache_key,
                    self.CACHE_TTL,
                    json.dumps(profile, ensure_ascii=False),
                )
            return profile
        except Exception:
            logger.warning(
                "[user_profile] 读取画像失败 | user_id=%s",
                user_id,
                exc_info=True,
            )
            return self._repository.empty_profile(user_id)

    async def upsert_profile_data(
        self,
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
                data_changed = await self._repository.upsert_profile_data(
                    db,
                    user_id=user_id,
                    profile=profile,
                )
                if data_changed:
                    await db.commit()
        except Exception:
            logger.warning(
                "[user_profile] 写入画像失败 | user_id=%s",
                user_id,
                exc_info=True,
            )
            return False

        if data_changed and redis_client is not None:
            await redis_client.delete(f"{self.CACHE_PREFIX}:{user_id}")
        return True


user_profile_service = UserProfileService()

__all__ = ["UserProfileService", "user_profile_service"]