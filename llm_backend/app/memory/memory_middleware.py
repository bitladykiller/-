"""记忆中间件。

统一编排：
- `before_agent`：读取短期记忆、用户画像、长期记忆
- `after_agent`：写入短期记忆、触发压缩、抽取长期记忆、刷新命中信息

本文件重点做流程编排，不把 Redis / Milvus / 画像服务的细节分散到多个调用点。
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

from app.core.logger import get_logger
from app.memory.config import LongTermMemoryConfig, long_term_config
from app.memory.memory_middleware_support import (
    build_summary_compressor,
    load_long_term_memory_state,
    load_short_term_memory_state,
    load_user_profile_state,
    save_profile_if_present,
    save_semantic_memories,
    save_short_term_turn,
    update_hit_long_term_memories,
)
from app.memory.profile_gateway import (
    ProfileReader,
    ProfileWriter,
    load_user_profile,
    save_user_profile,
)
from app.memory.schemas import (
    AgentMemoryState,
    MemorySearchResult,
    SessionSummary,
)

if TYPE_CHECKING:
    from app.memory.memory_extractor import MemoryExtractor
    from app.memory.redis_short_term_memory import RedisShortTermMemory
    from app.memory.simple_long_term_memory import SimpleLongTermMemory

logger = get_logger(__name__)


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

    @property
    def _profile_cache(self):
        """统一暴露用户画像服务可复用的 Redis 客户端。"""
        return getattr(self.redis_stm, "redis", None)

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
        try:
            await self._save_short_term_memory(
                tenant_id, user_id, session_id, user_message, assistant_message
            )
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
            compressed = await self._compress_short_term_memory_if_needed(
                tenant_id, user_id, session_id
            )
            if not compressed or not self.ltm_enabled:
                return

            new_summary = await self.redis_stm.get_summary(tenant_id, user_id, session_id)
            await self._extract_and_save_long_term_memory(
                tenant_id,
                user_id,
                user_message,
                assistant_message,
                new_summary,
            )
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
            await load_short_term_memory_state(
                memory_state,
                redis_stm=self.redis_stm,
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
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
        try:
            await load_user_profile_state(
                memory_state,
                profile_reader=self.profile_reader,
                profile_cache=self._profile_cache,
                user_id=user_id,
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
        try:
            await load_long_term_memory_state(
                memory_state,
                milvus_ltm=self.milvus_ltm,
                ltm_enabled=self.ltm_enabled,
                tenant_id=tenant_id,
                user_id=user_id,
                user_input=user_input,
            )
        except Exception:
            self._warn_once("milvus_ltm", "[memory] Milvus LTM 检索失败，长期记忆降级")
            memory_state.long_term_memories = []

    async def _save_short_term_memory(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """把本轮问答写入 Redis 短期记忆。"""
        now = int(time.time())
        await save_short_term_turn(
            redis_stm=self.redis_stm,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            user_message=user_message,
            assistant_message=assistant_message,
            now_ts=now,
        )

    async def _compress_short_term_memory_if_needed(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
    ) -> bool:
        """满足阈值时触发短期记忆压缩，并返回“压缩是否真的成功”。"""
        meta = await self.redis_stm.get_meta(tenant_id, user_id, session_id)
        msg_count = await self.redis_stm.get_message_count(tenant_id, user_id, session_id)

        if not self.redis_stm.should_compress(
            meta.total_turns,
            meta.last_compressed_turn,
            msg_count,
        ):
            return False

        compressed_round = meta.total_turns
        return await self.redis_stm.compress_session_memory(
            tenant_id,
            user_id,
            session_id,
            self._build_summary_compressor(compressed_round),
        )

    def _build_summary_compressor(self, compressed_round: int):
        """构造 Redis STM 压缩阶段需要的 LLM 摘要回调。"""
        return build_summary_compressor(
            llm_client=self.memory_extractor.llm_client,
            compressed_round=compressed_round,
        )

    async def _extract_and_save_long_term_memory(
        self,
        tenant_id: str,
        user_id: str,
        user_message: str,
        assistant_message: str,
        session_summary: SessionSummary | None,
    ) -> None:
        """压缩完成后抽取语义记忆和结构化画像。"""
        semantic_memories, profile = await self.memory_extractor.extract(
            user_message,
            assistant_message,
            session_summary,
        )
        await save_semantic_memories(
            milvus_ltm=self.milvus_ltm,
            tenant_id=tenant_id,
            user_id=user_id,
            semantic_memories=semantic_memories,
        )
        try:
            await save_profile_if_present(
                profile_writer=self.profile_writer,
                profile_cache=self._profile_cache,
                user_id=user_id,
                profile=profile,
            )
        except Exception as exc:
            logger.debug(f"[memory] 用户画像更新失败(user_id={user_id}): {exc}")

    async def _update_hit_long_term_memories(
        self,
        long_term_memories: list[MemorySearchResult],
    ) -> None:
        """刷新命中长期记忆的访问统计。"""
        await update_hit_long_term_memories(
            milvus_ltm=self.milvus_ltm,
            long_term_memories=long_term_memories,
        )
