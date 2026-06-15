"""LangGraph 消息辅助函数。

职责：
- 负责 LangChain 消息对象到模型输入消息的转换
- 负责 user 消息安全包装与标准消息列表构造

边界：
- 不负责节点路由和检索执行
- 不负责记忆上下文拼装
"""

import html
from collections.abc import Sequence

from langchain_core.messages import AnyMessage, ChatMessage


def wrap_user_message(raw_input: str) -> str:
    """将用户输入包裹在 XML 标签中，减少伪造闭合标签的风险。"""
    escaped = html.escape(raw_input, quote=False)
    return f"<user_message>\n{escaped}\n</user_message>"


def normalize_message_role(message: AnyMessage) -> str:
    """把 LangChain 消息角色收口为稳定的 user/assistant/... 角色名。"""
    raw_role = message.role if isinstance(message, ChatMessage) else message.type
    return {
        "human": "user",
        "ai": "assistant",
    }.get(raw_role, raw_role)


def extract_message_content(message: AnyMessage) -> str:
    """提取消息 content，并统一为字符串。"""
    return str(message.content or "")


def build_safe_messages(
    system_prompt: str,
    messages: Sequence[AnyMessage],
) -> list[dict[str, str]]:
    """构建安全消息列表，对 user 消息做 XML 隔离防注入。"""
    safe: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for message in messages:
        role = normalize_message_role(message)
        content = extract_message_content(message)
        if role == "user":
            safe.append({"role": "user", "content": wrap_user_message(content)})
            continue
        safe.append({"role": role, "content": content})
    return safe
