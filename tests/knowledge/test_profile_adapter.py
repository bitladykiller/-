"""画像适配器单测：租户参数贯穿。"""

from __future__ import annotations

import asyncio

from app.knowledge.infrastructure.orchestration.profile_adapter import (
    load_user_profile,
    save_user_profile,
    save_user_profile_with_source,
)


class FakeProfileService:
    def __init__(self) -> None:
        self.read_calls: list[tuple] = []
        self.write_calls: list[tuple] = []
        self.profile = {"user_id": 7, "preferred_brand": "小米"}

    async def get_profile(self, tenant_id, user_id, redis_client=None):
        self.read_calls.append((tenant_id, user_id, redis_client))
        return self.profile

    async def upsert_profile_data(
        self,
        tenant_id,
        user_id,
        profile,
        redis_client=None,
        source_turn_id=None,
    ):
        self.write_calls.append((tenant_id, user_id, profile, redis_client, source_turn_id))
        return True


def _run(awaitable):
    return asyncio.run(awaitable)


def test_load_user_profile_passes_tenant(monkeypatch) -> None:

    service = FakeProfileService()
    monkeypatch.setattr(
        "app.user.application.user_profile_service.user_profile_service",
        service,
    )

    profile = _run(load_user_profile("t_1", 7, redis_client="redis"))

    assert profile == {"user_id": 7, "preferred_brand": "小米"}
    assert service.read_calls == [("t_1", 7, "redis")]


def test_save_user_profile_passes_tenant(monkeypatch) -> None:

    service = FakeProfileService()
    monkeypatch.setattr(
        "app.user.application.user_profile_service.user_profile_service",
        service,
    )

    ok = _run(save_user_profile("t_1", 7, {"preferred_brand": "海尔"}, redis_client="r"))

    assert ok is True
    assert service.write_calls == [("t_1", 7, {"preferred_brand": "海尔"}, "r", None)]


def test_save_user_profile_with_source_passes_turn_id(monkeypatch) -> None:

    service = FakeProfileService()
    monkeypatch.setattr(
        "app.user.application.user_profile_service.user_profile_service",
        service,
    )

    ok = _run(
        save_user_profile_with_source(
            "t_1",
            7,
            {"preferred_brand": "海尔"},
            redis_client="r",
            source_turn_id="turn-9",
        )
    )

    assert ok is True
    assert service.write_calls == [
        ("t_1", 7, {"preferred_brand": "海尔"}, "r", "turn-9")
    ]
