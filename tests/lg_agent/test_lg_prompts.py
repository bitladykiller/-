import app.chat.infrastructure.modeling.prompts as prompts


def test_default_prompts_expose_required_keys() -> None:
    assert "router_system" in prompts.DEFAULT_PROMPTS
    assert "general_query" in prompts.DEFAULT_PROMPTS
    assert "react_system" in prompts.DEFAULT_PROMPTS
