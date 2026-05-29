"""
Prompt 注入防御 — XML 标签隔离 + 字符转义。

核心思路：
将用户输入包裹在 <user_message> 标签中，并对用户输入做 XML 转义，
防止攻击者伪造闭合标签。配合 Router 的结构化输出、Guardrails 的
二元判断、Cypher 写操作硬拦截，构成多层防御。
"""
from __future__ import annotations

import html
from typing import Tuple


def xml_escape(text: str) -> str:
    """转义 < > & ，防止用户伪造 XML 闭合标签。

    >>> xml_escape('</user_message>')
    '&lt;/user_message&gt;'

    注意：只转义 XML 关键字符，不转义引号（不影响 LLM 理解）。
    """
    return html.escape(text, quote=False)


def wrap_user_message(raw_input: str) -> Tuple[str, str]:
    """将用户输入包裹在 <user_message> XML 标签中，建立信任边界。

    Returns:
        (wrapped_message, display_text) — wrapped 用于嵌入 prompt，display 保留原文供记忆存储
    """
    escaped = xml_escape(raw_input)
    wrapped = (
        "<user_message>\n"
        f"{escaped}\n"
        "</user_message>"
    )
    return wrapped, raw_input
