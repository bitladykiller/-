import asyncio

from app.services import user_profile_store as profile_store


class FakeSessionFactory:
    def __init__(self, session) -> None:
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


def test_query_profile_from_db_merges_profile_row_and_fact_rows(monkeypatch) -> None:
    fake_session = object()
    monkeypatch.setattr(profile_store, "AsyncSessionLocal", FakeSessionFactory(fake_session))

    async def fake_load_profile_row(db, user_id):
        assert db is fake_session
        assert user_id == 7
        return {
            "preferred_brand": "海尔",
            "budget_range": "3000以内",
            "preferred_category": None,
            "tags": '["家电"]',
        }

    async def fake_load_active_fact_rows(db, user_id):
        assert db is fake_session
        assert user_id == 7
        return [{"fact_key": "city", "fact_value": "杭州"}]

    monkeypatch.setattr(profile_store, "load_profile_row", fake_load_profile_row)
    monkeypatch.setattr(profile_store, "load_active_fact_rows", fake_load_active_fact_rows)

    result = _run(profile_store.query_profile_from_db(7))

    assert result == {
        "user_id": 7,
        "preferred_brand": "海尔",
        "budget_range": "3000以内",
        "preferred_category": None,
        "tags": ["家电"],
        "facts": [{"key": "city", "value": "杭州"}],
    }


def test_upsert_profile_data_in_db_orchestrates_profile_fields_and_facts(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    fake_session = object()

    async def fake_upsert_profile_fields_in_db(db, **kwargs):
        assert db is fake_session
        calls.append(("profile_fields", kwargs))
        return True

    async def fake_upsert_fact_in_db(db, **kwargs):
        assert db is fake_session
        calls.append(("fact", kwargs))
        return kwargs["fact_key"] == "city"

    monkeypatch.setattr(profile_store, "upsert_profile_fields_in_db", fake_upsert_profile_fields_in_db)
    monkeypatch.setattr(profile_store, "upsert_fact_in_db", fake_upsert_fact_in_db)

    changed = _run(
        profile_store.upsert_profile_data_in_db(
            fake_session,
            user_id=5,
            profile={
                "preferred_brand": "海尔",
                "facts": [
                    {"key": "city", "value": "杭州"},
                    {"key": "budget", "value": "3000以内"},
                ],
            },
        )
    )

    assert changed is True
    assert calls == [
        (
            "profile_fields",
            {
                "user_id": 5,
                "preferred_brand": "海尔",
                "budget_range": None,
                "preferred_category": None,
                "tags": None,
            },
        ),
        (
            "fact",
            {"user_id": 5, "fact_key": "city", "fact_value": "杭州"},
        ),
        (
            "fact",
            {"user_id": 5, "fact_key": "budget", "fact_value": "3000以内"},
        ),
    ]


def test_upsert_profile_data_in_db_returns_false_when_nothing_changed(monkeypatch) -> None:
    fake_session = object()

    async def fake_upsert_profile_fields_in_db(db, **kwargs):
        return False

    async def fake_upsert_fact_in_db(db, **kwargs):
        return False

    monkeypatch.setattr(profile_store, "upsert_profile_fields_in_db", fake_upsert_profile_fields_in_db)
    monkeypatch.setattr(profile_store, "upsert_fact_in_db", fake_upsert_fact_in_db)

    changed = _run(
        profile_store.upsert_profile_data_in_db(
            fake_session,
            user_id=6,
            profile={"preferred_brand": "", "facts": [{"key": "city", "value": "杭州"}]},
        )
    )

    assert changed is False
