import app.chat.infrastructure.modeling.prompts as prompts


def test_prompt_constants_expose_required_templates() -> None:
    assert "路由分类器" in prompts.ROUTER_SYSTEM_PROMPT
    assert "{logic}" in prompts.GENERAL_QUERY_SYSTEM_PROMPT
    assert "neo4j_query" in prompts.REACT_SYSTEM_PROMPT
