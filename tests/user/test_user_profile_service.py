"""用户画像服务测试。

测试 UserProfileService 实例方法（非静态方法）。
"""

import asyncio
import json

from app.user.application.user_profile_service import UserProfileService
from app.user.infrastructure.repository.user_profile_repository import UserProfileRepository


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


class FakeSession:
    def __init__(self) -> None:
        self.committed = False

    async def commit(self) -> None:
        self.committed = True


class FakeSessionFactory:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    def __call__(self):
        session = self.session

        class _SessionContext:
            async def __aenter__(self_inner):  # noqa: N805
                return session

            async def __aexit__(self_inner, exc_type, exc, tb) -> bool:  # noqa: N805
                return False

        return _SessionContext()


class FakeRepository:
    """用于测试的假 Repository，记录调用并返回预设值。"""

    def __init__(self) -> None:
        self.get_profile_calls: list[int] = []
        self.upsert_profile_data_calls: list[tuple[int, dict]] = []
        self._profile_result: dict | None = None
        self._upsert_result: bool = True
        self._raise_on_get: Exception | None = None
        self._raise_on_upsert: Exception | None = None

    def set_profile_result(self, profile: dict) -> None:
        self._profile_result = profile

    def set_upsert_result(self, changed: bool) -> None:
        self._upsert_result = changed

    def set_raise_on_get(self, exc: Exception) -> None:
        self._raise_on_get = exc

    def set_raise_on_upsert(self, exc: Exception) -> None:
        self._raise_on_upsert = exc

    def empty_profile(self, user_id: int) -> dict:
        return UserProfileRepository.empty_profile(user_id)

    async def get_profile(self, db, user_id: int) -> dict:
        self.get_profile_calls.append(user_id)
        if self._raise_on_get:
            raise self._raise_on_get
        if self._profile_result is not None:
            return self._profile_result
        return self.empty_profile(user_id)

    async def upsert_profile_data(self, db, *, user_id: int, profile: dict) -> bool:
        self.upsert_profile_data_calls.append((user_id, profile))
        if self._raise_on_upsert:
            raise self._raise_on_upsert
        return self._upsert_result


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
    cache.values["user:profile:7"] = json.dumps(expected, ensure_ascii=False)

    repo = FakeRepository()
    repo.set_raise_on_get(AssertionError("unexpected db query"))

    monkeypatch.setattr(
        "app.user.application.user_profile_service.AsyncSessionLocal",
        FakeSessionFactory(FakeSession()),
    )

    service = UserProfileService(repository=repo)
    result = _run(service.get_profile(7, redis_client=cache))

    assert result == expected
    assert cache.setex_calls == []
    assert repo.get_profile_calls == []


def test_get_profile_ignores_invalid_cached_json_and_queries_db(monkeypatch) -> None:
    cache = FakeProfileCache()
    cache.values["user:profile:3"] = "{not-json"
    expected = {
        "user_id": 3,
        "preferred_brand": "海尔",
        "budget_range": "0-3000",
        "preferred_category": None,
        "tags": ["家电"],
        "facts": [{"key": "city", "value": "杭州"}],
    }

    repo = FakeRepository()
    repo.set_profile_result(expected)

    monkeypatch.setattr(
        "app.user.application.user_profile_service.AsyncSessionLocal",
        FakeSessionFactory(FakeSession()),
    )

    service = UserProfileService(repository=repo)
    result = _run(service.get_profile(3, redis_client=cache))

    assert result == expected
    assert cache.setex_calls == [
        (
            "user:profile:3",
            UserProfileService.CACHE_TTL,
            json.dumps(expected, ensure_ascii=False),
        )
    ]


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

    repo = FakeRepository()
    repo.set_profile_result(expected)

    monkeypatch.setattr(
        "app.user.application.user_profile_service.AsyncSessionLocal",
        FakeSessionFactory(FakeSession()),
    )

    service = UserProfileService(repository=repo)
    result = _run(service.get_profile(9, redis_client=cache))

    assert result == expected
    assert len(cache.setex_calls) == 1
    key, ttl, value = cache.setex_calls[0]
    assert key == "user:profile:9"
    assert ttl == UserProfileService.CACHE_TTL
    assert json.loads(value) == expected


def test_get_profile_returns_empty_profile_when_query_fails(monkeypatch) -> None:
    repo = FakeRepository()
    repo.set_raise_on_get(RuntimeError("boom"))

    monkeypatch.setattr(
        "app.user.application.user_profile_service.AsyncSessionLocal",
        FakeSessionFactory(FakeSession()),
    )

    service = UserProfileService(repository=repo)
    result = _run(service.get_profile(11))

    assert result == {
        "user_id": 11,
        "preferred_brand": None,
        "budget_range": None,
        "preferred_category": None,
        "tags": [],
        "facts": [],
    }


def test_upsert_profile_data_short_circuits_on_empty_payload(monkeypatch) -> None:
    repo = FakeRepository()
    repo.set_raise_on_upsert(AssertionError("unexpected write call"))

    monkeypatch.setattr(
        "app.user.application.user_profile_service.AsyncSessionLocal",
        FakeSessionFactory(FakeSession()),
    )

    service = UserProfileService(repository=repo)
    result = _run(service.upsert_profile_data(3, {}))

    assert result is True
    assert repo.upsert_profile_data_calls == []


def test_upsert_profile_data_commits_and_invalidates_cache_on_change(monkeypatch) -> None:
    session = FakeSession()
    cache = FakeProfileCache()
    profile = {"preferred_brand": "海尔"}

    repo = FakeRepository()
    repo.set_upsert_result(True)

    monkeypatch.setattr(
        "app.user.application.user_profile_service.AsyncSessionLocal",
        FakeSessionFactory(session),
    )

    service = UserProfileService(repository=repo)
    result = _run(service.upsert_profile_data(5, profile, redis_client=cache))

    assert result is True
    assert session.committed is True
    assert cache.deleted_keys == ["user:profile:5"]
    assert repo.upsert_profile_data_calls == [(5, profile)]


def test_upsert_profile_data_skips_commit_and_invalidation_when_store_reports_no_change(
    monkeypatch,
) -> None:
    session = FakeSession()
    cache = FakeProfileCache()

    repo = FakeRepository()
    repo.set_upsert_result(False)

    monkeypatch.setattr(
        "app.user.application.user_profile_service.AsyncSessionLocal",
        FakeSessionFactory(session),
    )

    service = UserProfileService(repository=repo)
    result = _run(
        service.upsert_profile_data(
            8,
            {"preferred_brand": "海尔"},
            redis_client=cache,
        )
    )

    assert result is True
    assert session.committed is False
    assert cache.deleted_keys == []


def test_upsert_profile_data_returns_false_when_write_raises(monkeypatch) -> None:
    session = FakeSession()
    cache = FakeProfileCache()

    repo = FakeRepository()
    repo.set_raise_on_upsert(RuntimeError("boom"))

    monkeypatch.setattr(
        "app.user.application.user_profile_service.AsyncSessionLocal",
        FakeSessionFactory(session),
    )

    service = UserProfileService(repository=repo)
    result = _run(
        service.upsert_profile_data(
            5,
            {"preferred_brand": "海尔"},
            redis_client=cache,
        )
    )

    assert result is False
    assert session.committed is False
    assert cache.deleted_keys == []
