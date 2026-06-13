"""`memory_middleware.py` 共享的阶段 helper。

这个模块负责：
- `before_agent` 阶段的各层记忆读取
- `after_agent` 阶段的短期记忆写入、画像回写和长期记忆落库 helper
- 摘要压缩回调与消息 payload 构造

这个模块不负责：
- 统一降级策略
- 单次告警去重
- 决定何时触发压缩或抽取
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias

from app.memory.profile_gateway import ProfileReader, ProfileWriter, coerce_user_id
from app.memory.prompt_builder import build_compression_prompt
from app.memory.schemas import (
    AgentMemoryState,
    MemoryExtractorResult,
    MemorySearchResult,
    MessageRecord,
    UserProfileData,
)

SummaryCompressor: TypeAlias = Callable[[str, list[MessageRecord]], Awaitable[str]]


def coerce_llm_response_text(response: Any) -> str:
    """把 LLM 返回统一收口成字符串。"""
    content = getattr(response, "content", response)
    return content if isinstance(content, str) else str(content)


def build_turn_messages(
    *,
    user_message: str,
    assistant_message: str,
    created_at: int,
    turn_index: int,
) -> list[MessageRecord]:
    """构造当前轮次的 user / assistant 两条短期消息。"""
    return [
        MessageRecord(
            message_id=f"msg_u_{created_at}",
            role="user",
            content=user_message,
            created_at=created_at,
            turn_index=turn_index,
        ),
        MessageRecord(
            message_id=f"msg_a_{created_at}",
            role="assistant",
            content=assistant_message,
            created_at=created_at,
            turn_index=turn_index,
        ),
    ]


async def load_short_term_memory_state(
    memory_state: AgentMemoryState,
    *,
    redis_stm: Any,
    tenant_id: str,
    user_id: str,
    session_id: str,
) -> None:
    """读取 Redis 中的短期摘要和最近消息。"""
    memory_state.session_summary = await redis_stm.get_summary(
        tenant_id,
        user_id,
        session_id,
    )
    memory_state.recent_messages = await redis_stm.get_recent_messages(
        tenant_id,
        user_id,
        session_id,
    )


async def load_user_profile_state(
    memory_state: AgentMemoryState,
    *,
    profile_reader: ProfileReader,
    profile_cache: Any | None,
    user_id: str,
) -> None:
    """读取 MySQL 用户画像。"""
    uid = coerce_user_id(user_id)
    if uid <= 0:
        return
    memory_state.user_profile = await profile_reader(uid, profile_cache)


async def load_long_term_memory_state(
    memory_state: AgentMemoryState,
    *,
    milvus_ltm: Any,
    ltm_enabled: bool,
    tenant_id: str,
    user_id: str,
    user_input: str,
) -> None:
    """从 Milvus 检索长期语义记忆。"""
    if not ltm_enabled:
        return
    memory_state.long_term_memories = await milvus_ltm.hybrid_search(
        tenant_id,
        user_id,
        user_input,
    )


async def save_short_term_turn(
    *,
    redis_stm: Any,
    tenant_id: str,
    user_id: str,
    session_id: str,
    user_message: str,
    assistant_message: str,
    now_ts: int,
) -> None:
    """把本轮问答写入 Redis 短期记忆。"""
    meta = await redis_stm.get_meta(tenant_id, user_id, session_id)
    meta.total_turns += 1
    meta.last_updated_at = now_ts

    messages = build_turn_messages(
        user_message=user_message,
        assistant_message=assistant_message,
        created_at=now_ts,
        turn_index=meta.total_turns,
    )
    for message in messages:
        await redis_stm.append_message(tenant_id, user_id, session_id, message)

    await redis_stm.save_meta(tenant_id, user_id, session_id, meta)
    await redis_stm.refresh_ttl(tenant_id, user_id, session_id)


def build_summary_compressor(
    *,
    llm_client: Any,
    compressed_round: int,
) -> SummaryCompressor:
    """构造 Redis STM 压缩阶段需要的 LLM 摘要回调。"""

    async def llm_compress_func(
        old_summary_str: str,
        old_messages: list[MessageRecord],
    ) -> str:
        prompt = build_compression_prompt(
            old_summary=old_summary_str,
            old_messages=old_messages,
            compressed_round=compressed_round,
        )
        response = await llm_client.ainvoke(prompt)
        return coerce_llm_response_text(response)

    return llm_compress_func


async def save_semantic_memories(
    *,
    milvus_ltm: Any,
    tenant_id: str,
    user_id: str,
    semantic_memories: list[MemoryExtractorResult],
) -> None:
    """批量持久化抽取出的语义长期记忆候选项。"""
    for memory in semantic_memories:
        should_save_memory = await milvus_ltm.deduplicate_memory(
            tenant_id,
            user_id,
            memory.memory_type,
            memory.content,
        )
        if not should_save_memory:
            continue
        await milvus_ltm.save_memory(
            tenant_id,
            user_id,
            memory.memory_type,
            memory.content,
        )


async def save_profile_if_present(
    *,
    profile_writer: ProfileWriter,
    profile_cache: Any | None,
    user_id: str,
    profile: UserProfileData,
) -> None:
    """仅在画像数据非空且 user_id 有效时触发画像回写。"""
    uid = coerce_user_id(user_id)
    if uid <= 0 or not profile or not isinstance(profile, dict):
        return
    await profile_writer(
        uid,
        profile,
        profile_cache,
    )


async def update_hit_long_term_memories(
    *,
    milvus_ltm: Any,
    long_term_memories: list[MemorySearchResult],
) -> None:
    """尽力刷新命中长期记忆的访问统计。"""
    for result in long_term_memories:
        try:
            await milvus_ltm.update_memory_hit_info(result.memory)
        except Exception:
            continue
