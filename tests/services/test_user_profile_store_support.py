import asyncio

from app.services.user_profile_store_support import (
    load_active_fact_rows,
    load_profile_row,
    upsert_fact_in_db,
    upsert_profile_fields_in_db,
)


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


def _run(awaitable):
    return asyncio.run(awaitable)


def test_load_profile_row_and_active_fact_rows_return_mapped_results() -> None:
    session = FakeSession(
        [
            FakeResult(first={"preferred_brand": "海尔"}),
            FakeResult(all_rows=[{"fact_key": "city", "fact_value": "杭州"}]),
        ]
    )

    profile_row = _run(load_profile_row(session, 7))
    fact_rows = _run(load_active_fact_rows(session, 7))

    assert profile_row == {"preferred_brand": "海尔"}
    assert fact_rows == [{"fact_key": "city", "fact_value": "杭州"}]


def test_upsert_fact_in_db_returns_false_when_value_did_not_change() -> None:
    session = FakeSession(
        [
            FakeResult(first={"id": 3, "fact_value": "杭州", "version": 2}),
        ]
    )

    changed = _run(
        upsert_fact_in_db(
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
        upsert_fact_in_db(
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
        upsert_fact_in_db(
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
        upsert_profile_fields_in_db(
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
        upsert_profile_fields_in_db(
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
