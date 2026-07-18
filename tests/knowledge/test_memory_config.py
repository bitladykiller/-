"""记忆配置测试 — 验证合并到 settings.app_config 后的值不变。"""

from app.shared.core.config import settings


def test_short_term_config_helpers_return_expected_values() -> None:
    stm = settings.app_config.memory.stm
    assert stm.time_window_seconds == 86400
    assert stm.redis.key_prefix == "agent:stm"
    assert stm.redis.ttl_seconds == 86400
    assert stm.redis.lock_ttl_seconds == 10
    assert stm.window.max_messages == 16
    assert stm.compression.enabled is True
    assert stm.compression.trigger_rounds == 6
    assert stm.compression.trigger_messages == 20
    assert stm.compression.keep_recent_rounds == 4


def test_long_term_config_helpers_return_expected_values() -> None:
    ltm = settings.app_config.memory.ltm
    assert ltm.collection_name == "customer_agent_long_memory"
    assert ltm.search.top_k == 5
    assert ltm.search.score_threshold == 0.72
    assert ltm.deduplication.top_k == 3
    assert ltm.deduplication.similarity_threshold == 0.88
    assert ltm.update_on_hit.enabled is True
    assert ltm.update_on_hit.update_last_hit_at is True
    assert ltm.update_on_hit.increase_hit_count is True


def test_long_term_memory_type_values_returns_stable_frozenset() -> None:
    values = frozenset(settings.app_config.memory.ltm_memory_types.values())
    assert values == frozenset({"issue_history", "solution_note"})


def test_compiled_sensitive_patterns_is_matchable() -> None:
    import re

    patterns = tuple(re.compile(p, re.IGNORECASE) for p in settings.app_config.memory.sensitive_patterns)
    assert any(p.search("我的password忘了") for p in patterns)