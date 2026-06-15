import asyncio

import app.user.application.user_profile_store as profile_store


class FakeResult:
    def __init__(
        self,
        *,
        first=None,
        all_rows=None,
        scalar_value=None,
    ) -> None:
        self._first = first
        self._all_rows = all_rows or []
        self._scalar_value = scalar_value

    def mappings(self):
        return self

    def first(self):
        return self._first

    def all(self):
        return self._all_rows

    def scalar(self):
        return self._scalar_value


class FakeSession:
    def __init__(self, results) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    async def execute(self, statement, params=None):
        sql_text = str(getattr(statement, "text", statement))
        self.calls.append((sql_text, params))
        return self._results.pop(0)


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


def test_empty_user_profile_returns_stable_default_payload() -> None:
    assert profile_store.empty_user_profile(8) == {
        "user_id": 8,
        "preferred_brand": None,
        "budget_range": None,
        "preferred_category": None,
        "tags": [],
        "facts": [],
    }


def test_query_profile_from_db_reads_rows_and_normalizes_payload(monkeypatch) -> None:
    session = FakeSession(
        [
            FakeResult(
                first={
                    "preferred_brand": " 海尔 ",
                    "budget_range": "3000以内",
                    "preferred_category": None,
                    "tags": '["家电", "", "家电"]',
                }
            ),
            FakeResult(
                all_rows=[
                    {"fact_key": "city", "fact_value": "杭州"},
                    {"fact_key": "", "fact_value": "ignored"},
                ]
            ),
        ]
    )
    monkeypatch.setattr(profile_store, "AsyncSessionLocal", FakeSessionFactory(session))

    result = _run(profile_store.query_profile_from_db(7))

    assert result == {
        "user_id": 7,
        "preferred_brand": "海尔",
        "budget_range": "3000以内",
        "preferred_category": None,
        "tags": ["家电"],
        "facts": [{"key": "city", "value": "杭州"}],
    }
    assert len(session.calls) == 2
    assert "FROM user_profiles WHERE user_id = :uid" in session.calls[0][0]
    assert "FROM user_facts" in session.calls[1][0]


def test_query_profile_from_db_falls_back_to_empty_tags_when_json_invalid(monkeypatch) -> None:
    session = FakeSession(
        [
            FakeResult(
                first={
                    "preferred_brand": " 海尔 ",
                    "budget_range": None,
                    "preferred_category": None,
                    "tags": "not-json",
                }
            ),
            FakeResult(all_rows=[]),
        ]
    )
    monkeypatch.setattr(profile_store, "AsyncSessionLocal", FakeSessionFactory(session))

    result = _run(profile_store.query_profile_from_db(7))

    assert result == {
        "user_id": 7,
        "preferred_brand": "海尔",
        "budget_range": None,
        "preferred_category": None,
        "tags": [],
        "facts": [],
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

    monkeypatch.setattr(
        profile_store,
        "upsert_profile_fields_in_db",
        fake_upsert_profile_fields_in_db,
    )
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


def test_upsert_profile_data_in_db_skips_empty_fields_and_invalid_facts(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    fake_session = object()

    async def fake_upsert_profile_fields_in_db(db, **kwargs):
        calls.append(("profile_fields", kwargs))
        return False

    async def fake_upsert_fact_in_db(db, **kwargs):
        calls.append(("fact", kwargs))
        return False

    monkeypatch.setattr(
        profile_store,
        "upsert_profile_fields_in_db",
        fake_upsert_profile_fields_in_db,
    )
    monkeypatch.setattr(profile_store, "upsert_fact_in_db", fake_upsert_fact_in_db)

    changed = _run(
        profile_store.upsert_profile_data_in_db(
            fake_session,
            user_id=6,
            profile={
                "preferred_brand": "",
                "facts": [
                    {"key": "city", "value": "杭州"},
                    {"key": "", "value": "ignored"},
                    {"key": "budget", "value": None},
                ],
            },
        )
    )

    assert changed is False
    assert calls == [
        (
            "fact",
            {"user_id": 6, "fact_key": "city", "fact_value": "杭州"},
        )
    ]


def test_upsert_fact_in_db_returns_false_when_value_did_not_change() -> None:
    session = FakeSession(
        [
            FakeResult(first={"id": 3, "fact_value": "杭州", "version": 2}),
        ]
    )

    changed = _run(
        profile_store.upsert_fact_in_db(
            session,
            user_id=8,
            fact_key="city",
            fact_value="杭州",
        )
    )

    assert changed is False
    assert len(session.calls) == 1


def test_upsert_fact_in_db_replaces_existing_fact_when_value_changes() -> None:
    session = FakeSession(
        [
            FakeResult(first={"id": 5, "fact_value": "杭州", "version": 2}),
            FakeResult(),
            FakeResult(),
            FakeResult(scalar_value=19),
            FakeResult(),
        ]
    )

    changed = _run(
        profile_store.upsert_fact_in_db(
            session,
            user_id=9,
            fact_key="city",
            fact_value="宁波",
        )
    )

    assert changed is True
    assert len(session.calls) == 5
    assert "UPDATE user_facts SET is_active = FALSE" in session.calls[1][0]
    assert "INSERT INTO user_facts (user_id, fact_key, fact_value, version)" in session.calls[2][0]
    assert "SELECT LAST_INSERT_ID()" in session.calls[3][0]
    assert session.calls[4][1] == {"new_id": 19, "old_id": 5}


def test_upsert_fact_in_db_inserts_first_version_when_missing() -> None:
    session = FakeSession(
        [
            FakeResult(first=None),
            FakeResult(),
        ]
    )

    changed = _run(
        profile_store.upsert_fact_in_db(
            session,
            user_id=6,
            fact_key="budget",
            fact_value="3000以内",
        )
    )

    assert changed is True
    assert len(session.calls) == 2
    assert "INSERT INTO user_facts (user_id, fact_key, fact_value)" in session.calls[1][0]


def test_upsert_profile_fields_in_db_returns_false_for_empty_fields() -> None:
    session = FakeSession([])

    changed = _run(
        profile_store.upsert_profile_fields_in_db(
            session,
            user_id=4,
            preferred_brand="",
            budget_range=None,
            preferred_category=None,
            tags=None,
        )
    )

    assert changed is False
    assert session.calls == []


def test_upsert_profile_fields_in_db_executes_generated_upsert_sql() -> None:
    session = FakeSession([FakeResult()])

    changed = _run(
        profile_store.upsert_profile_fields_in_db(
            session,
            user_id=4,
            preferred_brand="海尔",
            budget_range=None,
            preferred_category="冰箱",
            tags=["家电"],
        )
    )

    assert changed is True
    assert len(session.calls) == 1
    sql_text, params = session.calls[0]
    assert "INSERT INTO user_profiles" in sql_text
    assert params == {
        "uid": 4,
        "preferred_brand": "海尔",
        "preferred_category": "冰箱",
        "tags": '["家电"]',
    }


def test_upsert_profile_fields_in_db_keeps_explicit_empty_tags() -> None:
    session = FakeSession([FakeResult()])

    changed = _run(
        profile_store.upsert_profile_fields_in_db(
            session,
            user_id=4,
            preferred_brand="  小米 ",
            budget_range=None,
            preferred_category="",
            tags=[],
        )
    )

    assert changed is True
    assert len(session.calls) == 1
    _sql_text, params = session.calls[0]
    assert params == {
        "uid": 4,
        "preferred_brand": "小米",
        "tags": "[]",
    }
