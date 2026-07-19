from app.knowledge.domain.schemas import LongTermMemory
from app.knowledge.infrastructure.ltm.simple_long_term_memory import (
    build_hit_update_plan,
    build_new_memory_insert_record,
    preview_text,
    resolve_active_search_request,
)


def test_preview_text_and_search_param_resolution_are_stable() -> None:
    search_config = {"top_k": 5, "score_threshold": 0.72}

    assert preview_text("abcdef", 3) == "abc"
    assert resolve_active_search_request(
        search_config,
        "tenant-1",
        "user-1",
        None,
        None,
    ) == (
        'tenant_id == "tenant-1" and user_id == "user-1" and is_deleted == false',
        5,
        0.72,
    )
    assert resolve_active_search_request(
        search_config,
        "tenant-1",
        "user-1",
        2,
        0.88,
    ) == (
        'tenant_id == "tenant-1" and user_id == "user-1" and is_deleted == false',
        2,
        0.88,
    )


def test_build_new_memory_insert_record_returns_full_payload() -> None:
    memory_id, record = build_new_memory_insert_record(
        tenant_id="tenant-1",
        user_id="user-1",
        memory_type="solution_note",
        content="建议重启网关",
        embedding=[0.1, 0.2],
        now_ts=123,
        memory_id="mem-1",
    )

    assert memory_id == "mem-1"
    assert record == {
        "memory_id": "mem-1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "session_id": "",
        "memory_type": "solution_note",
        "content": "建议重启网关",
        "embedding": [0.1, 0.2],
        "created_at": 123,
        "updated_at": 123,
        "last_hit_at": 0,
        "hit_count": 0,
        "is_deleted": False,
    }


def test_build_hit_update_plan_follows_strategy() -> None:
    memory = LongTermMemory(
        memory_id="mem-1",
        tenant_id="tenant-1",
        user_id="user-1",
        memory_type="issue_history",
        content="门铃掉线",
        hit_count=2,
        last_hit_at=100,
    )
    update_config = {
        "enabled": True,
        "update_last_hit_at": True,
        "increase_hit_count": True,
    }

    update_plan = build_hit_update_plan(memory, update_config, now_ts=200)

    assert update_plan == {
        "hit_count": 3,
        "last_hit_at": 200,
        "update_record": {
            "memory_id": "mem-1",
            "updated_at": 200,
            "hit_count": 3,
            "last_hit_at": 200,
        },
    }
