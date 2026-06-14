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


def _coerce_llm_response_text(response) -> str:
    """把 LLM 返回统一收口成字符串。"""
    content = getattr(response, "content", response)
    return content if isinstance(content, str) else str(content)


def coerce_user_id(user_id: str) -> int:
    """把字符串 user_id 安全转换为 int，失败时返回 0。"""
    return int(user_id) if user_id and user_id.isdigit() else 0


def profile_cache(redis_stm: Any) -> Any | None:
    """统一暴露用户画像服务可复用的 Redis 客户端。"""
    return getattr(redis_stm, "redis", None)


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


def warn_once(
    errors_warned: set[str],
    *,
    key: str,
    message: str,
    logger: Any,
) -> None:
    """同一类降级警告仅记录一次，避免日志刷屏。"""
    if key in errors_warned:
        return
    logger.warning(message)
    errors_warned.add(key)


def build_summary_compressor(
    *,
    llm_client,
    compressed_round: int,
) -> _SummaryCompressor:
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
        return _coerce_llm_response_text(response)

    return llm_compress_func


def _build_turn_messages(
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


async def save_short_term_turn(
    *,
    redis_stm,
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

    messages = _build_turn_messages(
        user_message=user_message,
        assistant_message=assistant_message,
        created_at=now_ts,
        turn_index=meta.total_turns,
    )
    for message in messages:
        await redis_stm.append_message(tenant_id, user_id, session_id, message)

    await redis_stm.save_meta(tenant_id, user_id, session_id, meta)
    await redis_stm.refresh_ttl(tenant_id, user_id, session_id)


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


async def load_short_term_memory_safely(
    memory_state: AgentMemoryState,
    *,
    redis_stm: Any,
    tenant_id: str,
    user_id: str,
    session_id: str,
    load_short_term_memory_state: Callable[..., Awaitable[None]],
    warn_once: Callable[[str, str], None],
) -> None:
    """读取短期记忆；失败时降级为空摘要和空消息。"""
    try:
        await load_short_term_memory_state(
            memory_state,
            redis_stm=redis_stm,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
        )
    except Exception:
        warn_once("redis_stm_read", "[memory] Redis STM 读取失败，短期记忆降级")
        memory_state.session_summary = None
        memory_state.recent_messages = []


async def load_user_profile_safely(
    memory_state: AgentMemoryState,
    *,
    profile_reader: Any,
    profile_cache: Any | None,
    user_id: str,
    load_user_profile_state: Callable[..., Awaitable[None]],
    warn_once: Callable[[str, str], None],
) -> None:
    """读取用户画像；失败时降级为空画像。"""
    try:
        await load_user_profile_state(
            memory_state,
            profile_reader=profile_reader,
            profile_cache=profile_cache,
            user_id=user_id,
        )
    except Exception:
        warn_once("user_profile", "[memory] 用户画像读取失败，降级为空画像")
        memory_state.user_profile = {}


async def load_long_term_memory_safely(
    memory_state: AgentMemoryState,
    *,
    milvus_ltm: Any,
    ltm_enabled: bool,
    tenant_id: str,
    user_id: str,
    user_input: str,
    load_long_term_memory_state: Callable[..., Awaitable[None]],
    warn_once: Callable[[str, str], None],
) -> None:
    """读取长期记忆；失败时降级为空列表。"""
    try:
        await load_long_term_memory_state(
            memory_state,
            milvus_ltm=milvus_ltm,
            ltm_enabled=ltm_enabled,
            tenant_id=tenant_id,
            user_id=user_id,
            user_input=user_input,
        )
    except Exception:
        warn_once("milvus_ltm", "[memory] Milvus LTM 检索失败，长期记忆降级")
        memory_state.long_term_memories = []


async def save_short_term_memory_safely(
    *,
    redis_stm: Any,
    tenant_id: str,
    user_id: str,
    session_id: str,
    user_message: str,
    assistant_message: str,
    now_ts: int,
    save_short_term_turn: Callable[..., Awaitable[None]],
    warn_once: Callable[[str, str], None],
) -> None:
    """写入短期记忆；失败时只做单次降级告警。"""
    try:
        await save_short_term_turn(
            redis_stm=redis_stm,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            user_message=user_message,
            assistant_message=assistant_message,
            now_ts=now_ts,
        )
    except Exception:
        warn_once("redis_stm_write", "[memory] Redis STM 写入失败")


async def compress_short_term_memory_if_needed(
    *,
    redis_stm: Any,
    tenant_id: str,
    user_id: str,
    session_id: str,
    build_summary_compressor: Callable[[int], Any],
) -> bool:
    """满足阈值时触发短期记忆压缩，并返回是否真的成功。"""
    meta = await redis_stm.get_meta(tenant_id, user_id, session_id)
    msg_count = await redis_stm.get_message_count(tenant_id, user_id, session_id)
    if not redis_stm.should_compress(
        meta.total_turns,
        meta.last_compressed_turn,
        msg_count,
    ):
        return False

    compressed_round = meta.total_turns
    return await redis_stm.compress_session_memory(
        tenant_id,
        user_id,
        session_id,
        build_summary_compressor(compressed_round),
    )


async def extract_and_save_long_term_memory(
    *,
    memory_extractor: Any,
    milvus_ltm: Any,
    profile_writer: Any,
    profile_cache: Any | None,
    tenant_id: str,
    user_id: str,
    user_message: str,
    assistant_message: str,
    session_summary: SessionSummary | None,
    save_semantic_memories: Callable[..., Awaitable[None]],
    save_profile_if_present: Callable[..., Awaitable[None]],
    logger: Any,
) -> tuple[list[MemoryExtractorResult], UserProfileData]:
    """压缩完成后抽取语义记忆和结构化画像，并尽力回写。"""
    semantic_memories, profile = await memory_extractor.extract(
        user_message,
        assistant_message,
        session_summary,
    )
    await save_semantic_memories(
        milvus_ltm=milvus_ltm,
        tenant_id=tenant_id,
        user_id=user_id,
        semantic_memories=semantic_memories,
    )
    try:
        await save_profile_if_present(
            profile_writer=profile_writer,
            profile_cache=profile_cache,
            user_id=user_id,
            profile=profile,
        )
    except Exception as exc:
        logger.debug(f"[memory] 用户画像更新失败(user_id={user_id}): {exc}")
    return semantic_memories, profile


async def update_hit_long_term_memories_safely(
    *,
    milvus_ltm: Any,
    long_term_memories: list[MemorySearchResult],
    update_hit_long_term_memories: Callable[..., Awaitable[None]],
    warn_once: Callable[[str, str], None],
) -> None:
    """刷新命中长期记忆；失败时只做单次降级告警。"""
    try:
        await update_hit_long_term_memories(
            milvus_ltm=milvus_ltm,
            long_term_memories=long_term_memories,
        )
    except Exception:
        warn_once("ltm_hit_update", "[memory] LTM 命中统计刷新失败")


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
        return profile_cache(self.redis_stm)

    def _warn_once(self, key: str, message: str) -> None:
        """同一类降级警告仅记录一次，避免日志刷屏。"""
        warn_once(
            self._errors_warned,
            key=key,
            message=message,
            logger=logger,
        )

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
        await save_short_term_memory_safely(
            redis_stm=self.redis_stm,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            user_message=user_message,
            assistant_message=assistant_message,
            now_ts=int(time.time()),
            save_short_term_turn=save_short_term_turn,
            warn_once=self._warn_once,
        )

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
            compressed = await compress_short_term_memory_if_needed(
                redis_stm=self.redis_stm,
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                build_summary_compressor=self._build_summary_compressor,
            )
            if not compressed or not self.ltm_enabled:
                return

            new_summary = await self.redis_stm.get_summary(tenant_id, user_id, session_id)
            await extract_and_save_long_term_memory(
                memory_extractor=self.memory_extractor,
                milvus_ltm=self.milvus_ltm,
                profile_writer=self.profile_writer,
                profile_cache=self._profile_cache,
                tenant_id=tenant_id,
                user_id=user_id,
                user_message=user_message,
                assistant_message=assistant_message,
                session_summary=new_summary,
                save_semantic_memories=save_semantic_memories,
                save_profile_if_present=save_profile_if_present,
                logger=logger,
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
        await load_short_term_memory_safely(
            memory_state,
            redis_stm=self.redis_stm,
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            load_short_term_memory_state=load_short_term_memory_state,
            warn_once=self._warn_once,
        )

    async def _load_user_profile(
        self,
        memory_state: AgentMemoryState,
        user_id: str,
    ) -> None:
        """读取 MySQL 用户画像。"""
        await load_user_profile_safely(
            memory_state,
            profile_reader=self.profile_reader,
            profile_cache=self._profile_cache,
            user_id=user_id,
            load_user_profile_state=load_user_profile_state,
            warn_once=self._warn_once,
        )

    async def _load_long_term_memory(
        self,
        memory_state: AgentMemoryState,
        tenant_id: str,
        user_id: str,
        user_input: str,
    ) -> None:
        """从 Milvus 检索长期语义记忆。"""
        await load_long_term_memory_safely(
            memory_state,
            milvus_ltm=self.milvus_ltm,
            ltm_enabled=self.ltm_enabled,
            tenant_id=tenant_id,
            user_id=user_id,
            user_input=user_input,
            load_long_term_memory_state=load_long_term_memory_state,
            warn_once=self._warn_once,
        )

    def _build_summary_compressor(self, compressed_round: int):
        """构造 Redis STM 压缩阶段需要的 LLM 摘要回调。"""
        return build_summary_compressor(
            llm_client=self.memory_extractor.llm_client,
            compressed_round=compressed_round,
        )

    async def _update_hit_long_term_memories(
        self,
        long_term_memories: list[MemorySearchResult],
    ) -> None:
        """刷新命中长期记忆的访问统计。"""
        await update_hit_long_term_memories_safely(
            milvus_ltm=self.milvus_ltm,
            long_term_memories=long_term_memories,
            update_hit_long_term_memories=update_hit_long_term_memories,
            warn_once=self._warn_once,
        )
