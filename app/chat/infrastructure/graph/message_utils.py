"""LangGraph 消息辅助函数。

职责：
- 统一兼容 dict / LangChain Message 两类消息输入
- 负责 user 消息安全包装与标准消息列表构造
- 负责常见的节点回复样板和最后消息提取

边界：
- 不负责节点路由和检索执行
- 不负责记忆上下文拼装
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage

from app.shared.security import wrap_user_message


def _message_role(message: Any) -> str:
    """统一读取消息角色，兼容 dict 与 LangChain Message。"""
    if isinstance(message, dict):
        return str(message.get("role", "") or "")
    return str(
        getattr(message, "type", None) or getattr(message, "role", None) or ""
    )


def build_safe_messages(
    system_prompt: str,
    messages: Sequence[Any],
) -> list[dict[str, str]]:
    """构建安全消息列表，对 user 消息做 XML 隔离防注入。"""
    safe: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for message in messages:
        role = _message_role(message) or "user"
        if isinstance(message, dict):
            content = str(message.get("content", "") or "")
        else:
            content = str(getattr(message, "content", "") or "")
        if role == "user":
            wrapped, _ = wrap_user_message(content)
            safe.append({"role": "user", "content": wrapped})
            continue
        safe.append({"role": role, "content": content})
    return safe


def find_last_user_message(messages: list[Any]) -> str:
    """返回最后一条用户消息内容。"""
    for message in reversed(messages):
        if _message_role(message) not in {"human", "user"}:
            continue
        if isinstance(message, dict):
            return str(message.get("content", "") or "")
        return str(getattr(message, "content", "") or "")
    return ""


def find_last_assistant_message(messages: list[Any]) -> str:
    """返回最后一条有效助手消息，优先跳过进度提示。"""
    fallback = ""
    for message in reversed(messages):
        if _message_role(message) not in {"ai", "assistant"}:
            continue
        if isinstance(message, dict):
            content = str(message.get("content", "") or "")
        else:
            content = str(getattr(message, "content", "") or "")
        if not fallback:
            fallback = content
        if content and "正在" in content:
            continue
        return content
    return fallback


__all__ = [
    "build_safe_messages",
    "find_last_assistant_message",
    "find_last_user_message",
]
