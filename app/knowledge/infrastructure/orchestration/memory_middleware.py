"""记忆中间件。

统一编排：
- `before_agent`：读取短期记忆、用户画像、长期记忆
- `after_agent`：写入短期记忆、触发压缩、抽取长期记忆、刷新命中信息

本文件重点做流程编排，不把 Redis / Milvus / 画像服务的细节分散到多个调用点。
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeAlias

from app.shared.core.logger import get_logger
from app.knowledge.domain.prompt_builder import build_compression_prompt
from app.knowledge.domain.schemas import (
    AgentMemoryState,
    MemoryExtractorResult,
    MemorySearchResult,
    MessageRecord,
    SessionSummary,
    UserProfileData,
)
from app.knowledge.infrastructure.config import LongTermMemoryConfig, long_term_config

if TYPE_CHECKING:
    from app.knowledge.infrastructure.orchestration.memory_extractor import MemoryExtractor
    from app.knowledge.infrastructure.stm.redis_short_term_memory import (
        RedisShortTermMemory,
    )
    from app.knowledge.infrastructure.ltm.simple_long_term_memory import (
        SimpleLongTermMemory,
    )

logger = get_logger(__name__)
_SummaryCompressor = Callable[[str, list[MessageRecord]], Awaitable[str]]
ProfileReader: TypeAlias = Callable[[int, Any | None], Awaitable[UserProfileData]]
ProfileWriter: TypeAlias = Callable[[int, UserProfileData, Any | None], Awaitable[bool]]


def coerce_user_id(user_id: str) -> int:
    """把字符串 user_id 安全转换为 int，失败时返回 0。"""
    return int(user_id) if user_id and user_id.isdigit() else 0


async def load_user_profile(
    user_id: int,
    redis_client: Any | None = None,
) -> UserProfileData:
    """通过用户画像服务读取结构化画像。"""
    from app.user.application.user_profile_service import UserProfileService

    return await UserProfileService.get_profile(
        user_id,
        redis_client=redis_client,
    )


async def save_user_profile(
    user_id: int,
    profile: UserProfileData,
    redis_client: Any | None = None,
) -> bool:
    """通过用户画像服务回写结构化画像。"""
    from app.user.application.user_profile_service import UserProfileService

    return await UserProfileService.upsert_profile_data(
        user_id=user_id,
        profile=profile,
        redis_client=redis_client,
    )


class MemoryMiddleware:
    """记忆系统编排层。"""

    def __init__(
        self,
        redis_stm: RedisShortTermMemory,
        milvus_ltm: SimpleLongTermMemory,
        memory_extractor: MemoryExtractor,
        profile_reader: ProfileReader = load_user_profile,
        profile_writer: ProfileWriter = save_user_profile,
    ):
        self.redis_stm = redis_stm
        self.milvus_ltm = milvus_ltm
        self.memory_extractor = memory_extractor
        self.profile_reader = profile_reader
        self.profile_writer = profile_writer
        self.ltm_config: LongTermMemoryConfig = long_term_config()
        self.ltm_enabled = self.ltm_config["enabled"]
        self._errors_warned: set[str] = set()

    def _warn_once(self, key: str, message: str) -> None:
        """同一类降级警告仅记录一次，避免日志刷屏。"""
        if key in self._errors_warned:
            return
        logger.warning(message)
        self._errors_warned.add(key)

    async def before_agent(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        user_input: str,
    ) -> AgentMemoryState:
        """Agent 执行前：读取短期记忆、画像和长期记忆。"""
        memory_state = AgentMemoryState()
        await self._load_short_term_memory(memory_state, tenant_id, user_id, session_id)
        await self._load_user_profile(memory_state, user_id)
        await self._load_long_term_memory(memory_state, tenant_id, user_id, user_input)
        return memory_state

    async def after_agent(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        user_message: str,
        assistant_message: str,
        long_term_memories: list[MemorySearchResult] | None = None,
    ) -> None:
        """Agent 回复后：写入短期记忆，必要时压缩并抽取长期记忆。"""
        await self._save_short_term_memory_safely(
            tenant_id,
            user_id,
            session_id,
            user_message,
            assistant_message,
        )
        await self._compress_and_extract_long_term_memory(
            tenant_id,
            user_id,
            session_id,
            user_message,
            assistant_message,
        )
        if long_term_memories:
            await self._update_hit_long_term_memories(long_term_memories)

    async def _save_short_term_memory_safely(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """写入短期记忆；失败时只做单次降级告警。"""
        now_ts = int(time.time())
        try:
            meta = await self.redis_stm.get_meta(tenant_id, user_id, session_id)
            meta.total_turns += 1
            meta.last_updated_at = now_ts

            for message in [
                MessageRecord(
                    message_id=f"msg_u_{now_ts}",
                    role="user",
                    content=user_message,
                    created_at=now_ts,
                    turn_index=meta.total_turns,
                ),
                MessageRecord(
                    message_id=f"msg_a_{now_ts}",
                    role="assistant",
                    content=assistant_message,
                    created_at=now_ts,
                    turn_index=meta.total_turns,
                ),
            ]:
                await self.redis_stm.append_message(
                    tenant_id,
                    user_id,
                    session_id,
                    message,
                )

            await self.redis_stm.save_meta(tenant_id, user_id, session_id, meta)
            await self.redis_stm.refresh_ttl(tenant_id, user_id, session_id)
        except Exception:
            self._warn_once("redis_stm_write", "[memory] Redis STM 写入失败")

    async def _compress_and_extract_long_term_memory(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """在压缩成功后触发长期记忆抽取。"""
        try:
            meta = await self.redis_stm.get_meta(tenant_id, user_id, session_id)
            msg_count = await self.redis_stm.get_message_count(
                tenant_id,
                user_id,
                session_id,
            )
            compressed = False
            if self.redis_stm.should_compress(
                meta.total_turns,
                meta.last_compressed_turn,
                msg_count,
            ):
                compressed = await self.redis_stm.compress_session_memory(
                    tenant_id,
                    user_id,
                    session_id,
                    self._build_summary_compressor(meta.total_turns),
                )
            if not compressed or not self.ltm_enabled:
                return

            new_summary = await self.redis_stm.get_summary(tenant_id, user_id, session_id)
            semantic_memories, profile = await self.memory_extractor.extract(
                user_message,
                assistant_message,
                new_summary,
            )
            for memory in semantic_memories:
                should_save_memory = await self.milvus_ltm.deduplicate_memory(
                    tenant_id,
                    user_id,
                    memory.memory_type,
                    memory.content,
                )
                if not should_save_memory:
                    continue
                await self.milvus_ltm.save_memory(
                    tenant_id,
                    user_id,
                    memory.memory_type,
                    memory.content,
                )

            uid = coerce_user_id(user_id)
            if uid > 0 and profile and isinstance(profile, dict):
                try:
                    await self.profile_writer(
                        uid,
                        profile,
                        getattr(self.redis_stm, "redis", None),
                    )
                except Exception as exc:
                    logger.debug(f"[memory] 用户画像更新失败(user_id={user_id}): {exc}")
        except Exception:
            self._warn_once("compress", "[memory] 记忆压缩失败")

    async def _load_short_term_memory(
        self,
        memory_state: AgentMemoryState,
        tenant_id: str,
        user_id: str,
        session_id: str,
    ) -> None:
        """读取 Redis 中的短期摘要和最近消息。"""
        try:
            memory_state.session_summary = await self.redis_stm.get_summary(
                tenant_id,
                user_id,
                session_id,
            )
            memory_state.recent_messages = await self.redis_stm.get_recent_messages(
                tenant_id,
                user_id,
                session_id,
            )
        except Exception:
            self._warn_once("redis_stm_read", "[memory] Redis STM 读取失败，短期记忆降级")
            memory_state.session_summary = None
            memory_state.recent_messages = []

    async def _load_user_profile(
        self,
        memory_state: AgentMemoryState,
        user_id: str,
    ) -> None:
        """读取 MySQL 用户画像。"""
        uid = coerce_user_id(user_id)
        if uid <= 0:
            return
        try:
            memory_state.user_profile = await self.profile_reader(
                uid,
                getattr(self.redis_stm, "redis", None),
            )
        except Exception:
            self._warn_once("user_profile", "[memory] 用户画像读取失败，降级为空画像")
            memory_state.user_profile = {}

    async def _load_long_term_memory(
        self,
        memory_state: AgentMemoryState,
        tenant_id: str,
        user_id: str,
        user_input: str,
    ) -> None:
        """从 Milvus 检索长期语义记忆。"""
        if not self.ltm_enabled:
            return
        try:
            memory_state.long_term_memories = await self.milvus_ltm.hybrid_search(
                tenant_id,
                user_id,
                user_input,
            )
        except Exception:
            self._warn_once("milvus_ltm", "[memory] Milvus LTM 检索失败，长期记忆降级")
            memory_state.long_term_memories = []

    def _build_summary_compressor(self, compressed_round: int) -> _SummaryCompressor:
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
            response = await self.memory_extractor.llm_client.ainvoke(prompt)
            content = getattr(response, "content", response)
            return content if isinstance(content, str) else str(content)

        return llm_compress_func

    async def _update_hit_long_term_memories(
        self,
        long_term_memories: list[MemorySearchResult],
    ) -> None:
        """刷新命中长期记忆的访问统计。"""
        try:
            for result in long_term_memories:
                try:
                    await self.milvus_ltm.update_memory_hit_info(result.memory)
                except Exception:
                    continue
        except Exception:
            self._warn_once("ltm_hit_update", "[memory] LTM 命中统计刷新失败")
