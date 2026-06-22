"""LangGraph 记忆上下文组装层。

职责：
- 统一加载当前请求的记忆状态
- 组装记忆上下文文本和富化问题
- 为上层节点提供记忆请求入口（加载 / 富化问题）

不负责：
- LangGraph 节点路由
- 具体检索执行
- 记忆抽取和持久化细节
- 运行时依赖初始化和单例生命周期
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.chat.infrastructure.graph.state import AgentState
from app.knowledge.domain.prompt_builder import (
    build_memory_injection_prompt,
    build_summary_injection_prompt,
)
from app.knowledge.domain.schemas import (
    AgentMemoryState,
    MemorySearchResult,
    MessageRecord,
    SessionSummary,
    UserProfileData,
)

_DEFAULT_TENANT_ID = "default"
_DEFAULT_USER_ID = "anonymous"
_DEFAULT_SESSION_ID = "default"
_RECENT_MESSAGE_ROLE_LABELS = {
    "user": "用户",
    "assistant": "助手",
    "tool": "工具",
    "system": "系统",
}
_USER_PROFILE_TEXT_LABELS = {
    "preferred_brand": "偏好品牌",
    "budget_range": "预算范围",
    "preferred_category": "偏好品类",
}
_MEMORY_SECTION_TITLES = {
    "recent_messages": "P0 — 最近对话（权威性最高，冲突时以此为准）",
    "user_profile": "P1 — 用户画像（多次对话提炼，冲突时次于 P0）",
    "session_summary": "P2 — 会话摘要（压缩的旧对话，冲突时次于 P1）",
    "long_term_memory": "P3 — 长期记忆（历史跨会话，冲突时优先级最低）",
}
_MEMORY_INSTRUCTIONS = "【记忆说明】当以下信息来源存在矛盾时，优先信任 P0 > P1 > P2 > P3。"


def build_memory_section(title: str, body: str) -> str:
    if not body:
        return ""
    return f"[{title}]\n{body}"


def format_recent_messages(recent_messages: list[MessageRecord]) -> str:
    lines: list[str] = []
    for message in recent_messages:
        role = _RECENT_MESSAGE_ROLE_LABELS.get(message.role, message.role)
        lines.append(f"[{role}]: {message.content}")
    return "\n".join(lines)


def format_user_profile(user_profile: UserProfileData) -> str:
    profile_lines: list[str] = []
    for field_name, label in _USER_PROFILE_TEXT_LABELS.items():
        value = user_profile.get(field_name)
        if isinstance(value, str) and value:
            profile_lines.append(f"{label}: {value}")

    normalized_tags = [
        tag for tag in user_profile.get("tags", []) if isinstance(tag, str) and tag
    ]
    if normalized_tags:
        profile_lines.append(f"标签: {', '.join(normalized_tags)}")

    for fact in user_profile.get("facts", []):
        key = fact.get("key")
        value = fact.get("value")
        if isinstance(key, str) and key and isinstance(value, str) and value:
            profile_lines.append(f"{key}: {value}")
    return "\n".join(profile_lines)


def build_memory_context(
    session_summary: SessionSummary | None,
    recent_messages: list[MessageRecord],
    long_term_memories: list[MemorySearchResult],
    user_profile: UserProfileData | None = None,
) -> str:
    parts = [
        build_memory_section(
            _MEMORY_SECTION_TITLES["recent_messages"],
            format_recent_messages(recent_messages),
        ),
        build_memory_section(
            _MEMORY_SECTION_TITLES["user_profile"],
            format_user_profile(user_profile) if user_profile else "",
        ),
        build_memory_section(
            _MEMORY_SECTION_TITLES["session_summary"],
            build_summary_injection_prompt(session_summary),
        ),
        build_memory_section(
            _MEMORY_SECTION_TITLES["long_term_memory"],
            build_memory_injection_prompt(long_term_memories),
        ),
    ]
    parts = [section for section in parts if section and section.strip()]
    if not parts:
        return ""
    return _MEMORY_INSTRUCTIONS + "\n\n" + "\n\n".join(parts)


def build_enriched_question(
    question: str,
    memory_state: AgentMemoryState,
) -> str:
    context = build_memory_context(
        memory_state.session_summary,
        memory_state.recent_messages,
        memory_state.long_term_memories,
        memory_state.user_profile,
    )
    return f"{context}\n\n用户当前问题：{question}" if context else question


def configurable_scope(config: RunnableConfig) -> tuple[str, str, str]:
    raw_configurable = config.get("configurable", {})
    configurable = raw_configurable if isinstance(raw_configurable, dict) else {}
    tenant_id = configurable.get("tenant_id")
    user_id = configurable.get("user_id")
    thread_id = configurable.get("thread_id")
    return (
        tenant_id if isinstance(tenant_id, str) and tenant_id else _DEFAULT_TENANT_ID,
        user_id if isinstance(user_id, str) and user_id else _DEFAULT_USER_ID,
        thread_id if isinstance(thread_id, str) and thread_id else _DEFAULT_SESSION_ID,
    )


async def _get_memory_middleware():
    """从 AppContainer 获取 MemoryMiddleware。"""
    from app.platform.container import get_container

    container = await get_container()
    return container.memory_middleware


async def load_memory_state(
    state: AgentState,
    config: RunnableConfig,
    user_input: str,
) -> AgentMemoryState | None:
    if state.memory_state is not None:
        return state.memory_state

    middleware = await _get_memory_middleware()
    if middleware is None:
        return None

    try:
        tenant_id, user_id, session_id = configurable_scope(config)
        memory_state = await middleware.before_agent(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            user_input=user_input,
        )
    except Exception:
        logger.warning(
            "[memory] 记忆加载失败，将以无记忆状态运行 | tenant=%s user=%s session=%s",
            tenant_id,
            user_id,
            session_id,
            exc_info=True,
        )
        return None

    state.memory_state = memory_state
    return memory_state


async def enrich_question(
    state: AgentState,
    config: RunnableConfig,
    question: str,
) -> str:
    mem = await load_memory_state(state, config, question)
    if mem is None:
        return question
    return build_enriched_question(question, mem)


__all__ = [
    "build_enriched_question",
    "build_memory_context",
    "configurable_scope",
    "enrich_question",
    "load_memory_state",
]
