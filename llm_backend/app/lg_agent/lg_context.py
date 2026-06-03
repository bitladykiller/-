"""
记忆中间件单例管理 + 上下文构建。

v3.15: 从 lg_builder.py 拆分。负责：
- MemoryMiddleware 单例的懒初始化和生命周期管理
- 记忆上下文的组装（用户画像 + 对话记录 + 会话摘要 + LTM）
- 查询增强（将记忆上下文注入到检索问题中）
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from langchain_core.runnables import RunnableConfig

from app.core.config import settings, ServiceType
from app.memory.redis_short_term_memory import RedisShortTermMemory
from app.memory.simple_long_term_memory import SimpleLongTermMemory
from app.memory.memory_extractor import MemoryExtractor
from app.memory.memory_middleware import MemoryMiddleware
from app.memory.prompt_builder import build_memory_injection_prompt, build_summary_injection_prompt
from app.lg_agent.lg_states import AgentState

logger = logging.getLogger(__name__)


# ================================================================== #
# MemoryMiddleware 单例 — 懒初始化 + asyncio.Lock 防并发
# ================================================================== #

_memory_middleware_instance: Optional[MemoryMiddleware] = None
_memory_middleware_lock: asyncio.Lock = asyncio.Lock()


async def _get_memory_middleware() -> Optional[MemoryMiddleware]:
    """获取 MemoryMiddleware 单例。

    首次调用时创建：Redis STM + Milvus LTM + MemoryExtractor。
    创建失败时返回 None（降级为无记忆模式）。
    使用 asyncio.Lock 防止并发请求重复创建实例。
    """
    global _memory_middleware_instance
    if _memory_middleware_instance is not None:
        return _memory_middleware_instance
    async with _memory_middleware_lock:
        # double-check：锁内再检查一次，避免重复创建
        if _memory_middleware_instance is not None:
            return _memory_middleware_instance
        # v3.16 修复：创建逻辑必须在锁内，否则 double-check 形同虚设
        try:
            import redis.asyncio as redis
            from pymilvus import MilvusClient

            redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
            redis_stm = RedisShortTermMemory(redis_client)

            # 根据 EMBEDDING_TYPE 选择 embedding 模型
            if settings.EMBEDDING_TYPE == "ollama":
                from langchain_ollama import OllamaEmbeddings
                embedding_model = OllamaEmbeddings(
                    model=settings.EMBEDDING_MODEL,
                    base_url=settings.OLLAMA_BASE_URL,
                )
            else:
                from langchain_community.embeddings import HuggingFaceEmbeddings
                embedding_model = HuggingFaceEmbeddings(model_name=settings.EMBEDDING_MODEL)

            milvus_client = MilvusClient(uri=settings.MILVUS_URL)
            milvus_ltm = SimpleLongTermMemory(
                milvus_client=milvus_client,
                embedding_model=embedding_model,
                collection_name=settings.MILVUS_COLLECTION_NAME,
            )

            # 记忆抽取用独立的 LLM 实例（低温度保证抽取一致性）
            if settings.AGENT_SERVICE == ServiceType.DEEPSEEK:
                from langchain_deepseek import ChatDeepSeek
                extractor_llm = ChatDeepSeek(
                    api_key=settings.DEEPSEEK_API_KEY,
                    model_name=settings.DEEPSEEK_MODEL,
                    temperature=0.3,
                )
            else:
                from langchain_ollama import ChatOllama
                extractor_llm = ChatOllama(
                    model=settings.OLLAMA_AGENT_MODEL,
                    base_url=settings.OLLAMA_BASE_URL,
                    temperature=0.3,
                )

            memory_extractor = MemoryExtractor(llm_client=extractor_llm)
            _memory_middleware_instance = MemoryMiddleware(
                redis_stm=redis_stm,
                milvus_ltm=milvus_ltm,
                memory_extractor=memory_extractor,
            )
            # 异步健康检查，不阻塞首次调用，保存引用防止异常丢失
            _health_check_task = asyncio.create_task(_memory_middleware_instance.health_check())
            _health_check_task.add_done_callback(
                lambda t: t.exception() if not t.cancelled() else None
            )
            return _memory_middleware_instance
        except Exception:
            logger.error("MemoryMiddleware 初始化失败，将以无记忆模式运行", exc_info=True)
            return None


async def close_memory_middleware() -> None:
    """关闭 MemoryMiddleware 及其底层连接。在应用 shutdown 时调用。"""
    global _memory_middleware_instance
    if _memory_middleware_instance is None:
        return
    try:
        await _memory_middleware_instance.redis_stm.redis.close()
    except Exception:
        pass
    try:
        milvus_client = getattr(_memory_middleware_instance.milvus_ltm, 'milvus_client', None)
        if milvus_client:
            milvus_client.close()
    except Exception:
        pass
    _memory_middleware_instance = None


# ================================================================== #
# 记忆优先级模型
# ================================================================== #
#
# 设计原则（WHY）：
# 三层记忆可能包含相互矛盾的信息。例如用户画像说「偏好小米」，
# 但最近消息里用户说「这次想试试华为」——此时应以最近消息为准。
#
# 优先级从高到低：
#   P0 — 最近消息（最新意图，权威性最高）
#   P1 — 用户画像（结构化持久偏好，比语义记忆更可靠）
#   P2 — 会话摘要（压缩了最近 10+ 轮对话，补充上下文）
#   P3 — 长期记忆（历史跨会话语义记忆，时效性最弱）
#
# 处理冲突的规则交给 LLM，通过分级标记「优先级: P0」让 LLM 知道
# 当信息矛盾时应该更信任优先级高的来源。
# ================================================================== #


def build_memory_context(
    session_summary: str,
    recent_messages: list,
    long_term_memories: list,
    user_profile: Optional[dict] = None,
) -> str:
    """组装带优先级的记忆上下文字符串，用于注入 system prompt。

    记忆分层（按优先级降序）：
    P0: 最近对话记录 — 最新意图，权威性最高。和用户当前问题最相关，当与其他记忆冲突时以此为准。
    P1: 用户画像 — MySQL 中的结构化持久偏好。已从多次对话中提炼，比单条语义记忆可靠。
    P2: 会话摘要 — LLM 压缩的旧对话摘要。覆盖时间范围比最近消息广，但信息密度低。
    P3: 长期记忆 — Milvus 检出的历史语义记忆。时间跨度最大，最可能过时，冲突时优先级最低。

    Args:
        session_summary: Redis STM 中的会话摘要。
        recent_messages: 最近 N 条消息（含 role/content）。
        long_term_memories: Milvus LTM 检索结果。
        user_profile: MySQL 用户画像（可选）。

    Returns:
        拼接后的上下文字符串。格式带有明确的优先级标记，
        供 LLM 在信息冲突时选择更权威的来源。无内容时返回空字符串。
    """
    parts = []
    instructions = "【记忆说明】当以下信息来源存在矛盾时，优先信任 P0 > P1 > P2 > P3。\n"

    # --- P0: 最近对话 — 最高优先级 --- #
    if recent_messages:
        messages_text = ""
        for msg in recent_messages:
            role = "用户" if msg.role == "user" else "助手"
            messages_text += f"[{role}]: {msg.content}\n"
        parts.append("[P0 — 最近对话（权威性最高，冲突时以此为准）]\n" + messages_text)

    # --- P1: 用户画像 — 结构化持久偏好 --- #
    if user_profile and isinstance(user_profile, dict):
        profile_lines = []
        if user_profile.get("preferred_brand"):
            profile_lines.append(f"偏好品牌: {user_profile['preferred_brand']}")
        if user_profile.get("budget_range"):
            profile_lines.append(f"预算范围: {user_profile['budget_range']}")
        if user_profile.get("preferred_category"):
            profile_lines.append(f"偏好品类: {user_profile['preferred_category']}")
        if user_profile.get("tags"):
            profile_lines.append(f"标签: {', '.join(user_profile['tags'])}")
        for fact in (user_profile.get("facts") or []):
            profile_lines.append(f"{fact.get('key', '')}: {fact.get('value', '')}")
        if profile_lines:
            parts.append("[P1 — 用户画像（多次对话提炼，冲突时次于 P0）]\n" + "\n".join(profile_lines))

    # --- P2: 会话摘要 — 压缩旧对话 --- #
    summary_text = build_summary_injection_prompt(session_summary)
    if summary_text:
        parts.append("[P2 — 会话摘要（压缩的旧对话，冲突时次于 P1）]\n" + summary_text)

    # --- P3: 长期记忆 — 历史跨会话语义记忆 --- #
    ltm_text = build_memory_injection_prompt(long_term_memories)
    if ltm_text:
        parts.append("[P3 — 长期记忆（历史跨会话，冲突时优先级最低）]\n" + ltm_text)

    if not parts:
        return ""
    return instructions + "\n\n" + "\n\n".join(parts)


async def enrich_question(
    state: AgentState,
    config: RunnableConfig,
    question: str,
) -> str:
    """将记忆上下文注入到检索问题中。

    用于执行节点（execute_*）在检索前增强问题，
    使检索结果更符合用户的历史上下文和偏好。

    v3.17 优化：首次获取记忆后缓存到 state.memory_state，
    避免同一条请求中多次调用 middleware.before_agent() 导致的
    重复 Redis STM + MySQL Profile + Milvus LTM 开销。

    Args:
        state: 当前 Agent 状态（会修改其 memory_state 字段做缓存）。
        config: LangGraph RunnableConfig（含 user_id/session_id）。
        question: 原始用户问题。

    Returns:
        注入记忆上下文后的问题。注入失败时返回原问题。
    """
    # 复用已缓存的记忆状态，避免重复查询 Redis/MySQL/Milvus
    if state.memory_state is not None:
        mem = state.memory_state
    else:
        middleware = await _get_memory_middleware()
        if middleware is None:
            return question
        try:
            c = config.get("configurable", {})
            mem = await middleware.before_agent(
                tenant_id=c.get("tenant_id", "default"),
                user_id=c.get("user_id", "anonymous"),
                session_id=c.get("thread_id", "default"),
                user_input=question,
            )
            state.memory_state = mem  # 缓存供后续节点复用
        except Exception:
            return question

    ctx = build_memory_context(
        mem.session_summary,
        mem.recent_messages,
        mem.long_term_memories,
        mem.user_profile,
    )
    return f"{ctx}\n\n用户当前问题：{question}" if ctx else question
