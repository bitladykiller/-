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
from app.knowledge.domain.prompt_builder import build_compression_prompt
from app.knowledge.domain.schemas import (
    AgentMemoryState,
    MemorySearchResult,
    MessageRecord,
    SessionMeta,
    SessionSummary,
)
from app.knowledge.infrastructure.orchestration.profile_adapter import (
    load_user_profile,
    save_user_profile,
)
from app.shared.core.config import settings
from app.shared.core.logger import get_logger
from app.user.domain.schemas import UserProfileData

if TYPE_CHECKING:
    from app.knowledge.infrastructure.ltm.simple_long_term_memory import (
        SimpleLongTermMemory,
    )
    from app.knowledge.infrastructure.orchestration.memory_extractor import MemoryExtractor
    from app.knowledge.infrastructure.stm.redis_short_term_memory import (
        RedisShortTermMemory,
    )

logger = get_logger(__name__)
ProfileReader: TypeAlias = Callable[[int, Any | None], Awaitable[UserProfileData]]
ProfileWriter: TypeAlias = Callable[[int, UserProfileData, Any | None], Awaitable[bool]]


def _parse_numeric_user_id(user_id: str) -> int:
    """把 user_id 解析成正整数；匿名或非数字返回 0。"""
    return int(user_id) if user_id and user_id.isdigit() else 0


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
        """Agent 执行前：读取短期记忆、画像和长期记忆。

        三路读取（STM / 画像 / LTM）落在三套互不相干的存储上，彼此没有数据依赖，
        因此并发发起：耗时从"三者之和"降到"三者最大值"。这是每轮对话的必经路径，
        其中 LTM 检索还要做 embedding + 向量检索，往往是最慢的一路。

        任意一路失败都只降级该路，不影响其它记忆来源，也不让主对话链路失败。
        """
        stm_task = self._read_short_term(tenant_id, user_id, session_id)
        profile_task = self._read_user_profile(user_id)
        ltm_task = self._read_long_term(tenant_id, user_id, user_input)

        (session_summary, recent_messages), user_profile, long_term_memories = (
            await asyncio.gather(stm_task, profile_task, ltm_task)
        )

        memory_state = AgentMemoryState()
        memory_state.session_summary = session_summary
        memory_state.recent_messages = recent_messages
        if user_profile is not None:
            memory_state.user_profile = user_profile
        memory_state.long_term_memories = long_term_memories
        return memory_state

    async def _read_short_term(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
    ) -> tuple[SessionSummary | None, list[MessageRecord]]:
        """读取会话摘要与最近消息；失败时整体降级为空。"""
        try:
            return await asyncio.gather(
                self.redis_stm.get_summary(tenant_id, user_id, session_id),
                self.redis_stm.get_recent_messages(tenant_id, user_id, session_id),
            )
        except (aioredis.RedisError, asyncio.TimeoutError, ConnectionError):
            self._warn_once("redis_stm_read", "[memory] Redis STM 读取失败，短期记忆降级")
        except Exception:
            self._warn_once(
                "redis_stm_read", "[memory] Redis STM 读取失败（未知错误），短期记忆降级"
            )
        return None, []

    async def _read_user_profile(self, user_id: str) -> UserProfileData | None:
        """读取用户画像；非数字 user_id 视为匿名，直接跳过。"""
        uid = _parse_numeric_user_id(user_id)
        if uid <= 0:
            return None
        try:
            return await self.profile_reader(uid, getattr(self.redis_stm, "redis", None))
        except (aioredis.RedisError, asyncio.TimeoutError, ConnectionError):
            self._warn_once("user_profile", "[memory] 用户画像读取失败，降级为空画像")
        except Exception:
            self._warn_once(
                "user_profile", "[memory] 用户画像读取失败（未知错误），降级为空画像"
            )
        return {}

    async def _read_long_term(
        self,
        tenant_id: str,
        user_id: str,
        user_input: str,
    ) -> list[MemorySearchResult]:
        """检索长期记忆；未开启或失败时返回空列表。"""
        if not self.ltm_enabled:
            return []
        try:
            return await self.milvus_ltm.hybrid_search(tenant_id, user_id, user_input)
        except (asyncio.TimeoutError, ConnectionError):
            self._warn_once("milvus_ltm", "[memory] Milvus LTM 检索失败，长期记忆降级")
        except Exception:
            self._warn_once(
                "milvus_ltm", "[memory] Milvus LTM 检索失败（未知错误），长期记忆降级"
            )
        return []

    async def after_agent(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        user_message: str,
        assistant_message: str,
        long_term_memories: list[MemorySearchResult] | None = None,
    ) -> None:
        """Agent 回复后写记忆。

        阶段：
        1) 写 STM 本轮 user/assistant + meta + 刷新 TTL
        2) 达阈值则压缩；压缩成功且 ltm_enabled 再 extract → LTM/画像
        3) 可选刷新本轮命中的 LTM hit 统计
        """
        now_ts = int(time.time())
        # ---- 1) 短期记忆落盘 ----
        meta: SessionMeta | None = None
        try:
            meta = await self.redis_stm.get_meta(tenant_id, user_id, session_id)
            meta.total_turns += 1
            meta.last_updated_at = now_ts

            # 一轮对话的 user + assistant 两条消息合并成一次 Redis 往返
            await self.redis_stm.append_messages(
                tenant_id,
                user_id,
                session_id,
                [
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
                ],
            )

            await self.redis_stm.save_meta(tenant_id, user_id, session_id, meta)
            await self.redis_stm.refresh_ttl(tenant_id, user_id, session_id)
        except (aioredis.RedisError, asyncio.TimeoutError, ConnectionError):
            self._warn_once("redis_stm_write", "[memory] Redis STM 写入失败")
        except Exception:
            self._warn_once("redis_stm_write", "[memory] Redis STM 写入失败（未知错误）")

        # ---- 2) 压缩 + 抽取（仅 should_compress 为真时）----
        # WHY 不在每轮 extract：控 LLM 成本，压缩点语义更完整
        try:
            # 复用第 1 段刚写回的 meta，省一次 Redis 往返；
            # 只有第 1 段失败（meta 为 None）时才回源重读。
            if meta is None:
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
                        session_id=session_id,
                    )

                uid = _parse_numeric_user_id(user_id)
                if uid > 0 and profile and isinstance(profile, dict):
                    try:
                        await self.profile_writer(
                            uid,
                            profile,
                            getattr(self.redis_stm, "redis", None),
                        )
                    except Exception as exc:
                        logger.debug(f"[memory] 用户画像更新失败(user_id={user_id}): {exc}")
        except (asyncio.TimeoutError, ConnectionError):
            self._warn_once("compress", "[memory] 记忆压缩失败")
        except Exception:
            self._warn_once("compress", "[memory] 记忆压缩失败（未知错误）")

        # ---- 3) 命中统计（旁路逻辑，失败不影响主路径）----
        # 一次 upsert 刷完全部命中，避免逐条往返 Milvus
        if long_term_memories:
            try:
                await self.milvus_ltm.update_memory_hit_infos(
                    [result.memory for result in long_term_memories]
                )
            except (asyncio.TimeoutError, ConnectionError):
                self._warn_once("ltm_hit_update", "[memory] LTM 命中统计刷新失败")
            except Exception:
                self._warn_once("ltm_hit_update", "[memory] LTM 命中统计刷新失败（未知错误）")
