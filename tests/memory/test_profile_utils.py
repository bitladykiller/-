from app.memory.profile_utils import (
    build_profile_field_values,
    build_user_profile_facts,
    coerce_user_profile_payload,
    decode_profile_tags_json,
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


def test_decode_profile_tags_json_filters_invalid_values() -> None:
    assert decode_profile_tags_json('["空调", "空调", "", "高端"]') == ["空调", "高端"]
    assert decode_profile_tags_json("not-json") == []


def test_build_user_profile_facts_filters_invalid_rows() -> None:
    facts = build_user_profile_facts(
        [
            {"fact_key": "workspace", "fact_value": "阿里"},
            {"fact_key": "workspace", "fact_value": ""},
            {"fact_key": None, "fact_value": "ignored"},
        ]
    )

    assert facts == [{"key": "workspace", "value": "阿里"}]


def test_build_profile_field_values_keeps_explicit_empty_tags() -> None:
    field_values = build_profile_field_values(
        preferred_brand="  小米 ",
        budget_range=None,
        preferred_category="",
        tags=[],
    )

    assert field_values == {
        "preferred_brand": "小米",
        "tags": "[]",
    }
