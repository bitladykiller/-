"""请求级身份与追踪上下文。

这个模块负责：
- 用 contextvars 承载"当前请求"的 request_id 与已认证 user_id
- 让日志、检索过滤等横切关注点无需层层传参即可取到请求上下文

这个模块不负责：
- 身份的验证（见 app.user.application.auth_service）
- 日志格式（见 logger.py 的 RequestContextFilter）

WHY 用 contextvars 而不是显式传参：
request_id 要出现在**每一条**日志里，user 作用域要下沉到检索过滤——
这两者途经 FastAPI 中间件 → LangGraph 六个节点 → 检索器 → Milvus 存储层。
显式传参意味着给全链路每个函数签名都加一个参数；contextvars 是 asyncio
原生的请求局部存储（`asyncio.create_task` 会自动快照继承），是这类
横切上下文的标准解法。
"""

from __future__ import annotations

from contextvars import ContextVar

_REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="-")
_CURRENT_USER_ID: ContextVar[int | None] = ContextVar("current_user_id", default=None)


def set_request_id(request_id: str) -> None:
    _REQUEST_ID.set(request_id)


def get_request_id() -> str:
    return _REQUEST_ID.get()


def set_current_user_id(user_id: int | None) -> None:
    _CURRENT_USER_ID.set(user_id)


def get_current_user_id() -> int | None:
    """返回当前请求已认证的 user_id；无认证上下文时为 None。"""
    return _CURRENT_USER_ID.get()


__all__ = [
    "get_current_user_id",
    "get_request_id",
    "set_current_user_id",
    "set_request_id",
]
