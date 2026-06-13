from app.memory.config import (
    compiled_sensitive_patterns,
    long_term_collection_name,
    long_term_deduplication_config,
    long_term_memory_type_values,
    long_term_search_config,
    long_term_update_on_hit_config,
    short_term_compression_config,
    short_term_config,
    short_term_redis_config,
    short_term_window_config,
)


def test_short_term_config_helpers_return_expected_values() -> None:
    assert short_term_config()["time_window_seconds"] == 86400
    assert short_term_redis_config() == {
        "key_prefix": "agent:stm",
        "ttl_seconds": 86400,
        "lock_ttl_seconds": 10,
    }
    assert short_term_window_config() == {"max_messages": 16}
    assert short_term_compression_config() == {
        "enabled": True,
        "trigger_rounds": 6,
        "trigger_messages": 20,
        "keep_recent_rounds": 4,
    }


def test_long_term_config_helpers_return_expected_values() -> None:
    assert long_term_collection_name() == "customer_agent_long_memory"
    assert long_term_search_config() == {
        "top_k": 5,
        "score_threshold": 0.72,
    }
    assert long_term_deduplication_config() == {
        "top_k": 3,
        "similarity_threshold": 0.88,
    }
    assert long_term_update_on_hit_config() == {
        "enabled": True,
        "update_last_hit_at": True,
        "increase_hit_count": True,
    }


def test_long_term_memory_type_values_returns_stable_frozenset() -> None:
    values = long_term_memory_type_values()

    assert values == frozenset({"issue_history", "solution_note"})
    assert values is long_term_memory_type_values()


def test_compiled_sensitive_patterns_is_cached_and_matchable() -> None:
    patterns = compiled_sensitive_patterns()

    assert patterns is compiled_sensitive_patterns()
    assert any(pattern.search("我的password忘了") for pattern in patterns)
