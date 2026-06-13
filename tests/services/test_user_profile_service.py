import asyncio
import json

from app.services import user_profile_service as profile_service
from app.services.user_profile_service import UserProfileService
from app.services.user_profile_service_support import build_profile_cache_key


class FakeProfileCache:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.setex_calls: list[tuple[str, int, str]] = []
        self.deleted_keys: list[str] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = value
        self.setex_calls.append((key, ttl, value))

    async def delete(self, key: str) -> None:
        self.deleted_keys.append(key)
        self.values.pop(key, None)


def _run(awaitable):
    return asyncio.run(awaitable)


def test_get_profile_uses_cached_profile(monkeypatch) -> None:
    cache = FakeProfileCache()
    expected = {
        "user_id": 7,
        "preferred_brand": "小米",
        "budget_range": None,
        "preferred_category": "空调",
        "tags": ["家电"],
        "facts": [{"key": "city", "value": "杭州"}],
    }
    cache.values[build_profile_cache_key(7)] = json.dumps(
        expected,
        ensure_ascii=False,
    )

    async def unexpected_query(user_id: int):
        raise AssertionError(f"unexpected db query for {user_id}")

    monkeypatch.setattr(profile_service, "query_profile_from_db", unexpected_query)

    result = _run(UserProfileService.get_profile(7, redis_client=cache))

    assert result == expected
    assert cache.setex_calls == []


def test_get_profile_queries_db_and_backfills_cache(monkeypatch) -> None:
    cache = FakeProfileCache()
    expected = {
        "user_id": 9,
        "preferred_brand": "海尔",
        "budget_range": "0-3000",
        "preferred_category": None,
        "tags": ["家电"],
        "facts": [{"key": "city", "value": "杭州"}],
    }

    async def fake_query(user_id: int):
        assert user_id == 9
        return expected

    monkeypatch.setattr(profile_service, "query_profile_from_db", fake_query)

    result = _run(UserProfileService.get_profile(9, redis_client=cache))

    assert result == expected
    assert len(cache.setex_calls) == 1
    key, ttl, value = cache.setex_calls[0]
    assert key == build_profile_cache_key(9)
    assert ttl == UserProfileService.CACHE_TTL
    assert json.loads(value) == expected


def test_get_profile_returns_empty_profile_when_query_fails(monkeypatch) -> None:
    async def failing_query(user_id: int):
        raise RuntimeError(f"boom-{user_id}")

    monkeypatch.setattr(profile_service, "query_profile_from_db", failing_query)

    result = _run(UserProfileService.get_profile(11))

    assert result == {
        "user_id": 11,
        "preferred_brand": None,
        "budget_range": None,
        "preferred_category": None,
        "tags": [],
        "facts": [],
    }


def test_upsert_profile_data_short_circuits_on_empty_payload(monkeypatch) -> None:
    async def unexpected_write(**kwargs):
        raise AssertionError(f"unexpected write call: {kwargs}")

    monkeypatch.setattr(profile_service, "run_write_operation", unexpected_write)

    result = _run(UserProfileService.upsert_profile_data(3, {}))

    assert result is True


def test_upsert_profile_data_delegates_to_write_operation(monkeypatch) -> None:
    captured: dict[str, object] = {}
    cache = FakeProfileCache()
    profile = {"preferred_brand": "海尔"}

    async def fake_run_write_operation(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(profile_service, "run_write_operation", fake_run_write_operation)

    result = _run(
        UserProfileService.upsert_profile_data(5, profile, redis_client=cache)
    )

    assert result is True
    assert captured["session_factory"] is profile_service.AsyncSessionLocal
    assert captured["cache_user_id"] == 5
    assert captured["redis_client"] is cache
    assert captured["operation"] is profile_service.upsert_profile_data_in_db
    assert captured["cache_prefix"] == UserProfileService.CACHE_PREFIX
    assert captured["user_id"] == 5
    assert captured["profile"] == profile
