"""
Prompt 构建器。

v3.17: 移除未使用的 build_agent_prompt 及其辅助函数（_build_summary_section /
_build_messages_section / _build_long_term_section / _build_docs_section）。
Agent Prompt 构建已由 lg_context.py 的 build_memory_context + 优先级模型替代。

保留以下活跃函数：
- build_memory_injection_prompt：用于将 Milvus LTM 注入系统 Prompt
- build_summary_injection_prompt：用于将会话摘要注入系统 Prompt
"""

from typing import List, Optional
from app.memory.schemas import (
    SessionSummary,
    MemorySearchResult,
)


def _get_memory_type_label(memory_type: str) -> str:
    """获取记忆类型的中文标签。"""
    labels = {
        "user_profile": "用户画像",
        "issue_history": "历史问题",
        "solution_note": "有效方案",
    }
    return labels.get(memory_type, memory_type)


def build_memory_injection_prompt(
    long_term_memories: Optional[List[MemorySearchResult]],
) -> str:
    """
    构建长期记忆注入提示。

    用于将长期记忆注入到现有 Prompt 中。

    参数：
    - long_term_memories：长期记忆列表

    返回：
    - 长期记忆注入提示
    """
    if not long_term_memories:
        return ""

    # 格式化长期记忆
    memories_text = ""
    for i, search_result in enumerate(long_term_memories, 1):
        memory = search_result.memory
        memory_type = _get_memory_type_label(memory.memory_type)
        memories_text += f"{i}. {memory_type}：{memory.content}\n"

    return f"""【长期记忆参考】
{memories_text}
注意：以上长期记忆仅供参考，用户当前表达优先级更高。"""


def build_summary_injection_prompt(
    session_summary: Optional[SessionSummary],
) -> str:
    """
    构建会话摘要注入提示。

    直接透传 content，不拆分 field。
    """
    if not session_summary or not session_summary.content:
        return ""

    return f"""【会话上下文】
{session_summary.content}"""