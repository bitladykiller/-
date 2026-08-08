"""认证服务 — 注册 / 登录 / 令牌签发与校验。

这个模块负责：
- bcrypt 密码哈希与校验（passlib）
- JWT 访问令牌的签发与验证（HS256，密钥来自 settings.SECRET_KEY）
- 注册与登录的业务规则

这个模块不负责：
- HTTP 协议细节（401/表单解析在 app.api.auth / app.api.deps）
- 会话与画像业务

WHY 现在才有这一层：
users 表从第一天就有 password_hash 列，但 API 一直让客户端**自报 user_id**
（前端 localStorage 一个数字），服务端全盘信任——归属校验只能防误操作，
防不了冒充。身份必须由服务端从凭据推导，这是其它一切访问控制的前提。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import jwt
from app.shared.core.config import settings
from app.shared.core.errors import ResourceNotFoundError
from app.shared.core.logger import get_logger
from passlib.context import CryptContext

logger = get_logger(__name__)

_JWT_ALGORITHM = "HS256"
# bcrypt：自带盐值与成本因子，密码哈希的默认正解
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_USERNAME_MIN = 2
_USERNAME_MAX = 50
_PASSWORD_MIN = 6


class AuthError(Exception):
    """认证失败（凭据错误 / 令牌无效）。API 层映射 401。"""

    def __init__(self, message: str = "用户名或密码错误") -> None:
        super().__init__(message)
        self.message = message


class RegistrationError(Exception):
    """注册被拒绝（用户名占用 / 参数不合规）。API 层映射 400。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class AuthenticatedUser:
    """通过令牌验证后的请求方身份。

    tenant_id 是令牌声明的"活跃租户"，即本请求的数据隔离边界。
    API 层（deps.get_current_user）会再经 tenant_memberships 校验该声明，
    任何业务代码不得信任客户端自报的租户。
    """

    id: int
    username: str
    tenant_id: str = "default"


# ---------------------------------------------------------------------- #
# 纯函数：哈希与令牌
# ---------------------------------------------------------------------- #


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _pwd_context.verify(plain, hashed)
    except Exception:  # 库内格式异常按验证失败处理，不向上暴露细节
        return False


def issue_access_token(
    user_id: int,
    username: str,
    tenant_id: str = "default",
    *,
    now: int | None = None,
    ttl_seconds: int | None = None,
) -> str:
    """签发 JWT 访问令牌。

    Args:
        user_id: 用户 ID（sub）。
        username: 用户名。
        tenant_id: 活跃租户 ID——SaaS 数据隔离边界，必须与
            tenant_memberships 中的有效成员关系一致才能通过请求鉴权。
    """
    issued_at = now if now is not None else int(time.time())
    ttl = ttl_seconds if ttl_seconds is not None else settings.ACCESS_TOKEN_TTL_SECONDS
    payload = {
        "sub": str(user_id),
        "username": username,
        "tenant_id": tenant_id,
        "iat": issued_at,
        "exp": issued_at + ttl,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=_JWT_ALGORITHM)


def verify_access_token(token: str) -> AuthenticatedUser:
    """验证令牌并还原身份。

    Raises:
        AuthError: 过期、签名不符或载荷缺失。
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[_JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("登录已过期，请重新登录") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("无效的访问令牌") from exc

    sub = payload.get("sub")
    username = payload.get("username")
    if not (isinstance(sub, str) and sub.isdigit() and isinstance(username, str)):
        raise AuthError("令牌载荷缺失")
    tenant_id = payload.get("tenant_id")
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        tenant_id = "default"
    return AuthenticatedUser(id=int(sub), username=username, tenant_id=tenant_id)


def validate_registration(username: str, password: str) -> None:
    """注册参数校验；不合规抛 RegistrationError。"""
    name = (username or "").strip()
    if not (_USERNAME_MIN <= len(name) <= _USERNAME_MAX):
        raise RegistrationError(f"用户名长度须在 {_USERNAME_MIN}-{_USERNAME_MAX} 之间")
    if len(password or "") < _PASSWORD_MIN:
        raise RegistrationError(f"密码至少 {_PASSWORD_MIN} 位")


# ---------------------------------------------------------------------- #
# 服务：数据库交互
# ---------------------------------------------------------------------- #


class AuthService:
    """注册 / 登录服务。"""

    def __init__(self, session_factory=None) -> None:
        if session_factory is None:
            from app.shared.core.database import AsyncSessionLocal

            session_factory = AsyncSessionLocal
        self._session_factory = session_factory

    async def register(self, username: str, password: str) -> AuthenticatedUser:
        """注册新用户；用户名占用抛 RegistrationError。"""
        from app.user.infrastructure.models.user import User
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError

        validate_registration(username, password)
        name = username.strip()
        async with self._session_factory() as db:
            existing = await db.execute(select(User).where(User.username == name))
            if existing.scalar_one_or_none() is not None:
                raise RegistrationError("用户名已被占用")

            user = User(
                username=name,
                # email 列 NOT NULL UNIQUE；未开放邮箱注册前用占位保证唯一
                email=f"{name}@local.invalid",
                password_hash=hash_password(password),
            )
            db.add(user)
            try:
                await db.commit()
            except IntegrityError as exc:
                # 并发竞态兜底：两个同名注册同时穿过上面的查重后，
                # 后提交者会撞唯一键。真正的裁判是数据库约束，
                # 查重只是提前给出友好文案的快捷路径。
                await db.rollback()
                raise RegistrationError("用户名已被占用") from exc
            await db.refresh(user)
            logger.info("新用户注册 | user_id=%s username=%s", user.id, name)
            return AuthenticatedUser(id=user.id, username=user.username)

    async def authenticate(self, username: str, password: str) -> AuthenticatedUser:
        """校验用户名密码；失败抛 AuthError（不区分"用户不存在/密码错"）。"""
        from app.user.infrastructure.models.user import User
        from sqlalchemy import select

        async with self._session_factory() as db:
            result = await db.execute(select(User).where(User.username == (username or "").strip()))
            user = result.scalar_one_or_none()
            # 统一失败文案：区分"用户不存在"会泄露注册状态
            if user is None or not verify_password(password, user.password_hash):
                raise AuthError()

            user.last_login = datetime.now()
            await db.commit()
            return AuthenticatedUser(id=user.id, username=user.username)

    async def get_user(self, user_id: int) -> AuthenticatedUser:
        """按 id 取用户（令牌校验后的存在性确认场景）。"""
        from app.user.infrastructure.models.user import User
        from sqlalchemy import select

        async with self._session_factory() as db:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if user is None:
                raise ResourceNotFoundError("用户不存在")
            return AuthenticatedUser(id=user.id, username=user.username)


auth_service = AuthService()

__all__ = [
    "AuthenticatedUser",
    "AuthError",
    "AuthService",
    "RegistrationError",
    "auth_service",
    "hash_password",
    "issue_access_token",
    "validate_registration",
    "verify_access_token",
    "verify_password",
]
