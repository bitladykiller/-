from app.shared.core.json_utils import (
    extract_first_json_object,
    parse_first_json_object,
)


def test_extract_first_json_object_ignores_prefix_suffix_and_inner_braces() -> None:
    payload = extract_first_json_object(
        '前置说明 {"semantic":[{"content":"a { brace } inside string"}],"profile":{}} 后置说明'
    )

    assert payload == '{"semantic":[{"content":"a { brace } inside string"}],"profile":{}}'


def test_extract_first_json_object_returns_none_without_complete_json() -> None:
    assert extract_first_json_object("没有 JSON") is None
    assert extract_first_json_object('前置 {"semantic": [') is None


def test_parse_first_json_object_accepts_only_dict_payload() -> None:
    assert parse_first_json_object('前缀 {"foo":"bar"} 后缀') == {"foo": "bar"}
    assert parse_first_json_object('["not-a-dict"]') is None
    assert parse_first_json_object('前置 {"semantic": [}') is None
