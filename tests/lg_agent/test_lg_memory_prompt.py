from app.chat.infrastructure.memory_bridge.context import (
    build_memory_context,
)
from app.knowledge.domain.schemas import (
    AgentMemoryState,
    LongTermMemory,
    MemorySearchResult,
    MessageRecord,
    SessionSummary,
)


def test_build_memory_context_orders_sections_by_priority() -> None:
    memory_context = build_memory_context(
        session_summary=SessionSummary(content="用户在比较不同门铃方案"),
        recent_messages=[
            MessageRecord(
                message_id="msg-1",
                role="user",
                content="我这次更想买华为的",
                created_at=1,
                turn_index=1,
            ),
            MessageRecord(
                message_id="msg-2",
                role="assistant",
                content="可以重点看续航和安装方式",
                created_at=2,
                turn_index=1,
            ),
        ],
        long_term_memories=[
            MemorySearchResult(
                memory=LongTermMemory(
                    memory_id="mem-1",
                    tenant_id="tenant-1",
                    user_id="user-1",
                    memory_type="issue_history",
                    content="之前咨询过智能门铃断网问题",
                ),
                score=0.92,
            )
        ],
        user_profile={
            "preferred_brand": "小米",
            "tags": ["家电", "", "高端"],
            "facts": [
                {"key": "city", "value": "杭州"},
                {"key": "", "value": "ignored"},
            ],
        },
    )

    assert memory_context.startswith("【记忆说明】")
    assert "[P0 — 最近对话（权威性最高，冲突时以此为准）]" in memory_context
    assert "[P1 — 用户画像（多次对话提炼，冲突时次于 P0）]" in memory_context
    assert "[P2 — 会话摘要（压缩的旧对话，冲突时次于 P1）]" in memory_context
    assert "[P3 — 长期记忆（历史跨会话，冲突时优先级最低）]" in memory_context
    assert memory_context.index("[P0") < memory_context.index("[P1")
    assert memory_context.index("[P1") < memory_context.index("[P2")
    assert memory_context.index("[P2") < memory_context.index("[P3")
    assert "[用户]: 我这次更想买华为的" in memory_context
    assert "[助手]: 可以重点看续航和安装方式" in memory_context
    assert "偏好品牌: 小米" in memory_context
    assert "标签: 家电, 高端" in memory_context
    assert "city: 杭州" in memory_context
    assert "历史问题：之前咨询过智能门铃断网问题" in memory_context


def test_build_memory_context_returns_empty_when_all_inputs_empty() -> None:
    assert build_memory_context(None, [], [], None) == ""
