from app.knowledge.domain.prompt_builder import (
    build_compression_prompt,
    build_memory_injection_prompt,
    build_summary_injection_prompt,
)
from app.knowledge.domain.schemas import LongTermMemory, MemorySearchResult, MessageRecord, SessionSummary


def _build_memory_search_result(
    *,
    memory_type: str,
    content: str,
    memory_id: str = "mem-1",
) -> MemorySearchResult:
    return MemorySearchResult(
        memory=LongTermMemory(
            memory_id=memory_id,
            tenant_id="tenant-1",
            user_id="user-1",
            memory_type=memory_type,
            content=content,
        ),
        score=0.9,
    )


def test_build_memory_injection_prompt_formats_memory_lines() -> None:
    prompt = build_memory_injection_prompt(
        [
            _build_memory_search_result(memory_type="issue_history", content="用户曾咨询过空调"),
            _build_memory_search_result(
                memory_type="solution_note",
                content="推荐先确认房间面积",
                memory_id="mem-2",
            ),
        ]
    )

    assert "【长期记忆参考】" in prompt
    assert "1. 历史问题：用户曾咨询过空调" in prompt
    assert "2. 有效方案：推荐先确认房间面积" in prompt


def test_build_summary_injection_prompt_returns_empty_for_missing_content() -> None:
    assert build_summary_injection_prompt(None) == ""
    assert build_summary_injection_prompt(SessionSummary()) == ""


def test_build_compression_prompt_includes_summary_messages_and_round() -> None:
    prompt = build_compression_prompt(
        old_summary="用户之前在比较洗衣机品牌",
        old_messages=[
            MessageRecord(
                message_id="msg-1",
                role="user",
                content="我想看看海尔洗衣机",
                created_at=1,
                turn_index=1,
            ),
            MessageRecord(
                message_id="msg-2",
                role="assistant",
                content="可以先看容量和能效等级",
                created_at=2,
                turn_index=1,
            ),
        ],
        compressed_round=5,
    )

    assert "你是对话摘要助手" in prompt
    assert "用户之前在比较洗衣机品牌" in prompt
    assert "[user]: 我想看看海尔洗衣机" in prompt
    assert "[assistant]: 可以先看容量和能效等级" in prompt
    assert '"compressed_round": 5' in prompt
