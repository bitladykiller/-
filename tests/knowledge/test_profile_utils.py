"""用户画像工具函数测试。

测试 UserProfileRepository 内部的辅助函数。
"""

from app.user.domain.profile_payload_support import (
    coerce_user_profile_payload,
    normalize_optional_text,
    normalize_profile_data,
    normalize_profile_facts,
    normalize_profile_tags,
    PROFILE_FIELD_NAMES,
)
from app.user.infrastructure.repository.user_profile_repository import (
    _decode_profile_tags_json,
    _build_user_profile_facts,
    _build_profile_field_values,
)


def test_coerce_user_profile_payload_returns_stable_defaults() -> None:
    payload = coerce_user_profile_payload(
        42,
        {
            "preferred_brand": "  海尔 ",
            "tags": ["家电", "家电", "", "  冰箱  "],
            "facts": [
                {"key": "city", "value": " 杭州 "},
                {"key": "", "value": "ignored"},
            ],
        },
    )

    assert payload == {
        "user_id": 42,
        "preferred_brand": "海尔",
        "budget_range": None,
        "preferred_category": None,
        "tags": ["家电", "冰箱"],
        "facts": [{"key": "city", "value": "杭州"}],
    }


def test_profile_normalization_filters_text_tags_and_facts() -> None:
    assert PROFILE_FIELD_NAMES == (
        "preferred_brand",
        "budget_range",
        "preferred_category",
    )
    assert normalize_optional_text("  海尔 ") == "海尔"
    assert normalize_optional_text("") is None
    assert normalize_optional_text(1) is None
    assert normalize_profile_tags(["家电", "家电", "", " 冰箱 ", None]) == [
        "家电",
        "冰箱",
    ]
    assert normalize_profile_facts(
        [
            {"key": " city ", "value": " 杭州 "},
            {"key": "", "value": "ignored"},
            {"key": "budget", "value": None},
        ]
    ) == [{"key": "city", "value": "杭州"}]
    assert normalize_profile_data(
        {
            "preferred_brand": " 小米 ",
            "preferred_category": "",
            "tags": ["智能家居", ""],
            "facts": [{"key": "city", "value": "上海"}],
        }
    ) == {
        "preferred_brand": "小米",
        "tags": ["智能家居"],
        "facts": [{"key": "city", "value": "上海"}],
    }


def test_decode_profile_tags_json_filters_invalid_values() -> None:
    assert _decode_profile_tags_json('["空调", "空调", "", "高端"]') == ["空调", "高端"]
    assert _decode_profile_tags_json("not-json") == []


def test_build_user_profile_facts_filters_invalid_rows() -> None:
    facts = _build_user_profile_facts(
        [
            {"fact_key": "workspace", "fact_value": "阿里"},
            {"fact_key": "workspace", "fact_value": ""},
            {"fact_key": None, "fact_value": "ignored"},
        ]
    )

    assert facts == [{"key": "workspace", "value": "阿里"}]


def test_build_profile_field_values_keeps_explicit_empty_tags() -> None:
    field_values = _build_profile_field_values(
        preferred_brand="  小米 ",
        budget_range=None,
        preferred_category="",
        tags=[],
    )

    assert field_values == {
        "preferred_brand": "小米",
        "tags": "[]",
    }


def test_profile_store_helpers_handle_db_rows_and_write_fields() -> None:
    assert _decode_profile_tags_json('["高端", "", "高端"]') == ["高端"]
    assert _decode_profile_tags_json(None) == []
    assert _build_user_profile_facts(
        [
            {"fact_key": "workspace", "fact_value": "阿里"},
            {"fact_key": None, "fact_value": "ignored"},
        ]
    ) == [{"key": "workspace", "value": "阿里"}]
    assert _build_profile_field_values(
        preferred_brand="  华为 ",
        budget_range=" 3000-5000 ",
        preferred_category=None,
        tags=["门铃", "门铃", ""],
    ) == {
        "preferred_brand": "华为",
        "budget_range": "3000-5000",
        "tags": '["门铃"]',
    }