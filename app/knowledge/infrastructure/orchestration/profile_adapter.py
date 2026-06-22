"""用户画像适配器。

将 user 域的 UserProfileService 适配为 knowledge 域所需的
ProfileReader / ProfileWriter 接口，避免 knowledge 域直接依赖 user 域。
"""

from __future__ import annotations

from typing import Any

from app.knowledge.domain.schemas import UserProfileData


async def load_user_profile(
    user_id: int,
    redis_client: Any | None = None,
) -> UserProfileData:
    """通过用户画像服务读取结构化画像。"""
    from app.user.application.user_profile_service import user_profile_service

    return await user_profile_service.get_profile(
        user_id,
        redis_client=redis_client,
    )


async def save_user_profile(
    user_id: int,
    profile: UserProfileData,
    redis_client: Any | None = None,
) -> bool:
    """通过用户画像服务回写结构化画像。"""
    from app.user.application.user_profile_service import user_profile_service

    return await user_profile_service.upsert_profile_data(
        user_id=user_id,
        profile=profile,
        redis_client=redis_client,
    )


__all__ = ["load_user_profile", "save_user_profile"]