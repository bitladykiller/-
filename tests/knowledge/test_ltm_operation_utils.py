"""LTM 纯函数单测。

注意：这里一律使用真实的配置 dataclass，不用等价的 dict 替身。
历史上这些测试传 dict，掩盖了生产代码把 frozen dataclass 当 dict 下标取值的
运行期 TypeError（见 test_ltm_config_objects_are_attribute_accessed）。
"""

from app.knowledge.domain.schemas import LongTermMemory
from app.knowledge.infrastructure.ltm.simple_long_term_memory import (
    build_hit_update_plan,
    build_new_memory_insert_record,
    preview_text,
    resolve_active_search_request,
)
from app.shared.core.app_config import (
    LTMDeduplicationConfig,
    LTMSearchConfig,
    LTMUpdateOnHitConfig,
)


def test_preview_text_and_search_param_resolution_are_stable() -> None:
    search_config = LTMSearchConfig(top_k=5, score_threshold=0.72)

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
    update_config = LTMUpdateOnHitConfig(
        enabled=True,
        update_last_hit_at=True,
        increase_hit_count=True,
    )

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


def test_ltm_config_objects_are_attribute_accessed_not_subscripted() -> None:
    """回归测试：LTM 配置是 frozen dataclass，不支持下标。

    生产代码一旦写回 `config["top_k"]`，会抛 TypeError 并被上层宽泛的
    except 吞掉，表现为"长期记忆静默失效"——没有报错，只是永远检索不到、
    也永远写不进去。这里直接钉死访问方式。
    """
    for config, key in (
        (LTMSearchConfig(), "top_k"),
        (LTMDeduplicationConfig(), "top_k"),
        (LTMUpdateOnHitConfig(), "enabled"),
    ):
        assert hasattr(config, key)
        try:
            config[key]  # type: ignore[index]
        except TypeError:
            pass
        else:  # pragma: no cover - 配置类型变了就该重新审视调用方
            raise AssertionError(f"{type(config).__name__} 变成可下标了，请复核调用方写法")
