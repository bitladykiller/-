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

from app.chat.infrastructure.graph.state import AgentState
from app.chat.infrastructure.memory_bridge.runtime import get_memory_middleware
from app.knowledge.domain.schemas import (
    AgentMemoryState,
    MemorySearchResult,
    MessageRecord,
    SessionSummary,
    UserProfileData,
)
from langchain_core.runnables import RunnableConfig

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
_MEMORY_TYPE_LABELS = {
    "issue_history": "历史问题",
    "solution_note": "有效方案",
}
_MEMORY_SECTION_TITLES = {
    "recent_messages": "P0 — 最近对话（权威性最高，冲突时以此为准）",
    "user_profile": "P1 — 用户画像（多次对话提炼，冲突时次于 P0）",
    "session_summary": "P2 — 会话摘要（压缩的旧对话，冲突时次于 P1）",
    "long_term_memory": "P3 — 长期记忆（历史跨会话，冲突时优先级最低）",
}


def build_memory_context(
    session_summary: SessionSummary | None,
    recent_messages: list[MessageRecord],
    long_term_memories: list[MemorySearchResult],
    user_profile: UserProfileData | None = None,
) -> str:
    """组装带优先级的记忆上下文字符串，用于注入 system prompt。"""
    recent_message_lines: list[str] = []
    for message in recent_messages:
        role = _RECENT_MESSAGE_ROLE_LABELS.get(message.role, message.role)
        recent_message_lines.append(f"[{role}]: {message.content}")

    user_profile_text = ""
    if user_profile:
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
        user_profile_text = "\n".join(profile_lines)

    long_term_memory_text = ""
    if long_term_memories:
        memory_lines: list[str] = []
        for index, search_result in enumerate(long_term_memories, 1):
            memory = search_result.memory
            memory_type_label = _MEMORY_TYPE_LABELS.get(
                memory.memory_type,
                memory.memory_type,
            )
            memory_lines.append(f"{index}. {memory_type_label}：{memory.content}")
        if memory_lines:
            joined_memory_lines = "\n".join(memory_lines)
            long_term_memory_text = (
                "【长期记忆参考】\n"
                f"{joined_memory_lines}\n"
                "注意：以上长期记忆仅供参考，用户当前表达优先级更高。"
            )

    parts: list[str] = []
    for title, body in (
        (
            _MEMORY_SECTION_TITLES["recent_messages"],
            "\n".join(recent_message_lines),
        ),
        (_MEMORY_SECTION_TITLES["user_profile"], user_profile_text),
        (
            _MEMORY_SECTION_TITLES["session_summary"],
            f"【会话上下文】\n{session_summary.content}"
            if session_summary and session_summary.content
            else "",
        ),
        (_MEMORY_SECTION_TITLES["long_term_memory"], long_term_memory_text),
    ):
        if body:
            parts.append(f"[{title}]\n{body}")
    parts = [section for section in parts if section.strip()]
    if not parts:
        return ""
    return "【记忆说明】当以下信息来源存在矛盾时，优先信任 P0 > P1 > P2 > P3。" + "\n\n" + "\n\n".join(parts)


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
        raw_configurable = config.get("configurable", {})
        configurable = raw_configurable if isinstance(raw_configurable, dict) else {}
        tenant_id = configurable.get("tenant_id")
        user_id = configurable.get("user_id")
        thread_id = configurable.get("thread_id")
        memory_state = await middleware.before_agent(
            tenant_id=tenant_id if isinstance(tenant_id, str) and tenant_id else _DEFAULT_TENANT_ID,
            user_id=user_id if isinstance(user_id, str) and user_id else _DEFAULT_USER_ID,
            session_id=thread_id if isinstance(thread_id, str) and thread_id else _DEFAULT_SESSION_ID,
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

    context = build_memory_context(
        mem.session_summary,
        mem.recent_messages,
        mem.long_term_memories,
        mem.user_profile,
    )
    return f"{context}\n\n用户当前问题：{question}" if context else question
