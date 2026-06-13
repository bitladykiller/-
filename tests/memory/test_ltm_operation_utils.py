from app.memory.ltm_operation_utils import (
    build_cluster_merge_plan,
    build_hit_update_plan,
    build_new_memory_insert_record,
    build_soft_delete_record,
    preview_text,
    resolve_active_search_request,
    resolve_search_params,
)
from app.memory.schemas import LongTermMemory


def test_preview_text_and_search_param_resolution_are_stable() -> None:
    search_config = {"top_k": 5, "score_threshold": 0.72}

    assert preview_text("abcdef", 3) == "abc"
    assert resolve_search_params(search_config, None, None) == (5, 0.72)
    assert resolve_search_params(search_config, 2, 0.88) == (2, 0.88)
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
        "memory_type": "solution_note",
        "content": "建议重启网关",
        "embedding": [0.1, 0.2],
        "created_at": 123,
        "updated_at": 123,
        "last_hit_at": 0,
        "hit_count": 0,
        "is_deleted": False,
    }


def test_build_hit_update_plan_and_soft_delete_record_follow_strategy() -> None:
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
    assert build_soft_delete_record("mem-1", 300) == {
        "memory_id": "mem-1",
        "updated_at": 300,
        "is_deleted": True,
    }


def test_build_cluster_merge_plan_returns_main_record_and_delete_list() -> None:
    merge_plan = build_cluster_merge_plan(
        [
            {
                "memory_id": "mem-1",
                "content": "第一次描述",
                "updated_at": 10,
                "hit_count": 1,
                "last_hit_at": 50,
                "embedding": [1.0, 0.0],
            },
            {
                "memory_id": "mem-2",
                "content": "第二次描述",
                "updated_at": 20,
                "hit_count": 3,
                "last_hit_at": 80,
                "embedding": [0.99, 0.01],
            },
        ],
        now_ts=99,
    )

    assert merge_plan["merged_content"] == "第一次描述；第二次描述"
    assert merge_plan["deleted_memory_ids"] == ["mem-1"]
    assert merge_plan["merged_record"]["memory_id"] == "mem-2"
    assert merge_plan["merged_record"]["updated_at"] == 99
    assert merge_plan["merged_record"]["hit_count"] == 4
    assert merge_plan["merged_record"]["last_hit_at"] == 80
