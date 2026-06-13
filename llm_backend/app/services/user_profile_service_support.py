"""`user_profile_service.py` 共享的服务编排 helper。

职责：
- 承接用户画像缓存 key、缓存读写和失效逻辑
- 承接写操作的事务提交与缓存失效样板

边界：
- 不提供最终的服务入口方法
- 不承载 MySQL 具体查询 / 写入语义
- 不暴露 HTTP 或路由相关逻辑
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeAlias

from app.memory.profile_utils import coerce_user_profile_payload
from app.memory.schemas import UserProfilePayload

PROFILE_CACHE_TTL = 1800  # 30 分钟
PROFILE_CACHE_PREFIX = "user:profile"

DBWriteOperation: TypeAlias = Callable[..., Awaitable[bool]]
SessionFactory: TypeAlias = Callable[[], Any]


class ProfileCache(Protocol):
    """用户画像缓存客户端的最小接口。"""

    async def get(self, key: str) -> str | bytes | None: ...

    async def setex(self, key: str, ttl: int, value: str) -> Any: ...

    async def delete(self, key: str) -> Any: ...


def build_profile_cache_key(
    user_id: int,
    *,
    cache_prefix: str = PROFILE_CACHE_PREFIX,
) -> str:
    """构造用户画像缓存 key。"""
    return f"{cache_prefix}:{user_id}"


async def invalidate_profile_cache(
    redis_client: ProfileCache | None,
    user_id: int,
    *,
    cache_prefix: str = PROFILE_CACHE_PREFIX,
) -> None:
    """清理用户画像缓存。无缓存客户端时直接跳过。"""
    if redis_client:
        await redis_client.delete(
            build_profile_cache_key(user_id, cache_prefix=cache_prefix)
        )


async def load_cached_profile(
    redis_client: ProfileCache | None,
    user_id: int,
    *,
    cache_prefix: str = PROFILE_CACHE_PREFIX,
) -> UserProfilePayload | None:
    """从 Redis 读取缓存画像。未命中或无缓存客户端时返回 None。"""
    if not redis_client:
        return None

    cached = await redis_client.get(
        build_profile_cache_key(user_id, cache_prefix=cache_prefix)
    )
    if not cached:
        return None

    try:
        return coerce_user_profile_payload(user_id, json.loads(cached))
    except (TypeError, ValueError):
        return None


async def cache_profile(
    redis_client: ProfileCache | None,
    profile: UserProfilePayload,
    *,
    cache_prefix: str = PROFILE_CACHE_PREFIX,
    cache_ttl: int = PROFILE_CACHE_TTL,
) -> None:
    """把画像结果写入 Redis 缓存。"""
    if not redis_client:
        return

    await redis_client.setex(
        build_profile_cache_key(profile["user_id"], cache_prefix=cache_prefix),
        cache_ttl,
        json.dumps(profile, ensure_ascii=False),
    )


async def run_write_operation(
    *,
    session_factory: SessionFactory,
    cache_user_id: int,
    redis_client: ProfileCache | None,
    operation: DBWriteOperation,
    cache_prefix: str = PROFILE_CACHE_PREFIX,
    **operation_kwargs: Any,
) -> bool:
    """统一处理服务层写操作的事务提交和缓存失效。

    返回值语义：
    - `True`：本次写流程成功结束，包括“正常写入”和“无需变更的 no-op”
    - `False`：数据库执行阶段失败
    """
    data_changed = False
    try:
        async with session_factory() as db:
            data_changed = await operation(db, **operation_kwargs)
            if data_changed:
                await db.commit()
    except Exception:
        return False

    if data_changed:
        await invalidate_profile_cache(
            redis_client,
            cache_user_id,
            cache_prefix=cache_prefix,
        )
    return True


__all__ = [
    "DBWriteOperation",
    "PROFILE_CACHE_PREFIX",
    "PROFILE_CACHE_TTL",
    "ProfileCache",
    "build_profile_cache_key",
    "cache_profile",
    "invalidate_profile_cache",
    "load_cached_profile",
    "run_write_operation",
]
