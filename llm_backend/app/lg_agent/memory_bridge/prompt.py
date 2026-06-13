"""LangGraph 记忆上下文提示词构造。

负责：
- 把最近消息、用户画像、会话摘要、长期记忆组装成分级上下文
- 统一定义不同记忆来源的优先级说明
- 生成带记忆上下文的富化问题

不负责：
- 记忆中间件初始化
- Redis / MySQL / Milvus 访问
- LangGraph 节点路由
"""

from __future__ import annotations

from app.memory.prompt_builder import (
    build_memory_injection_prompt,
    build_summary_injection_prompt,
)
from app.memory.schemas import (
    AgentMemoryState,
    MemorySearchResult,
    MessageRecord,
    SessionSummary,
    UserProfileData,
    UserProfileFact,
)

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


def _build_memory_section(title: str, body: str) -> str:
    """把段落标题和正文拼成统一分段格式。"""
    if not body:
        return ""
    return f"[{title}]\n{body}"


def _format_recent_messages(recent_messages: list[MessageRecord]) -> str:
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


def _format_user_profile(user_profile: UserProfileData) -> str:
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


def _build_recent_messages_section(recent_messages: list[MessageRecord]) -> str:
    """构造 P0 最近对话分段。"""
    return _build_memory_section(
        _MEMORY_SECTION_TITLES["recent_messages"],
        _format_recent_messages(recent_messages),
    )


def _build_user_profile_section(user_profile: UserProfileData | None) -> str:
    """构造 P1 用户画像分段。"""
    if not user_profile:
        return ""
    return _build_memory_section(
        _MEMORY_SECTION_TITLES["user_profile"],
        _format_user_profile(user_profile),
    )


def _build_session_summary_section(session_summary: SessionSummary | None) -> str:
    """构造 P2 会话摘要分段。"""
    return _build_memory_section(
        _MEMORY_SECTION_TITLES["session_summary"],
        build_summary_injection_prompt(session_summary),
    )


def _build_long_term_memory_section(
    long_term_memories: list[MemorySearchResult],
) -> str:
    """构造 P3 长期记忆分段。"""
    return _build_memory_section(
        _MEMORY_SECTION_TITLES["long_term_memory"],
        build_memory_injection_prompt(long_term_memories),
    )


def _build_memory_sections(
    session_summary: SessionSummary | None,
    recent_messages: list[MessageRecord],
    long_term_memories: list[MemorySearchResult],
    user_profile: UserProfileData | None,
) -> list[str]:
    """按优先级生成所有非空记忆分段。"""
    sections = [
        _build_recent_messages_section(recent_messages),
        _build_user_profile_section(user_profile),
        _build_session_summary_section(session_summary),
        _build_long_term_memory_section(long_term_memories),
    ]
    return [section for section in sections if section and section.strip()]


def build_memory_context(
    session_summary: SessionSummary | None,
    recent_messages: list[MessageRecord],
    long_term_memories: list[MemorySearchResult],
    user_profile: UserProfileData | None = None,
) -> str:
    """组装带优先级的记忆上下文字符串，用于注入 system prompt。"""
    parts = _build_memory_sections(
        session_summary,
        recent_messages,
        long_term_memories,
        user_profile,
    )
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


__all__ = [
    "build_memory_context",
    "build_enriched_question",
]
