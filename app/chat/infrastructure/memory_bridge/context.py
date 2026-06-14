"""LangGraph 记忆上下文组装层。

这个模块负责：
- 统一加载当前请求的记忆状态
- 组装记忆上下文文本和富化问题
- 为上层节点提供记忆请求入口（加载 / 富化问题）

这个模块不负责：
- LangGraph 节点路由
- 具体检索执行
- 记忆抽取和持久化细节
- 具体的上下文文本拼装
- 运行时依赖初始化和单例生命周期
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from app.chat.infrastructure.graph.state import AgentState
from app.chat.infrastructure.memory_bridge.runtime import (
    get_memory_middleware,
)
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
    UserProfileFact,
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
    """把段落标题和正文拼成统一分段格式。"""
    if not body:
        return ""
    return f"[{title}]\n{body}"


def format_recent_messages(recent_messages: list[MessageRecord]) -> str:
    """格式化最近对话记录，供 P0 记忆段落使用。"""
    lines: list[str] = []
    for message in recent_messages:
        role = _RECENT_MESSAGE_ROLE_LABELS.get(message.role, message.role)
        lines.append(f"[{role}]: {message.content}")
    return "\n".join(lines)


def _format_profile_facts(facts: list[UserProfileFact]) -> list[str]:
    """格式化用户画像里的 key-value 事实。"""
    lines: list[str] = []
    for fact in facts:
        key = fact.get("key")
        value = fact.get("value")
        if isinstance(key, str) and key and isinstance(value, str) and value:
            lines.append(f"{key}: {value}")
    return lines


def _format_profile_tags(tags: list[str]) -> str | None:
    """格式化用户画像标签，顺手过滤空字符串和脏值。"""
    normalized_tags = [tag for tag in tags if isinstance(tag, str) and tag]
    if not normalized_tags:
        return None
    return f"标签: {', '.join(normalized_tags)}"


def format_user_profile(user_profile: UserProfileData) -> str:
    """格式化结构化用户画像，供 P1 记忆段落使用。"""
    profile_lines: list[str] = []
    for field_name, label in _USER_PROFILE_TEXT_LABELS.items():
        value = user_profile.get(field_name)
        if isinstance(value, str) and value:
            profile_lines.append(f"{label}: {value}")

    tags_line = _format_profile_tags(user_profile.get("tags", []))
    if tags_line is not None:
        profile_lines.append(tags_line)

    profile_lines.extend(_format_profile_facts(user_profile.get("facts", [])))
    return "\n".join(profile_lines)


def build_memory_context(
    session_summary: SessionSummary | None,
    recent_messages: list[MessageRecord],
    long_term_memories: list[MemorySearchResult],
    user_profile: UserProfileData | None = None,
) -> str:
    """组装带优先级的记忆上下文字符串，用于注入 system prompt。"""
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
    """把记忆上下文注入到用户问题前。"""
    context = build_memory_context(
        memory_state.session_summary,
        memory_state.recent_messages,
        memory_state.long_term_memories,
        memory_state.user_profile,
    )
    return f"{context}\n\n用户当前问题：{question}" if context else question


def _get_configurable_value(
    config: RunnableConfig,
    key: str,
    default: str,
) -> str:
    """读取 LangGraph `configurable` 中的值，缺失时回退到默认值。"""
    configurable = config.get("configurable", {})
    value = configurable.get(key)
    return value if isinstance(value, str) and value else default


def configurable_scope(config: RunnableConfig) -> tuple[str, str, str]:
    """统一读取当前请求的 tenant / user / session 标识。"""
    return (
        _get_configurable_value(config, "tenant_id", _DEFAULT_TENANT_ID),
        _get_configurable_value(config, "user_id", _DEFAULT_USER_ID),
        _get_configurable_value(config, "thread_id", _DEFAULT_SESSION_ID),
    )


async def load_memory_state(
    state: AgentState,
    config: RunnableConfig,
    user_input: str,
) -> AgentMemoryState | None:
    """加载并缓存当前请求的记忆状态。"""
    if state.memory_state is not None:
        return state.memory_state

    middleware = await get_memory_middleware()
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
        return None

    state.memory_state = memory_state
    return memory_state


async def enrich_question(
    state: AgentState,
    config: RunnableConfig,
    question: str,
) -> str:
    """将记忆上下文注入到检索问题中。"""
    mem = await load_memory_state(state, config, question)
    if mem is None:
        return question

    return build_enriched_question(question, mem)


__all__ = [
    "build_enriched_question",
    "build_memory_context",
    "configurable_scope",
    "enrich_question",
    "get_memory_middleware",
    "load_memory_state",
]
