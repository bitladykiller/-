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

from collections.abc import Callable, Sequence
from typing import Any, TypedDict

from langchain_core.messages import AIMessage

from app.shared.security import wrap_user_message


class MessagePayload(TypedDict):
    """节点返回的标准消息负载。"""

    messages: list[AIMessage]


def _message_role(message: Any) -> str:
    """统一读取消息角色，兼容 dict 与 LangChain Message。"""
    if isinstance(message, dict):
        return str(message.get("role", "") or "")
    return str(
        getattr(message, "type", None) or getattr(message, "role", None) or ""
    )


def _message_content(message: Any) -> str:
    """统一读取消息文本内容。"""
    if isinstance(message, dict):
        return str(message.get("content", "") or "")
    return str(getattr(message, "content", "") or "")


def build_safe_messages(
    system_prompt: str,
    messages: Sequence[Any],
) -> list[dict[str, str]]:
    """构建安全消息列表，对 user 消息做 XML 隔离防注入。"""
    safe: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for message in messages:
        role = _message_role(message) or "user"
        content = _message_content(message)
        if role == "user":
            wrapped, _ = wrap_user_message(content)
            safe.append({"role": "user", "content": wrapped})
            continue
        safe.append({"role": role, "content": content})
    return safe


def build_progress_response(
    progress_message: str,
    summary: str,
) -> MessagePayload:
    """统一构造“进度提示 + 最终摘要”的两段式回复。"""
    return {
        "messages": [
            AIMessage(content=progress_message),
            AIMessage(content=summary),
        ]
    }


def build_simple_message_response(message: str) -> MessagePayload:
    """统一构造单条助手消息响应。"""
    return {"messages": [AIMessage(content=message)]}


def _find_last_message_content(
    messages: list[Any],
    *,
    predicate: Callable[[Any], bool],
    skip_progress: bool = False,
) -> str:
    """反向查找最后一条满足条件的消息文本。"""
    for message in reversed(messages):
        if not predicate(message):
            continue
        content = _message_content(message)
        if skip_progress and content and "正在" in content:
            continue
        return content
    return ""


def find_last_user_message(messages: list[Any]) -> str:
    """返回最后一条用户消息内容。"""
    return _find_last_message_content(
        messages,
        predicate=lambda message: _message_role(message) in {"human", "user"},
    )


def find_last_assistant_message(messages: list[Any]) -> str:
    """返回最后一条有效助手消息，优先跳过进度提示。"""
    assistant_message = _find_last_message_content(
        messages,
        predicate=lambda message: _message_role(message) in {"ai", "assistant"},
        skip_progress=True,
    )
    if assistant_message:
        return assistant_message
    return _find_last_message_content(
        messages,
        predicate=lambda message: _message_role(message) in {"ai", "assistant"},
    )


__all__ = [
    "MessagePayload",
    "build_progress_response",
    "build_safe_messages",
    "build_simple_message_response",
    "find_last_assistant_message",
    "find_last_user_message",
]
