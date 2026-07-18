"""记忆中间件。

统一编排：
- `before_agent`：读取短期记忆、用户画像、长期记忆
- `after_agent`：写入短期记忆、触发压缩、抽取长期记忆、刷新命中信息

本文件重点做流程编排，不把 Redis / Milvus / 画像服务的细节分散到多个调用点。
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeAlias

import redis.asyncio as aioredis

from app.shared.core.logger import get_logger
from app.knowledge.domain.prompt_builder import build_compression_prompt
from app.knowledge.domain.schemas import (
    AgentMemoryState,
    MemorySearchResult,
    MessageRecord,
)
from app.user.domain.schemas import UserProfileData
from app.shared.core.config import settings
from app.knowledge.infrastructure.orchestration.profile_adapter import (
    load_user_profile,
    save_user_profile,
)

if TYPE_CHECKING:
    from app.knowledge.infrastructure.orchestration.memory_extractor import MemoryExtractor
    from app.knowledge.infrastructure.stm.redis_short_term_memory import (
        RedisShortTermMemory,
    )
    from app.knowledge.infrastructure.ltm.simple_long_term_memory import (
        SimpleLongTermMemory,
    )

logger = get_logger(__name__)
ProfileReader: TypeAlias = Callable[[int, Any | None], Awaitable[UserProfileData]]
ProfileWriter: TypeAlias = Callable[[int, UserProfileData, Any | None], Awaitable[bool]]


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
        self.ltm_enabled = settings.app_config.memory.ltm.enabled
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
        except (aioredis.RedisError, asyncio.TimeoutError, ConnectionError):
            self._warn_once("redis_stm_read", "[memory] Redis STM 读取失败，短期记忆降级")
            memory_state.session_summary = None
            memory_state.recent_messages = []
        except Exception:
            self._warn_once("redis_stm_read", "[memory] Redis STM 读取失败（未知错误），短期记忆降级")
            memory_state.session_summary = None
            memory_state.recent_messages = []

        uid = int(user_id) if user_id and user_id.isdigit() else 0
        if uid > 0:
            try:
                memory_state.user_profile = await self.profile_reader(
                    uid,
                    getattr(self.redis_stm, "redis", None),
                )
            except (aioredis.RedisError, asyncio.TimeoutError, ConnectionError):
                self._warn_once("user_profile", "[memory] 用户画像读取失败，降级为空画像")
                memory_state.user_profile = {}
            except Exception:
                self._warn_once("user_profile", "[memory] 用户画像读取失败（未知错误），降级为空画像")
                memory_state.user_profile = {}

        if self.ltm_enabled:
            try:
                memory_state.long_term_memories = await self.milvus_ltm.hybrid_search(
                    tenant_id,
                    user_id,
                    user_input,
                )
            except (asyncio.TimeoutError, ConnectionError):
                self._warn_once(
                    "milvus_ltm",
                    "[memory] Milvus LTM 检索失败，长期记忆降级",
                )
                memory_state.long_term_memories = []
            except Exception:
                self._warn_once(
                    "milvus_ltm",
                    "[memory] Milvus LTM 检索失败（未知错误），长期记忆降级",
                )
                memory_state.long_term_memories = []
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
        except (aioredis.RedisError, asyncio.TimeoutError, ConnectionError):
            self._warn_once("redis_stm_write", "[memory] Redis STM 写入失败")
        except Exception:
            self._warn_once("redis_stm_write", "[memory] Redis STM 写入失败（未知错误）")

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
                async def summary_compressor(
                    old_summary_str: str,
                    old_messages: list[MessageRecord],
                ) -> str:
                    prompt = build_compression_prompt(
                        old_summary=old_summary_str,
                        old_messages=old_messages,
                        compressed_round=meta.total_turns,
                    )
                    response = await self.memory_extractor.llm_client.ainvoke(prompt)
                    content = getattr(response, "content", response)
                    return content if isinstance(content, str) else str(content)

                compressed = await self.redis_stm.compress_session_memory(
                    tenant_id,
                    user_id,
                    session_id,
                    summary_compressor,
                )
            if compressed and self.ltm_enabled:
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

                uid = int(user_id) if user_id and user_id.isdigit() else 0
                if uid > 0 and profile and isinstance(profile, dict):
                    try:
                        await self.profile_writer(
                            uid,
                            profile,
                            getattr(self.redis_stm, "redis", None),
                        )
                    except (aioredis.RedisError, asyncio.TimeoutError, ConnectionError) as exc:
                        logger.debug(f"[memory] 用户画像更新失败(user_id={user_id}): {exc}")
                    except Exception as exc:
                        logger.debug(f"[memory] 用户画像更新失败(user_id={user_id}): {exc}")
        except (asyncio.TimeoutError, ConnectionError):
            self._warn_once("compress", "[memory] 记忆压缩失败")
        except Exception:
            self._warn_once("compress", "[memory] 记忆压缩失败（未知错误）")

        if long_term_memories:
            try:
                for result in long_term_memories:
                    try:
                        await self.milvus_ltm.update_memory_hit_info(result.memory)
                    except (asyncio.TimeoutError, ConnectionError):
                        continue
            except (asyncio.TimeoutError, ConnectionError):
                self._warn_once("ltm_hit_update", "[memory] LTM 命中统计刷新失败")
            except Exception:
                self._warn_once("ltm_hit_update", "[memory] LTM 命中统计刷新失败（未知错误）")
