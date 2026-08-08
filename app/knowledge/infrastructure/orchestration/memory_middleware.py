"""记忆中间件。

统一编排：
- `before_agent`：读取短期记忆、用户画像、长期记忆
- `after_agent`：写入短期记忆、触发压缩、抽取长期记忆、刷新命中信息

本文件重点做流程编排，不把 Redis / Milvus / 画像服务的细节分散到多个调用点。

v3.36+:
- after_agent 返回 TurnMemoryReport，handler 据此决定补偿策略
- 各视图独立追踪状态到 turn_view_status 表
- 压缩使用 compression_id 显式幂等键
- hit_count 通过 memory_hit_events 表去重
- 画像写支持 source_turn_id 传递
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeAlias

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
    save_user_profile_with_source,
)
from app.knowledge.infrastructure.orchestration.turn_view_tracker import (
    TurnMemoryReport,
    ViewName,
    ViewStatus,
    build_compression_id,
)
from app.shared.core.config import settings
from app.shared.core.degradation import log_degradation
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
ProfileReader: TypeAlias = Callable[[str, int, Any | None], Awaitable[UserProfileData]]
ProfileWriter: TypeAlias = Callable[
    [str, int, UserProfileData, Any | None, str | None], Awaitable[bool]
]


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
        profile_writer: ProfileWriter = save_user_profile_with_source,
    ):
        self.redis_stm = redis_stm
        self.milvus_ltm = milvus_ltm
        self.memory_extractor = memory_extractor
        self.profile_reader = profile_reader
        self.profile_writer = profile_writer
        self.ltm_enabled = settings.app_config.memory.ltm.enabled
        self._errors_warned: set[str] = set()

    def _degrade_once(self, key: str, operation: str, exc: BaseException, **context: Any) -> None:
        """同一类降级只记录一次，避免日志刷屏。

        与旧版 `_warn_once` 的区别：旧版只打一句固定文案，既没有异常对象也没有
        堆栈——外部抖动和代码缺陷在日志里长得一模一样。现在交给 `log_degradation`
        分类：外部故障 warning，非预期异常 error + 完整堆栈。

        仍然按 key 去重：首次已经带上完整堆栈，足够定位和告警，
        没必要让同一个问题每轮对话都刷一遍。
        """
        if key in self._errors_warned:
            return
        self._errors_warned.add(key)
        log_degradation(logger, operation, exc, **context)

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
        profile_task = self._read_user_profile(tenant_id, user_id)
        ltm_task = self._read_long_term(tenant_id, user_id, user_input)

        (session_summary, recent_messages), user_profile, long_term_memories = await asyncio.gather(
            stm_task, profile_task, ltm_task
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
        except Exception as exc:
            self._degrade_once(
                "redis_stm_read",
                "memory.read_short_term",
                exc,
                tenant=tenant_id,
                user=user_id,
                session=session_id,
            )
        return None, []

    async def _read_user_profile(
        self,
        tenant_id: str,
        user_id: str,
    ) -> UserProfileData | None:
        """读取指定租户下用户画像；非数字 user_id 视为匿名，直接跳过。"""
        uid = _parse_numeric_user_id(user_id)
        if uid <= 0:
            return None
        try:
            return await self.profile_reader(
                tenant_id,
                uid,
                getattr(self.redis_stm, "redis", None),
            )
        except Exception as exc:
            self._degrade_once(
                "user_profile",
                "memory.read_user_profile",
                exc,
                tenant=tenant_id,
                user=uid,
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
        except Exception as exc:
            self._degrade_once(
                "milvus_ltm",
                "memory.read_long_term",
                exc,
                tenant=tenant_id,
                user=user_id,
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
        *,
        turn_id: str = "",
        event_created_at: int | None = None,
        persist_view_status: bool = True,
    ) -> TurnMemoryReport:
        """Agent 回复后写记忆。

        阶段：
        1) 写 STM 本轮 user/assistant + meta + 刷新 TTL
        2) 达阈值则压缩；压缩成功且 ltm_enabled 再 extract → LTM/画像
        3) 可选刷新本轮命中的 LTM hit 统计

        v3.36+: 返回 TurnMemoryReport，handler 据此决定补偿策略。
        persist_view_status=False 用于 fire-and-forget 回退路径（MySQL 不可用）。
        """
        report = TurnMemoryReport(turn_id=turn_id)

        now_ts = int(event_created_at or time.time())

        # ---- 1) 短期记忆落盘 ----
        meta: SessionMeta | None = None
        try:
            if turn_id and hasattr(self.redis_stm, "append_turn_once"):
                _inserted, meta = await self.redis_stm.append_turn_once(
                    tenant_id,
                    user_id,
                    session_id,
                    turn_id=turn_id,
                    user_message=user_message,
                    assistant_message=assistant_message,
                    created_at=now_ts,
                )
                await self.redis_stm.refresh_ttl(tenant_id, user_id, session_id)
            else:
                meta = await self.redis_stm.get_meta(tenant_id, user_id, session_id)
                meta.total_turns += 1
                meta.last_updated_at = now_ts

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

            report.record(ViewName.STM.value, ViewStatus.COMPLETED)
        except Exception as exc:
            self._degrade_once(
                "redis_stm_write",
                "memory.write_short_term",
                exc,
                tenant=tenant_id,
                user=user_id,
                session=session_id,
            )
            report.record(ViewName.STM.value, ViewStatus.FAILED, str(exc))

        # ---- 2) 压缩 + 抽取（仅 should_compress 为真时）----
        # WHY 不在每轮 extract：控 LLM 成本，压缩点语义更完整
        compressed = False
        semantic_memories: list[Any] = []
        try:
            if meta is None:
                meta = await self.redis_stm.get_meta(tenant_id, user_id, session_id)
            msg_count = await self.redis_stm.get_message_count(
                tenant_id,
                user_id,
                session_id,
            )
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

                # v3.36+: 传递 compression_id 给压缩方法
                compression_id = build_compression_id(
                    session_id, meta.last_compressed_turn, meta.total_turns
                )
                compressed = await self.redis_stm.compress_session_memory(
                    tenant_id,
                    user_id,
                    session_id,
                    summary_compressor,
                    compression_id=compression_id,
                )

            # 写入 LTM 语义记忆
            ltm_saved_count = 0

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
                    save_kwargs: dict[str, str] = {"session_id": session_id}
                    if turn_id:
                        save_kwargs["memory_id"] = str(
                            uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"{turn_id}:{memory.memory_type}:{memory.content}",
                            )
                        )
                    await self.milvus_ltm.save_memory(
                        tenant_id,
                        user_id,
                        memory.memory_type,
                        memory.content,
                        **save_kwargs,
                    )
                    ltm_saved_count += 1

                report.record(
                    ViewName.LTM.value,
                    ViewStatus.COMPLETED if ltm_saved_count > 0 else ViewStatus.SKIPPED,
                )

                # 写入用户画像（v3.36+: 带 source_turn_id）
                uid = _parse_numeric_user_id(user_id)
                if uid > 0 and profile and isinstance(profile, dict):
                    try:
                        await self.profile_writer(
                            tenant_id,
                            uid,
                            profile,
                            getattr(self.redis_stm, "redis", None),
                            turn_id if turn_id else None,
                        )
                        report.record(ViewName.PROFILE.value, ViewStatus.COMPLETED)
                    except Exception as exc:
                        log_degradation(logger, "memory.write_user_profile", exc, user=user_id)
                        report.record(ViewName.PROFILE.value, ViewStatus.FAILED, str(exc))
                else:
                    report.record(ViewName.PROFILE.value, ViewStatus.SKIPPED)

            if compressed:
                report.record(ViewName.COMPRESSION.value, ViewStatus.COMPLETED)
                if not self.ltm_enabled:
                    report.record(ViewName.LTM.value, ViewStatus.SKIPPED)
                    report.record(ViewName.PROFILE.value, ViewStatus.SKIPPED)
            else:
                report.record(ViewName.COMPRESSION.value, ViewStatus.SKIPPED)
                report.record(ViewName.LTM.value, ViewStatus.SKIPPED)
                report.record(ViewName.PROFILE.value, ViewStatus.SKIPPED)
        except Exception as exc:
            self._degrade_once(
                "compress",
                "memory.compress_and_extract",
                exc,
                tenant=tenant_id,
                user=user_id,
                session=session_id,
            )
            report.record(ViewName.COMPRESSION.value, ViewStatus.FAILED, str(exc))
            report.record(ViewName.LTM.value, ViewStatus.FAILED, str(exc))
            report.record(ViewName.PROFILE.value, ViewStatus.FAILED, str(exc))

        # ---- 3) 命中统计（旁路逻辑，去重写入）----
        if long_term_memories:
            try:
                # v3.36+: 使用带去重的命中更新方法
                await self.milvus_ltm.update_memory_hit_infos_deduped(
                    [result.memory for result in long_term_memories],
                    turn_id=turn_id,
                )
                report.record(ViewName.HITS.value, ViewStatus.COMPLETED)
            except Exception as exc:
                self._degrade_once(
                    "ltm_hit_update",
                    "memory.refresh_ltm_hits",
                    exc,
                    tenant=tenant_id,
                    user=user_id,
                )
                report.record(ViewName.HITS.value, ViewStatus.FAILED, str(exc))
        else:
            report.record(ViewName.HITS.value, ViewStatus.SKIPPED)

        # 持久化视图状态到 MySQL（仅事件路径）
        if persist_view_status and turn_id:
            try:
                from app.knowledge.infrastructure.orchestration.turn_view_tracker import (
                    record_all_view_statuses,
                )

                await record_all_view_statuses(turn_id, report, tenant_id=tenant_id)
            except Exception:
                logger.warning(
                    "记录视图状态失败 | turn=%s",
                    turn_id,
                    exc_info=True,
                )

        return report
