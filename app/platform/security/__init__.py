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

__all__ = ["wrap_user_message"]


def wrap_user_message(raw_input: str) -> tuple[str, str]:
    """将用户输入包裹在 <user_message> XML 标签中，建立信任边界。

    Returns:
        (wrapped_message, display_text) — wrapped 用于嵌入 prompt，display 保留原文供记忆存储
    """
    escaped = html.escape(raw_input, quote=False)
    wrapped = f"<user_message>\n{escaped}\n</user_message>"
    return wrapped, raw_input
