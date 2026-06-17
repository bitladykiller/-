"""用户画像领域模型。

职责：
- 定义 durable 用户画像的 TypedDict 结构
- 作为 user 领域对外共享的类型契约

边界：
- 不负责画像持久化
- 不负责记忆上下文组装
"""
from __future__ import annotations

from typing_extensions import TypedDict


class UserProfileFact(TypedDict):
    """用户画像中的单条结构化事实。"""

    key: str
    value: str


class UserProfileData(TypedDict, total=False):
    """记忆上下文里使用的标准化用户画像结构。"""

    preferred_brand: str | None
    budget_range: str | None
    preferred_category: str | None
    tags: list[str]
    facts: list[UserProfileFact]


class UserProfilePayload(UserProfileData):
    """用户画像服务对外返回的完整结构，额外包含 user_id。"""

    user_id: int
