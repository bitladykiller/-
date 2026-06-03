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
# 记忆上下文构建
# ================================================================== #

def build_memory_context(
    session_summary: str,
    recent_messages: list,
    long_term_memories: list,
    user_profile: Optional[dict] = None,
) -> str:
    """组装完整的记忆上下文字符串，用于注入 system prompt。

    按优先级拼接：
    1. 用户画像（品牌偏好/预算/品类/标签/事实）
    2. 最近对话记录
    3. 会话摘要（LLM 生成的压缩摘要）
    4. 长期记忆（Milvus LTM 检索的相关记忆）

    Args:
        session_summary: Redis STM 中的会话摘要。
        recent_messages: 最近 N 条消息（含 role/content）。
        long_term_memories: Milvus LTM 检索结果。
        user_profile: MySQL 用户画像（可选）。

    Returns:
        拼接后的上下文字符串。无内容时返回空字符串。
    """
    parts = []

    # --- 用户画像 --- #
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
            parts.append("【用户画像】\n" + "\n".join(profile_lines))

    # --- 最近对话记录 --- #
    if recent_messages:
        messages_text = ""
        for msg in recent_messages:
            role = "用户" if msg.role == "user" else "助手"
            messages_text += f"[{role}]: {msg.content}\n"
        parts.append(f"【最近对话记录】\n{messages_text}")

    # --- 会话摘要 --- #
    summary_text = build_summary_injection_prompt(session_summary)
    if summary_text:
        parts.append(summary_text)

    # --- 长期记忆 --- #
    ltm_text = build_memory_injection_prompt(long_term_memories)
    if ltm_text:
        parts.append(ltm_text)

    if not parts:
        return ""
    return "\n\n" + "\n\n".join(parts)


async def enrich_question(
    state: AgentState,
    config: RunnableConfig,
    question: str,
) -> str:
    """将记忆上下文注入到检索问题中。

    用于执行节点（execute_*）在检索前增强问题，
    使检索结果更符合用户的历史上下文和偏好。

    Args:
        state: 当前 Agent 状态。
        config: LangGraph RunnableConfig（含 user_id/session_id）。
        question: 原始用户问题。

    Returns:
        注入记忆上下文后的问题。注入失败时返回原问题。
    """
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
        ctx = build_memory_context(
            mem.session_summary,
            mem.recent_messages,
            mem.long_term_memories,
            mem.user_profile,
        )
        return f"{ctx}\n\n用户当前问题：{question}" if ctx else question
    except Exception:
        return question
