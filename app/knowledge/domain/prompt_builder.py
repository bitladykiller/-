"""记忆相关 Prompt 片段构建器。

职责：
- 生成长期记忆注入片段
- 生成会话摘要注入片段
- 生成短期记忆压缩提示词

边界：
- 这里只负责拼接局部提示片段
- 完整的 Agent 上下文组装由 `lg_context.py` 负责
"""
from __future__ import annotations

from app.knowledge.domain.schemas import (
    MemorySearchResult,
    MessageRecord,
    SessionSummary,
)

_MEMORY_TYPE_LABELS = {
    "issue_history": "历史问题",
    "solution_note": "有效方案",
}
_LONG_TERM_MEMORY_TITLE = "长期记忆参考"
_SESSION_SUMMARY_TITLE = "会话上下文"
_LONG_TERM_MEMORY_NOTE = "注意：以上长期记忆仅供参考，用户当前表达优先级更高。"
_COMPRESSION_ASSISTANT_ROLE = "对话摘要助手"


def _build_prompt_block(
    title: str,
    body: str,
    *,
    note: str = "",
) -> str:
    """把标题、正文和可选说明拼成统一提示片段。"""
    if not body:
        return ""

    block = f"【{title}】\n{body}"
    return f"{block}\n{note}" if note else block


def build_memory_injection_prompt(
    long_term_memories: list[MemorySearchResult] | None,
) -> str:
    """构建长期记忆注入提示。"""
    if not long_term_memories:
        return ""

    memory_lines: list[str] = []
    for index, search_result in enumerate(long_term_memories, 1):
        memory = search_result.memory
        memory_type_label = _MEMORY_TYPE_LABELS.get(
            memory.memory_type,
            memory.memory_type,
        )
        memory_lines.append(f"{index}. {memory_type_label}：{memory.content}")

    memories_text = "\n".join(memory_lines)
    return _build_prompt_block(
        _LONG_TERM_MEMORY_TITLE,
        memories_text,
        note=_LONG_TERM_MEMORY_NOTE,
    )


def build_summary_injection_prompt(
    session_summary: SessionSummary | None,
) -> str:
    """构建会话摘要注入提示。"""
    if not session_summary or not session_summary.content:
        return ""
    return _build_prompt_block(_SESSION_SUMMARY_TITLE, session_summary.content)


def build_compression_prompt(
    old_summary: str,
    old_messages: list[MessageRecord],
    compressed_round: int,
) -> str:
    """构建 STM 压缩阶段发给 LLM 的摘要提示词。"""
    messages_text = "\n".join(
        f"[{message.role}]: {message.content}"
        for message in old_messages
        if getattr(message, "role", None) and getattr(message, "content", None)
    )
    return f"""你是{_COMPRESSION_ASSISTANT_ROLE}。请将以下对话历史压缩为一段简洁的摘要。

已有的摘要（如有）：{old_summary or "无"}

最近的对话：
{messages_text}

请用一段中文概括这段对话，内容包括：
- 用户问了什么、关心什么
- Agent 给出了什么信息、做了什么
- 尚未解决的问题或待确认的事项

输出严格JSON格式，包含两个字段：
- "content": 上述摘要文本（自由格式，一段中文）
- "compressed_at": {compressed_round}  ← 直接用这个值
- "compressed_round": {compressed_round}  ← 直接用这个值

只输出JSON，不要其他内容。"""
