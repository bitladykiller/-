import json

from app.services.user_profile_store_utils import (
    build_profile_update_parts,
    build_profile_upsert_sql,
    empty_user_profile,
    merge_profile_row,
)


def test_empty_user_profile_returns_stable_default_payload() -> None:
    assert empty_user_profile(8) == {
        "user_id": 8,
        "preferred_brand": None,
        "budget_range": None,
        "preferred_category": None,
        "tags": [],
        "facts": [],
    }


def test_build_profile_upsert_sql_uses_passed_columns_and_assignments() -> None:
    sql = build_profile_upsert_sql(
        ["preferred_brand", "tags"],
        ["preferred_brand = :preferred_brand", "tags = :tags"],
    )

    assert "INSERT INTO user_profiles (user_id, preferred_brand, tags)" in sql
    assert "VALUES (:uid, :preferred_brand, :tags)" in sql
    assert "ON DUPLICATE KEY UPDATE preferred_brand = :preferred_brand, tags = :tags" in sql


def test_merge_profile_row_normalizes_text_and_tags() -> None:
    profile = empty_user_profile(5)

    merge_profile_row(
        profile,
        {
            "preferred_brand": " 小米 ",
            "budget_range": "",
            "preferred_category": " 智能门锁 ",
            "tags": json.dumps(["家电", "家电", " ", "智能"], ensure_ascii=False),
        },
    )

    assert profile == {
        "user_id": 5,
        "preferred_brand": "小米",
        "budget_range": None,
        "preferred_category": "智能门锁",
        "tags": ["家电", "智能"],
        "facts": [],
    }


def test_build_profile_update_parts_only_keeps_normalized_fields() -> None:
    columns, assignments, params = build_profile_update_parts(
        preferred_brand=" 海尔 ",
        budget_range="",
        preferred_category=None,
        tags=["厨房", "厨房", " "],
        user_id=11,
    )

    assert columns == ["preferred_brand", "tags"]
    assert assignments == [
        "preferred_brand = :preferred_brand",
        "tags = :tags",
    ]
    assert params == {
        "uid": 11,
        "preferred_brand": "海尔",
        "tags": json.dumps(["厨房"], ensure_ascii=False),
    }
