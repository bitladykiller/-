"""请求级身份与追踪上下文。

这个模块负责：
- 用 contextvars 承载"当前请求"的 request_id、已认证 user_id、tenant_id 与角色
- 让日志、检索过滤等横切关注点无需层层传参即可取到请求上下文

这个模块不负责：
- 身份的验证（见 app.user.application.auth_service / tenant_service）
- 日志格式（见 logger.py 的 RequestContextFilter）

WHY 用 contextvars 而不是显式传参：
request_id 要出现在**每一条**日志里，user/tenant 作用域要下沉到检索过滤——
这两者途经 FastAPI 中间件 → LangGraph 六个节点 → 检索器 → Milvus 存储层。
显式传参意味着给全链路每个函数签名都加一个参数；contextvars 是 asyncio
原生的请求局部存储（`asyncio.create_task` 会自动快照继承），是这类
横切上下文的标准解法。

⚠️ 同步请求链（HTTP → Agent → 存储）内 ContextVar 是可信的；
跨进程（事件 worker）ContextVar 无法传播，必须由事件 payload 显式携带
tenant_id，再在消费者里恢复（见 app.platform.events）。
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

_REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="-")
_CURRENT_USER_ID: ContextVar[int | None] = ContextVar("current_user_id", default=None)
_CURRENT_TENANT_ID: ContextVar[str] = ContextVar(
    "current_tenant_id",
    default="default",
)
_CURRENT_ROLE: ContextVar[str] = ContextVar("current_role", default="")


@dataclass(frozen=True)
class TenantContext:
    """请求生命周期内唯一可信的租户安全上下文。

    由 deps.get_current_user 在"JWT 校验 + membership 校验"通过后建立，
    全链路（API → Service → Repository → Retriever → Event Publisher）
    只应使用这里的 tenant_id / user_id / role，不再信任请求参数。
    """

    tenant_id: str
    user_id: int
    role: str = ""


def set_request_id(request_id: str) -> None:
    _REQUEST_ID.set(request_id)


def get_request_id() -> str:
    return _REQUEST_ID.get()


def set_current_user_id(user_id: int | None) -> None:
    _CURRENT_USER_ID.set(user_id)


def get_current_user_id() -> int | None:
    """返回当前请求已认证的 user_id；无认证上下文时为 None。"""
    return _CURRENT_USER_ID.get()


def set_current_tenant_id(tenant_id: str) -> None:
    _CURRENT_TENANT_ID.set(tenant_id or "default")


def get_current_tenant_id() -> str:
    """返回当前请求的租户边界；未显式设置时回落 default。"""
    return _CURRENT_TENANT_ID.get()


def set_current_role(role: str) -> None:
    _CURRENT_ROLE.set(role)


def get_current_role() -> str:
    """返回当前请求在租户内的角色；无认证上下文时为空串。"""
    return _CURRENT_ROLE.get()


def set_tenant_context(ctx: TenantContext) -> None:
    """把已验证的 TenantContext 写入 contextvars。"""
    set_current_tenant_id(ctx.tenant_id)
    set_current_user_id(ctx.user_id)
    set_current_role(ctx.role)


def get_tenant_context() -> TenantContext | None:
    """读取当前请求的 TenantContext；未认证上下文时返回 None。"""
    user_id = get_current_user_id()
    if user_id is None:
        return None
    return TenantContext(
        tenant_id=get_current_tenant_id(),
        user_id=user_id,
        role=get_current_role(),
    )


__all__ = [
    "TenantContext",
    "get_current_role",
    "get_current_tenant_id",
    "get_current_user_id",
    "get_request_id",
    "get_tenant_context",
    "set_current_role",
    "set_current_tenant_id",
    "set_current_user_id",
    "set_request_id",
    "set_tenant_context",
]
