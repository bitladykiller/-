"""记忆层到画像服务层的桥接模块。

负责：
- 把 memory 层需要的“读画像 / 写画像”能力抽象成稳定函数接口
- 屏蔽 `UserProfileService` 的导入位置和调用细节
- 提供 user_id 规范化 helper，避免中间件重复处理

不负责：
- Redis STM / Milvus LTM 的读写
- 记忆抽取逻辑
- Agent 编排
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias

from app.memory.schemas import UserProfileData

ProfileReader: TypeAlias = Callable[[int, Any | None], Awaitable[UserProfileData]]
ProfileWriter: TypeAlias = Callable[[int, UserProfileData, Any | None], Awaitable[bool]]


def coerce_user_id(user_id: str) -> int:
    """把字符串 user_id 安全转换为 int，失败时返回 0。"""
    return int(user_id) if user_id and user_id.isdigit() else 0


async def load_user_profile(
    user_id: int,
    redis_client: Any | None = None,
) -> UserProfileData:
    """通过用户画像服务读取结构化画像。"""
    from app.services.user_profile_service import UserProfileService

    return await UserProfileService.get_profile(
        user_id,
        redis_client=redis_client,
    )


async def save_user_profile(
    user_id: int,
    profile: UserProfileData,
    redis_client: Any | None = None,
) -> bool:
    """通过用户画像服务回写结构化画像。"""
    from app.services.user_profile_service import UserProfileService

    return await UserProfileService.upsert_profile_data(
        user_id=user_id,
        profile=profile,
        redis_client=redis_client,
    )
