"""
记忆中间件。

统一处理短期记忆和长期记忆的读取和写入。
before_agent: Agent 执行前读取记忆
after_agent: Agent 回复后写入记忆
各层独立降级，故障不互相影响。
"""
import sys
import time
import asyncio
from typing import Optional, List, Dict, Any
from app.memory.config import SHORT_TERM_MEMORY_CONFIG, LONG_TERM_MEMORY_CONFIG
from app.memory.redis_short_term_memory import RedisShortTermMemory
from app.memory.simple_long_term_memory import SimpleLongTermMemory
from app.memory.memory_extractor import MemoryExtractor
from app.memory.schemas import (
    MessageRecord, SessionMeta, SessionSummary,
    LongTermMemory, MemorySearchResult, AgentMemoryState,
)


class MemoryMiddleware:
    """记忆中间件。

    统一处理短期记忆和长期记忆的读取和写入。
    包含两个阶段：
    1. before_agent：在 Agent 执行前调用
    2. after_agent：在 Agent 回复后调用
    """

    def __init__(
        self,
        redis_stm: RedisShortTermMemory,
        milvus_ltm: SimpleLongTermMemory,
        memory_extractor: MemoryExtractor,
    ):
        self.redis_stm = redis_stm
        self.milvus_ltm = milvus_ltm
        self.memory_extractor = memory_extractor
        self.stm_config = SHORT_TERM_MEMORY_CONFIG
        self.ltm_config = LONG_TERM_MEMORY_CONFIG
        self._healthy: dict[str, bool] = {}
        self._errors_logged: set[str] = set()  # 首次失败 stderr，避免刷屏

    # ------------------------------------------------------------------ #
    # 健康检查
    # ------------------------------------------------------------------ #

    async def health_check(self) -> dict[str, bool]:
        """检查各记忆层连接健康状态，各层独立检查互不影响。"""
        self._healthy = {}

        # Redis STM 检查
        try:
            await self.redis_stm.redis.ping()
            self._healthy["redis_stm"] = True
        except Exception:
            self._healthy["redis_stm"] = False

        # Milvus LTM 检查
        try:
            self._healthy["milvus_ltm"] = self.milvus_ltm.milvus_client is not None
        except Exception:
            self._healthy["milvus_ltm"] = False

        return self._healthy

    def is_healthy(self, layer: str | None = None) -> bool:
        """检查指定层是否健康，未指定则检查全部。"""
        if layer:
            return self._healthy.get(layer, False)
        return all(self._healthy.values()) if self._healthy else False

    # ------------------------------------------------------------------ #
    # before_agent：读取记忆
    # ------------------------------------------------------------------ #

    async def before_agent(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        user_input: str,
    ) -> AgentMemoryState:
        """Agent 执行前：从 Redis 读短期 + 从 Milvus 检索长期。"""
        memory_state = AgentMemoryState()

        # 1. 读取 Redis 短期记忆
        try:
            memory_state.session_summary = await self.redis_stm.get_summary(
                tenant_id, user_id, session_id
            )
            memory_state.recent_messages = await self.redis_stm.get_recent_messages(
                tenant_id, user_id, session_id
            )
        except Exception:
            if "redis_stm_read" not in self._errors_logged:
                print("[memory] Redis STM 读取失败，短期记忆降级", file=sys.stderr)
                self._errors_logged.add("redis_stm_read")
            memory_state.session_summary = None
            memory_state.recent_messages = []

        # 2. MySQL 用户画像（v3.2: 从 MySQL + Redis 缓存读取）
        try:
            from app.services.user_profile_service import UserProfileService
            uid = int(user_id) if user_id and user_id.isdigit() else 0
            if uid > 0:
                profile = await UserProfileService.get_profile(
                    uid, redis_client=getattr(self.redis_stm, 'redis', None)
                )
                memory_state.user_profile = profile
        except Exception:
            if "user_profile" not in self._errors_logged:
                print("[memory] 用户画像读取失败，降级为空画像", file=sys.stderr)
                self._errors_logged.add("user_profile")
            memory_state.user_profile = {}

        # 3. 检索 Milvus 长期记忆（语义）
        try:
            if self.ltm_config.get("enabled", True):
                memory_state.long_term_memories = await self.milvus_ltm.hybrid_search(
                    tenant_id, user_id, user_input
                )
        except Exception:
            if "milvus_ltm" not in self._errors_logged:
                print("[memory] Milvus LTM 检索失败，长期记忆降级", file=sys.stderr)
                self._errors_logged.add("milvus_ltm")
            memory_state.long_term_memories = []

        return memory_state

    # ------------------------------------------------------------------ #
    # after_agent：写入记忆
    # ------------------------------------------------------------------ #

    async def after_agent(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        user_message: str,
        assistant_message: str,
        session_summary: Optional[SessionSummary] = None,
        long_term_memories: Optional[List[MemorySearchResult]] = None,
    ) -> None:
        """Agent 回复后：写入短期 → 压缩 → 抽取长期 → 更新命中。"""
        # 1. 写入 Redis 短期记忆
        try:
            await self._save_short_term_memory(
                tenant_id, user_id, session_id, user_message, assistant_message
            )
        except Exception:
            if "redis_stm_write" not in self._errors_logged:
                print("[memory] Redis STM 写入失败", file=sys.stderr)
                self._errors_logged.add("redis_stm_write")

        # 2. 判断并执行压缩，压缩成功时顺便抽取长期记忆
        try:
            compressed = await self._compress_short_term_memory_if_needed(
                tenant_id, user_id, session_id
            )
            if compressed and self.ltm_config.get("enabled", True):
                # 压缩刚发生，LLM 已经分析过旧对话 → 用新摘要抽取 LTM
                new_summary = await self.redis_stm.get_summary(tenant_id, user_id, session_id)
                await self._extract_and_save_long_term_memory(
                    tenant_id, user_id, session_id,
                    user_message, assistant_message, new_summary,
                )
        except Exception:
            if "compress" not in self._errors_logged:
                print("[memory] 记忆压缩失败", file=sys.stderr)
                self._errors_logged.add("compress")

        # 4. 更新命中的长期记忆
        try:
            if long_term_memories:
                await self._update_hit_long_term_memories(long_term_memories)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 短期记忆操作
    # ------------------------------------------------------------------ #

    async def _save_short_term_memory(
        self, tenant_id, user_id, session_id, user_message, assistant_message
    ):
        meta = await self.redis_stm.get_meta(tenant_id, user_id, session_id)
        meta.total_turns += 1
        meta.last_updated_at = int(time.time())

        now = int(time.time())
        user_msg = MessageRecord(
            message_id=f"msg_u_{now}",
            role="user",
            content=user_message,
            created_at=now,
            turn_index=meta.total_turns,
        )
        asst_msg = MessageRecord(
            message_id=f"msg_a_{now}",
            role="assistant",
            content=assistant_message,
            created_at=now,
            turn_index=meta.total_turns,
        )

        await self.redis_stm.append_message(tenant_id, user_id, session_id, user_msg)
        await self.redis_stm.append_message(tenant_id, user_id, session_id, asst_msg)
        await self.redis_stm.save_meta(tenant_id, user_id, session_id, meta)
        await self.redis_stm.refresh_ttl(tenant_id, user_id, session_id)

    async def _compress_short_term_memory_if_needed(
        self, tenant_id, user_id, session_id
    ) -> bool:
        meta = await self.redis_stm.get_meta(tenant_id, user_id, session_id)
        msg_count = await self.redis_stm.get_message_count(
            tenant_id, user_id, session_id
        )

        if not self.redis_stm.should_compress(
            meta.total_turns, meta.last_compressed_turn, msg_count
        ):
            return False

        async def llm_compress_func(old_summary_str, old_messages):
            prompt = self._build_compress_prompt(old_summary_str, old_messages)
            response = await self.memory_extractor.llm_client.ainvoke(prompt)
            raw = response.content if hasattr(response, "content") else str(response)
            return raw

        await self.redis_stm.compress_session_memory(
            tenant_id, user_id, session_id, llm_compress_func
        )
        return True

    # ------------------------------------------------------------------ #
    # 长期记忆操作
    # ------------------------------------------------------------------ #

    async def _extract_and_save_long_term_memory(
        self, tenant_id, user_id, session_id,
        user_message, assistant_message, session_summary,
    ):
        semantic, profile = await self.memory_extractor.extract(
            user_message, assistant_message, session_summary
        )

        # 语义记忆 → Milvus
        for mem in semantic:
            exists = await self.milvus_ltm.deduplicate_memory(
                tenant_id, user_id, mem.memory_type, mem.content
            )
            if not exists:
                await self.milvus_ltm.save_memory(
                    tenant_id, user_id, mem.memory_type, mem.content
                )

        # 结构化画像 → MySQL + 清除 Redis 缓存
        if profile and isinstance(profile, dict):
            try:
                from app.services.user_profile_service import UserProfileService
                uid = int(user_id) if user_id and user_id.isdigit() else 0
                if uid > 0:
                    if profile.get("preferred_brand") or profile.get("budget_range"):
                        await UserProfileService.upsert_profile(
                            user_id=uid,
                            preferred_brand=profile.get("preferred_brand"),
                            budget_range=profile.get("budget_range"),
                            preferred_category=profile.get("preferred_category"),
                            tags=profile.get("tags"),
                            redis_client=getattr(self.redis_stm, 'redis', None),
                        )
                    for fact in (profile.get("facts") or []):
                        if fact.get("key") and fact.get("value"):
                            await UserProfileService.upsert_fact(
                                user_id=uid,
                                fact_key=fact["key"],
                                fact_value=fact["value"],
                                redis_client=getattr(self.redis_stm, 'redis', None),
                            )
            except Exception:
                pass

    async def _update_hit_long_term_memories(
        self, long_term_memories: List[MemorySearchResult]
    ):
        for result in long_term_memories:
            try:
                await self.milvus_ltm.update_memory_hit_info(result.memory)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # 压缩 Prompt
    # ------------------------------------------------------------------ #

    def _build_compress_prompt(self, old_summary_str: str, old_messages: list) -> str:
        messages_text = "\n".join(
            f"[{m.role}]: {m.content}" for m in old_messages
            if hasattr(m, 'role') and hasattr(m, 'content')
        )

        return f"""你是对话摘要助手。请将以下对话历史压缩为JSON格式的会话摘要。

已有的摘要（如有）：{old_summary_str or "无"}

最近的对话：
{messages_text}

请输出JSON，包含以下字段：
- user_goal: 用户当前主要诉求
- confirmed_facts: 已确认的信息列表
- tried_solutions: 已尝试的方案列表
- rejected_solutions: 用户拒绝的方案列表
- current_state: 当前问题状态
- next_action: 下一步建议动作

只输出JSON，不要其他内容。"""

    def _parse_compress_response(self, response: str) -> SessionSummary:
        import re, json
        try:
            match = re.search(r"\{.*\}", response, re.DOTALL)
            if match:
                data = json.loads(match.group())
                return SessionSummary(**data)
        except Exception:
            pass
        return SessionSummary()
