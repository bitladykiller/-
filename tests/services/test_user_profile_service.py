import asyncio
import json

import app.user.application.user_profile_service as profile_service


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
            async def __aenter__(self):
                return session

            async def __aexit__(self, _exc_type, exc, _tb) -> bool:
                return False

        return _SessionContext()


def _run(awaitable):
    return asyncio.run(awaitable)


def test_get_profile_uses_cached_profile(monkeypatch) -> None:
    cache = FakeProfileCache()
    expected = {
        "preferred_brand": "小米",
        "budget_range": None,
        "preferred_category": "空调",
        "tags": ["家电"],
        "facts": [{"key": "city", "value": "杭州"}],
    }
    cache.values["user:profile:7"] = json.dumps(
        expected,
        ensure_ascii=False,
    )

    async def unexpected_query(user_id: int):
        raise AssertionError(f"unexpected db query for {user_id}")

    monkeypatch.setattr(profile_service, "query_profile_from_db", unexpected_query)

    result = _run(profile_service.get_profile(7, redis_client=cache))

    assert result == expected
    assert cache.setex_calls == []


def test_get_profile_ignores_invalid_cached_json_and_queries_db(monkeypatch) -> None:
    cache = FakeProfileCache()
    cache.values["user:profile:3"] = "{not-json"
    expected = {
        "preferred_brand": "海尔",
        "budget_range": "0-3000",
        "preferred_category": None,
        "tags": ["家电"],
        "facts": [{"key": "city", "value": "杭州"}],
    }

    async def fake_query(user_id: int):
        assert user_id == 3
        return expected

    monkeypatch.setattr(profile_service, "query_profile_from_db", fake_query)

    result = _run(profile_service.get_profile(3, redis_client=cache))

    assert result == expected
    assert cache.setex_calls == [
        (
            "user:profile:3",
            1800,
            json.dumps(expected, ensure_ascii=False),
        )
    ]


def test_get_profile_queries_db_and_backfills_cache(monkeypatch) -> None:
    cache = FakeProfileCache()
    expected = {
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

    result = _run(profile_service.get_profile(9, redis_client=cache))

    assert result == expected
    assert len(cache.setex_calls) == 1
    key, ttl, value = cache.setex_calls[0]
    assert key == "user:profile:9"
    assert ttl == 1800
    assert json.loads(value) == expected


def test_get_profile_returns_empty_profile_when_query_fails(monkeypatch) -> None:
    async def failing_query(user_id: int):
        raise RuntimeError(f"boom-{user_id}")

    monkeypatch.setattr(profile_service, "query_profile_from_db", failing_query)

    result = _run(profile_service.get_profile(11))

    assert result == {
        "preferred_brand": None,
        "budget_range": None,
        "preferred_category": None,
        "tags": [],
        "facts": [],
    }


def test_upsert_profile_data_short_circuits_on_empty_payload(monkeypatch) -> None:
    async def unexpected_write(*args, **kwargs):
        raise AssertionError(f"unexpected write call: {args}, {kwargs}")

    monkeypatch.setattr(profile_service, "upsert_profile_data_in_db", unexpected_write)

    result = _run(profile_service.upsert_profile_data(3, {}))

    assert result is True


def test_upsert_profile_data_commits_and_invalidates_cache_on_change(monkeypatch) -> None:
    session = FakeSession()
    cache = FakeProfileCache()
    profile = {"preferred_brand": "海尔"}

    async def fake_upsert_profile_data_in_db(db, **kwargs):
        assert db is session
        assert kwargs == {"user_id": 5, "profile": profile}
        return True

    monkeypatch.setattr(profile_service, "AsyncSessionLocal", FakeSessionFactory(session))
    monkeypatch.setattr(
        profile_service,
        "upsert_profile_data_in_db",
        fake_upsert_profile_data_in_db,
    )

    result = _run(
        profile_service.upsert_profile_data(5, profile, redis_client=cache)
    )

    assert result is True
    assert session.committed is True
    assert cache.deleted_keys == ["user:profile:5"]


def test_upsert_profile_data_skips_commit_and_invalidation_when_store_reports_no_change(
    monkeypatch,
) -> None:
    session = FakeSession()
    cache = FakeProfileCache()

    async def fake_upsert_profile_data_in_db(db, **kwargs):
        assert db is session
        return False

    monkeypatch.setattr(profile_service, "AsyncSessionLocal", FakeSessionFactory(session))
    monkeypatch.setattr(
        profile_service,
        "upsert_profile_data_in_db",
        fake_upsert_profile_data_in_db,
    )

    result = _run(
        profile_service.upsert_profile_data(
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

    async def failing_upsert_profile_data_in_db(db, **kwargs):
        assert db is session
        raise RuntimeError("boom")

    monkeypatch.setattr(profile_service, "AsyncSessionLocal", FakeSessionFactory(session))
    monkeypatch.setattr(
        profile_service,
        "upsert_profile_data_in_db",
        failing_upsert_profile_data_in_db,
    )

    result = _run(
        profile_service.upsert_profile_data(
            5,
            {"preferred_brand": "海尔"},
            redis_client=cache,
        )
    )

    assert result is False
    assert session.committed is False
    assert cache.deleted_keys == []
