from app.memory.memory_config_defaults import (
    LONG_TERM_MEMORY_CONFIG,
    LONG_TERM_MEMORY_TYPES,
    SENSITIVE_PATTERNS,
    SHORT_TERM_MEMORY_CONFIG,
)


def test_short_term_memory_defaults_expose_expected_shape() -> None:
    assert SHORT_TERM_MEMORY_CONFIG["redis"]["key_prefix"] == "agent:stm"
    assert SHORT_TERM_MEMORY_CONFIG["compression"]["keep_recent_rounds"] == 4


def test_long_term_memory_defaults_and_sensitive_patterns_are_stable() -> None:
    assert LONG_TERM_MEMORY_CONFIG["collection_name"] == "customer_agent_long_memory"
    assert LONG_TERM_MEMORY_TYPES == {
        "ISSUE_HISTORY": "issue_history",
        "SOLUTION_NOTE": "solution_note",
    }
    assert any("password" in pattern for pattern in SENSITIVE_PATTERNS)
