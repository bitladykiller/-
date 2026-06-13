from app.lg_agent.lg_prompts import (
    _DEFAULT_PROMPTS,
    _get_prompt,
)


def test_get_prompt_reads_from_loaded_prompt_mapping(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.lg_agent.lg_prompts._prompt_mapping",
        {"router_system": "custom router"},
    )

    assert _get_prompt("router_system") == "custom router"


def test_default_prompts_expose_required_keys() -> None:
    assert "router_system" in _DEFAULT_PROMPTS
    assert "general_query" in _DEFAULT_PROMPTS
    assert "react_system" in _DEFAULT_PROMPTS
