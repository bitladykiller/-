"""用户画像适配器。

将 user 域的 UserProfileService 适配为 knowledge 域所需的
ProfileReader / ProfileWriter 接口。
"""

from __future__ import annotations

from typing import Any

from app.user.domain.schemas import UserProfileData


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
    """通过用户画像服务回写结构化画像（兼容旧接口，不传来源信息）。"""
    from app.user.application.user_profile_service import user_profile_service

    return await user_profile_service.upsert_profile_data(
        user_id=user_id,
        profile=profile,
        redis_client=redis_client,
    )


async def save_user_profile_with_source(
    user_id: int,
    profile: UserProfileData,
    redis_client: Any | None = None,
    source_turn_id: str | None = None,
) -> bool:
    """通过用户画像服务回写结构化画像，带来源 turn_id。

    v3.36+: source_turn_id 写入 user_facts.source_turn_id，配合
    唯一索引 uk_user_fact_source 实现事件级幂等——同一 turn 产生的
    同 key 事实只会写入一次，LLM 非确定性抽取不会导致重复版本。
    """
    from app.user.application.user_profile_service import user_profile_service

    return await user_profile_service.upsert_profile_data(
        user_id=user_id,
        profile=profile,
        redis_client=redis_client,
        source_turn_id=source_turn_id,
    )


__all__ = ["load_user_profile", "save_user_profile", "save_user_profile_with_source"]
