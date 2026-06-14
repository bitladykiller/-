from app.knowledge.infrastructure.profile.profile_payload_support import (
    coerce_user_profile_payload,
)
import app.user.application.user_profile_store as profile_store


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


def test_decode_profile_tags_json_filters_invalid_values() -> None:
    assert profile_store.decode_profile_tags_json('["空调", "空调", "", "高端"]') == ["空调", "高端"]
    assert profile_store.decode_profile_tags_json("not-json") == []


def test_build_user_profile_facts_filters_invalid_rows() -> None:
    facts = profile_store.build_user_profile_facts(
        [
            {"fact_key": "workspace", "fact_value": "阿里"},
            {"fact_key": "workspace", "fact_value": ""},
            {"fact_key": None, "fact_value": "ignored"},
        ]
    )

    assert facts == [{"key": "workspace", "value": "阿里"}]


def test_build_profile_field_values_keeps_explicit_empty_tags() -> None:
    field_values = profile_store.build_profile_field_values(
        preferred_brand="  小米 ",
        budget_range=None,
        preferred_category="",
        tags=[],
    )

    assert field_values == {
        "preferred_brand": "小米",
        "tags": "[]",
    }
