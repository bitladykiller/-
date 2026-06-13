"""
Prompt 注入防御。

职责：
- 提供面向 Prompt 的 XML 转义与包裹工具
- 为 Agent 节点建立最外层的用户输入信任边界

边界：
- 这里只做轻量字符串防护，不承载完整安全策略编排
"""
from __future__ import annotations

import html

__all__ = ["xml_escape", "wrap_user_message"]
USER_MESSAGE_TAG = "user_message"


def _wrap_xml_tag(tag: str, content: str) -> str:
    """把内容包进指定 XML 标签块。"""
    return f"<{tag}>\n{content}\n</{tag}>"


def xml_escape(text: str) -> str:
    """转义 < > & ，防止用户伪造 XML 闭合标签。

    >>> xml_escape('</user_message>')
    '&lt;/user_message&gt;'

    注意：只转义 XML 关键字符，不转义引号（不影响 LLM 理解）。
    """
    return html.escape(text, quote=False)


def wrap_user_message(raw_input: str) -> tuple[str, str]:
    """将用户输入包裹在 <user_message> XML 标签中，建立信任边界。

    Returns:
        (wrapped_message, display_text) — wrapped 用于嵌入 prompt，display 保留原文供记忆存储
    """
    escaped = xml_escape(raw_input)
    wrapped = _wrap_xml_tag(USER_MESSAGE_TAG, escaped)
    return wrapped, raw_input
