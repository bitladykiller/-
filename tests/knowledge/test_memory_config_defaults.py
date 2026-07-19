"""记忆配置默认值测试 — 验证合并到 settings.app_config 后的值不变。"""

from app.shared.core.config import settings


def test_short_term_memory_defaults_expose_expected_shape() -> None:
    stm = settings.app_config.memory.stm
    assert stm.redis.key_prefix == "agent:stm"
    assert stm.compression.keep_recent_rounds == 4


def test_long_term_memory_defaults_and_sensitive_patterns_are_stable() -> None:
    ltm = settings.app_config.memory.ltm
    assert ltm.collection_name == "customer_agent_long_memory"
    assert settings.app_config.memory.ltm_memory_types == {
        "ISSUE_HISTORY": "issue_history",
        "SOLUTION_NOTE": "solution_note",
    }
    assert any("password" in p for p in settings.app_config.memory.sensitive_patterns)
