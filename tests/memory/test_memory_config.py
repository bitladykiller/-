from app.knowledge.infrastructure.config import (
    COMPILED_SENSITIVE_PATTERNS,
    LONG_TERM_MEMORY_CONFIG,
    LONG_TERM_MEMORY_TYPE_VALUES,
    SHORT_TERM_MEMORY_CONFIG,
)


def test_short_term_config_exposes_expected_nested_values() -> None:
    config = SHORT_TERM_MEMORY_CONFIG

    assert config["time_window_seconds"] == 86400
    assert config["redis"] == {
        "key_prefix": "agent:stm",
        "ttl_seconds": 86400,
        "lock_ttl_seconds": 10,
    }
    assert config["window"] == {"max_messages": 16}
    assert config["compression"] == {
        "trigger_rounds": 6,
        "trigger_messages": 20,
        "keep_recent_rounds": 4,
    }


def test_long_term_config_exposes_expected_nested_values() -> None:
    config = LONG_TERM_MEMORY_CONFIG

    assert config["collection_name"] == "customer_agent_long_memory"
    assert config["search"] == {
        "top_k": 5,
        "score_threshold": 0.72,
    }
    assert config["deduplication"] == {
        "top_k": 3,
        "similarity_threshold": 0.88,
    }


def test_long_term_memory_type_values_exposes_stable_frozenset() -> None:
    assert LONG_TERM_MEMORY_TYPE_VALUES == frozenset({"issue_history", "solution_note"})
    assert LONG_TERM_MEMORY_TYPE_VALUES is LONG_TERM_MEMORY_TYPE_VALUES


def test_compiled_sensitive_patterns_are_matchable() -> None:
    assert COMPILED_SENSITIVE_PATTERNS is COMPILED_SENSITIVE_PATTERNS
    assert any(
        pattern.search("我的password忘了")
        for pattern in COMPILED_SENSITIVE_PATTERNS
    )
