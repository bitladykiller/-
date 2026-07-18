from app.knowledge.infrastructure.ltm.simple_long_term_memory import (
    build_active_memory_filter,
    build_memory_record,
    build_partial_update_record,
    has_dedup_match,
    search_results_from_hits,
)


def test_build_active_memory_filter_supports_optional_memory_type() -> None:
    assert build_active_memory_filter("tenant-1", "user-1") == (
        'tenant_id == "tenant-1" and user_id == "user-1" and is_deleted == false'
    )
    assert build_active_memory_filter("tenant-1", "user-1", "issue_history") == (
        'tenant_id == "tenant-1" and user_id == "user-1" and '
        'memory_type == "issue_history" and is_deleted == false'
    )


def test_build_memory_record_and_partial_update_record_return_stable_payloads() -> None:
    memory_record = build_memory_record(
        memory_id="mem-1",
        tenant_id="tenant-1",
        user_id="user-1",
        memory_type="solution_note",
        content="建议检查网络",
        embedding=[0.1, 0.2],
        now_ts=123,
    )
    partial_update_record = build_partial_update_record(
        "mem-1",
        updated_at=456,
        hit_count=3,
        is_deleted=True,
    )

    assert memory_record == {
        "memory_id": "mem-1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "memory_type": "solution_note",
        "content": "建议检查网络",
        "embedding": [0.1, 0.2],
        "created_at": 123,
        "updated_at": 123,
        "last_hit_at": 0,
        "hit_count": 0,
        "is_deleted": False,
    }
    assert partial_update_record == {
        "memory_id": "mem-1",
        "updated_at": 456,
        "hit_count": 3,
        "is_deleted": True,
    }


def test_search_results_from_hits_skips_invalid_entities() -> None:
    results = search_results_from_hits(
        [
            {
                "entity": {
                    "memory_id": "mem-1",
                    "tenant_id": "tenant-1",
                    "user_id": "user-1",
                    "memory_type": "issue_history",
                    "content": "之前问过洗衣机问题",
                },
                "score": 0.93,
            },
            {"entity": None, "score": 0.5},
        ]
    )

    assert len(results) == 1
    assert results[0].memory.memory_id == "mem-1"
    assert results[0].memory.memory_type == "issue_history"
    assert results[0].score == 0.93


def test_has_dedup_match_uses_max_distance_against_threshold() -> None:
    assert has_dedup_match([], 0.9) is False
    assert has_dedup_match([[{"distance": 0.82}, {"distance": 0.95}]], 0.9) is True
    assert has_dedup_match([[{"distance": 0.82}, {"distance": 0.88}]], 0.9) is False
