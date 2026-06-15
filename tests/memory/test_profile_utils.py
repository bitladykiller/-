from app.knowledge.infrastructure.profile.profile_payload_support import (
    PROFILE_FIELD_NAMES,
    coerce_user_profile_data,
    normalize_optional_text,
    normalize_profile_data,
    normalize_profile_tags,
)


def test_coerce_user_profile_data_returns_stable_defaults() -> None:
    payload = coerce_user_profile_data(
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
    assert normalize_profile_data(
        {
            "preferred_brand": " 小米 ",
            "preferred_category": "",
            "tags": ["智能家居", ""],
            "facts": [
                {"key": " city ", "value": " 上海 "},
                {"key": "", "value": "ignored"},
                {"key": "budget", "value": None},
            ],
        }
    ) == {
        "preferred_brand": "小米",
        "tags": ["智能家居"],
        "facts": [{"key": "city", "value": "上海"}],
    }
