"""
Prompt 构建器。

将短期摘要、最近消息、长期记忆、知识库检索结果、当前用户问题拼接成最终 Prompt。

关键设计：
1. Prompt 中不能暴露 tenant_id、user_id、memory_id 等内部字段
2. 长期记忆只作为辅助参考
3. 用户当前表达优先级高于长期记忆
4. 知识库检索结果优先级高于历史长期记忆
"""

from typing import List, Optional, Dict, Any
from app.memory.schemas import (
    SessionSummary,
    MessageRecord,
    MemorySearchResult,
)



def build_agent_prompt(
    user_input: str,
    session_summary: Optional[SessionSummary] = None,
    recent_messages: Optional[List[MessageRecord]] = None,
    long_term_memories: Optional[List[MemorySearchResult]] = None,
    retrieved_docs: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    构建 Agent Prompt。

    参数：
    - user_input：用户当前问题
    - session_summary：会话摘要
    - recent_messages：最近消息列表
    - long_term_memories：长期记忆列表
    - retrieved_docs：知识库检索结果

    返回：
    - 构建好的 Prompt
    """
    # 构建系统要求部分
    system_requirements = """你是一个智能客服 Agent，需要根据用户当前问题、短期会话记忆、长期用户记忆和知识库检索结果进行回答。

【系统要求】
1. 优先解决用户当前问题。
2. 短期记忆只代表当前会话上下文。
3. 长期记忆只作为辅助参考，不能直接暴露给用户。
4. 如果长期记忆和用户当前表达冲突，以用户当前表达为准。
5. 涉及订单、退款、支付、账号安全等问题时，必须调用对应工具或提示转人工，不能编造状态。
6. 不要告诉用户"我记得你之前……"，除非这个信息对当前服务有帮助。
7. 如果缺少关键信息，要优先追问用户或者调用工具查询。
8. 回答要自然、简洁、像真实客服，不要暴露内部系统字段。"""

    # 构建短期会话摘要部分
    summary_section = _build_summary_section(session_summary)

    # 构建最近对话窗口部分
    messages_section = _build_messages_section(recent_messages)

    # 构建长期记忆部分
    long_term_section = _build_long_term_section(long_term_memories)

    # 构建知识库检索结果部分
    docs_section = _build_docs_section(retrieved_docs)

    # 构建用户当前问题部分
    user_input_section = f"""【用户当前问题】
{user_input}"""

    # 构建输出要求
    output_requirements = """请输出：
1. 给用户的自然语言回复
2. 是否需要调用工具
3. 如果需要调用工具，给出工具名和参数
4. 是否需要转人工"""

    # 拼接最终 Prompt
    prompt = f"""{system_requirements}

{summary_section}

{messages_section}

{long_term_section}

{docs_section}

{user_input_section}

{output_requirements}"""

    return prompt


def _build_summary_section(
    session_summary: Optional[SessionSummary],
) -> str:
    """
    构建会话摘要部分。

    参数：
    - session_summary：会话摘要

    返回：
    - 会话摘要部分
    """
    if not session_summary:
        return """【短期会话摘要】
（无）"""

    # 格式化确认事实
    confirmed_facts = ""
    if session_summary.confirmed_facts:
        confirmed_facts = "\n".join(
            f"  - {fact}" for fact in session_summary.confirmed_facts
        )

    # 格式化尝试过的方案
    tried_solutions = ""
    if session_summary.tried_solutions:
        tried_solutions = "\n".join(
            f"  - {solution}" for solution in session_summary.tried_solutions
        )

    # 格式化拒绝过的方案
    rejected_solutions = ""
    if session_summary.rejected_solutions:
        rejected_solutions = "\n".join(
            f"  - {solution}" for solution in session_summary.rejected_solutions
        )

    # 构建摘要部分
    summary_text = f"""【短期会话摘要】
用户目标：{session_summary.user_goal}
当前状态：{session_summary.current_state}
下一步建议：{session_summary.next_action}"""

    if confirmed_facts:
        summary_text += f"""
已确认信息：
{confirmed_facts}"""

    if tried_solutions:
        summary_text += f"""
已尝试方案：
{tried_solutions}"""

    if rejected_solutions:
        summary_text += f"""
用户拒绝方案：
{rejected_solutions}"""

    return summary_text


def _build_messages_section(
    recent_messages: Optional[List[MessageRecord]],
) -> str:
    """
    构建最近消息部分。

    参数：
    - recent_messages：最近消息列表

    返回：
    - 最近消息部分
    """
    if not recent_messages:
        return """【最近对话窗口】
（无）"""

    # 格式化消息
    messages_text = ""
    for msg in recent_messages:
        role = "用户" if msg.role == "user" else "助手"
        messages_text += f"[{role}]: {msg.content}\n"

    return f"""【最近对话窗口】
{messages_text}"""


def _build_long_term_section(
    long_term_memories: Optional[List[MemorySearchResult]],
) -> str:
    """
    构建长期记忆部分。

    参数：
    - long_term_memories：长期记忆列表

    返回：
    - 长期记忆部分
    """
    if not long_term_memories:
        return """【长期记忆】
（无）"""

    # 格式化长期记忆
    memories_text = ""
    for i, search_result in enumerate(long_term_memories, 1):
        memory = search_result.memory
        memory_type = _get_memory_type_label(memory.memory_type)
        memories_text += f"{i}. {memory_type}：{memory.content}\n"

    return f"""【长期记忆】
{memories_text}"""


def _build_docs_section(
    retrieved_docs: Optional[List[Dict[str, Any]]],
) -> str:
    """
    构建知识库检索结果部分。

    参数：
    - retrieved_docs：知识库检索结果

    返回：
    - 知识库检索结果部分
    """
    if not retrieved_docs:
        return """【知识库检索结果】
（无）"""

    # 格式化检索结果
    docs_text = ""
    for i, doc in enumerate(retrieved_docs, 1):
        content = doc.get("content", "")
        source = doc.get("source", "")
        score = doc.get("score", 0)

        # 截断过长的内容
        if len(content) > 500:
            content = content[:500] + "..."

        docs_text += f"{i}. "
        if source:
            docs_text += f"[来源: {source}] "
        docs_text += f"{content}\n"

    return f"""【知识库检索结果】
{docs_text}"""


def _get_memory_type_label(memory_type: str) -> str:
    """
    获取记忆类型的中文标签。

    参数：
    - memory_type：记忆类型

    返回：
    - 中文标签
    """
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

    用于将会话摘要注入到现有 Prompt 中。

    参数：
    - session_summary：会话摘要

    返回：
    - 会话摘要注入提示
    """
    if not session_summary:
        return ""

    # 格式化确认事实
    confirmed_facts = ""
    if session_summary.confirmed_facts:
        confirmed_facts = "\n".join(
            f"  - {fact}" for fact in session_summary.confirmed_facts
        )

    # 构建摘要注入提示
    summary_text = f"""【会话上下文】
用户目标：{session_summary.user_goal}
当前状态：{session_summary.current_state}"""

    if confirmed_facts:
        summary_text += f"""
已确认信息：
{confirmed_facts}"""

    return summary_text