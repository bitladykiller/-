from app.knowledge.infrastructure.profile.profile_payload_support import (
    PROFILE_FIELD_NAMES,
    normalize_profile_data,
    normalize_optional_text,
    normalize_profile_facts,
    normalize_profile_tags,
)
import app.user.application.user_profile_store as profile_store


def test_profile_normalization_support_filters_text_tags_and_facts() -> None:
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


def test_profile_normalization_support_builds_payload_and_profile_data() -> None:
    normalized = normalize_profile_data(
        {
            "preferred_brand": " 小米 ",
            "preferred_category": "",
            "tags": ["智能家居", ""],
            "facts": [{"key": "city", "value": "上海"}],
        }
    )

    assert normalized == {
        "preferred_brand": "小米",
        "tags": ["智能家居"],
        "facts": [{"key": "city", "value": "上海"}],
    }


def test_profile_storage_support_handles_db_rows_and_write_fields() -> None:
    assert profile_store.decode_profile_tags_json('["高端", "", "高端"]') == ["高端"]
    assert profile_store.decode_profile_tags_json(None) == []
    assert profile_store.build_user_profile_facts(
        [
            {"fact_key": "workspace", "fact_value": "阿里"},
            {"fact_key": None, "fact_value": "ignored"},
        ]
    ) == [{"key": "workspace", "value": "阿里"}]
    assert profile_store.build_profile_field_values(
        preferred_brand="  华为 ",
        budget_range=" 3000-5000 ",
        preferred_category=None,
        tags=["门铃", "门铃", ""],
    ) == {
        "preferred_brand": "华为",
        "budget_range": "3000-5000",
        "tags": '["门铃"]',
    }
