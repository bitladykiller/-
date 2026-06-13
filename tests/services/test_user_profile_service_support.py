import asyncio
import json

from app.services.user_profile_service_support import (
    PROFILE_CACHE_PREFIX,
    PROFILE_CACHE_TTL,
    build_profile_cache_key,
    cache_profile,
    load_cached_profile,
    run_write_operation,
)


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
            async def __aenter__(self_inner):
                return session

            async def __aexit__(self_inner, exc_type, exc, tb) -> bool:
                return False

        return _SessionContext()


def _run(awaitable):
    return asyncio.run(awaitable)


def test_build_profile_cache_key_uses_default_prefix() -> None:
    assert build_profile_cache_key(7) == f"{PROFILE_CACHE_PREFIX}:7"


def test_load_cached_profile_returns_none_for_invalid_json() -> None:
    cache = FakeProfileCache()
    cache.values[build_profile_cache_key(3)] = "{not-json"

    result = _run(load_cached_profile(cache, 3))

    assert result is None


def test_cache_profile_uses_default_ttl_and_serializes_payload() -> None:
    cache = FakeProfileCache()
    profile = {
        "user_id": 9,
        "preferred_brand": "海尔",
        "budget_range": "0-3000",
        "preferred_category": None,
        "tags": ["家电"],
        "facts": [{"key": "city", "value": "杭州"}],
    }

    _run(cache_profile(cache, profile))

    assert cache.setex_calls == [
        (
            build_profile_cache_key(9),
            PROFILE_CACHE_TTL,
            json.dumps(profile, ensure_ascii=False),
        )
    ]


def test_run_write_operation_commits_and_invalidates_cache_on_change() -> None:
    session = FakeSession()
    session_factory = FakeSessionFactory(session)
    cache = FakeProfileCache()

    async def fake_operation(db, **kwargs):
        assert db is session
        assert kwargs == {"user_id": 5, "profile": {"preferred_brand": "海尔"}}
        return True

    result = _run(
        run_write_operation(
            session_factory=session_factory,
            cache_user_id=5,
            redis_client=cache,
            operation=fake_operation,
            user_id=5,
            profile={"preferred_brand": "海尔"},
        )
    )

    assert result is True
    assert session.committed is True
    assert cache.deleted_keys == [build_profile_cache_key(5)]


def test_run_write_operation_returns_false_when_operation_fails() -> None:
    session_factory = FakeSessionFactory(FakeSession())
    cache = FakeProfileCache()

    async def failing_operation(db, **kwargs):
        raise RuntimeError("boom")

    result = _run(
        run_write_operation(
            session_factory=session_factory,
            cache_user_id=5,
            redis_client=cache,
            operation=failing_operation,
        )
    )

    assert result is False
    assert cache.deleted_keys == []


def test_run_write_operation_skips_commit_and_invalidation_on_noop() -> None:
    session = FakeSession()
    session_factory = FakeSessionFactory(session)
    cache = FakeProfileCache()

    async def no_op_operation(db, **kwargs):
        return False

    result = _run(
        run_write_operation(
            session_factory=session_factory,
            cache_user_id=8,
            redis_client=cache,
            operation=no_op_operation,
        )
    )

    assert result is True
    assert session.committed is False
    assert cache.deleted_keys == []
