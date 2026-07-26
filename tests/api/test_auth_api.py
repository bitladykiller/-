"""鉴权依赖与认证端点单测。"""

from __future__ import annotations

import pytest
from app.api import auth as auth_api
from app.api.deps import get_current_user
from app.shared.core.identity import get_current_user_id, set_current_user_id
from app.user.application.auth_service import (
    AuthenticatedUser,
    AuthError,
    RegistrationError,
    issue_access_token,
)
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_get_current_user_rejects_missing_token() -> None:
    with pytest.raises(HTTPException) as exc:
        get_current_user(None)

    assert exc.value.status_code == 401
    assert "登录" in str(exc.value.detail)


def test_get_current_user_rejects_invalid_token() -> None:
    with pytest.raises(HTTPException) as exc:
        get_current_user(_bearer("not-a-jwt"))

    assert exc.value.status_code == 401


def test_get_current_user_accepts_valid_token_and_sets_context() -> None:
    set_current_user_id(None)
    token = issue_access_token(42, "alice")

    user = get_current_user(_bearer(token))

    assert user == AuthenticatedUser(id=42, username="alice")
    # 横切层（日志/检索分域）从 contextvars 取身份
    assert get_current_user_id() == 42
    set_current_user_id(None)


async def test_register_endpoint_maps_registration_error_to_400(monkeypatch) -> None:
    async def broken_register(username: str, password: str):
        raise RegistrationError("用户名已被占用")

    monkeypatch.setattr(auth_api.auth_service, "register", broken_register)

    with pytest.raises(HTTPException) as exc:
        await auth_api.register(auth_api.CredentialsRequest(username="a", password="b"))

    assert exc.value.status_code == 400


async def test_login_endpoint_maps_auth_error_to_401(monkeypatch) -> None:
    async def broken_auth(username: str, password: str):
        raise AuthError()

    monkeypatch.setattr(auth_api.auth_service, "authenticate", broken_auth)

    with pytest.raises(HTTPException) as exc:
        await auth_api.login(auth_api.CredentialsRequest(username="a", password="b"))

    assert exc.value.status_code == 401


async def test_login_returns_verifiable_token(monkeypatch) -> None:
    async def ok_auth(username: str, password: str):
        return AuthenticatedUser(id=7, username="bob")

    monkeypatch.setattr(auth_api.auth_service, "authenticate", ok_auth)

    payload = await auth_api.login(
        auth_api.CredentialsRequest(username="bob", password="pw123456")
    )

    from app.user.application.auth_service import verify_access_token

    assert payload["user_id"] == 7
    assert verify_access_token(payload["access_token"]).username == "bob"
