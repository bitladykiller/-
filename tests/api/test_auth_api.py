"""鉴权依赖与认证端点单测（含多租户身份模型）。"""

from __future__ import annotations

import pytest
from app.api import auth as auth_api
from app.api.deps import get_current_user
from app.shared.core.identity import (
    get_current_role,
    get_current_tenant_id,
    get_current_user_id,
    set_current_tenant_id,
    set_current_user_id,
)
from app.user.application.auth_service import (
    AuthenticatedUser,
    AuthError,
    RegistrationError,
    issue_access_token,
    verify_access_token,
)
from app.user.application.tenant_service import tenant_service
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials


def _bearer(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


async def test_get_current_user_rejects_missing_token() -> None:
    with pytest.raises(HTTPException) as exc:
        await get_current_user(None)

    assert exc.value.status_code == 401
    assert "登录" in str(exc.value.detail)


async def test_get_current_user_rejects_invalid_token() -> None:
    with pytest.raises(HTTPException) as exc:
        await get_current_user(_bearer("not-a-jwt"))

    assert exc.value.status_code == 401


async def test_get_current_user_rejects_membership_mismatch(monkeypatch) -> None:
    async def no_membership(user_id: int, tenant_id: str) -> str | None:
        return None

    monkeypatch.setattr(tenant_service, "validate_membership", no_membership)
    token = issue_access_token(42, "alice", tenant_id="company_a")

    with pytest.raises(HTTPException) as exc:
        await get_current_user(_bearer(token))

    assert exc.value.status_code == 401
    assert "租户" in str(exc.value.detail)


async def test_get_current_user_accepts_valid_token_and_sets_context(monkeypatch) -> None:
    async def ok_membership(user_id: int, tenant_id: str) -> str | None:
        return "admin"

    monkeypatch.setattr(tenant_service, "validate_membership", ok_membership)
    set_current_user_id(None)
    set_current_tenant_id("default")
    token = issue_access_token(42, "alice", tenant_id="company_a")

    user = await get_current_user(_bearer(token))

    assert user == AuthenticatedUser(id=42, username="alice", tenant_id="company_a")
    # 横切层（日志/检索分域）从 contextvars 取身份与租户边界
    assert get_current_user_id() == 42
    assert get_current_tenant_id() == "company_a"
    assert get_current_role() == "admin"
    set_current_user_id(None)
    set_current_tenant_id("default")


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
        return AuthenticatedUser(id=7, username="bob", tenant_id="company_a")

    async def resolve_tenant(user_id: int) -> str:
        return "company_a"

    monkeypatch.setattr(auth_api.auth_service, "authenticate", ok_auth)
    monkeypatch.setattr(auth_api.tenant_service, "resolve_active_tenant", resolve_tenant)

    payload = await auth_api.login(auth_api.CredentialsRequest(username="bob", password="pw123456"))

    assert payload["user_id"] == 7
    assert payload["tenant_id"] == "company_a"
    verified = verify_access_token(payload["access_token"])
    assert verified.username == "bob"
    assert verified.tenant_id == "company_a"


async def test_register_creates_personal_tenant_and_issues_token(monkeypatch) -> None:
    async def ok_register(username: str, password: str):
        return AuthenticatedUser(id=9, username="carol")

    async def create_tenant(user_id: int, username: str) -> str:
        assert user_id == 9
        return "t_created"

    monkeypatch.setattr(auth_api.auth_service, "register", ok_register)
    monkeypatch.setattr(auth_api.tenant_service, "create_personal_tenant", create_tenant)

    payload = await auth_api.register(
        auth_api.CredentialsRequest(username="carol", password="pw123456")
    )

    assert payload["tenant_id"] == "t_created"
    assert verify_access_token(payload["access_token"]).tenant_id == "t_created"


async def test_switch_tenant_issues_token_with_target_tenant(monkeypatch) -> None:
    async def switch_ok(user_id: int, tenant_id: str) -> str:
        return tenant_id

    monkeypatch.setattr(auth_api.tenant_service, "switch_tenant", switch_ok)

    payload = await auth_api.switch_tenant(
        auth_api.SwitchTenantRequest(tenant_id="company_b"),
        AuthenticatedUser(id=7, username="bob", tenant_id="company_a"),
    )

    assert payload["tenant_id"] == "company_b"
    assert verify_access_token(payload["access_token"]).tenant_id == "company_b"


async def test_list_tenants_returns_memberships(monkeypatch) -> None:
    from app.user.application.tenant_service import TenantMembershipView

    async def list_ok(user_id: int):
        return [
            TenantMembershipView("t1", "租户一", "owner", "active"),
            TenantMembershipView("t2", "租户二", "member", "active"),
        ]

    monkeypatch.setattr(auth_api.tenant_service, "list_user_tenants", list_ok)

    items = await auth_api.list_tenants(AuthenticatedUser(id=7, username="bob", tenant_id="t1"))

    assert items == [
        {"tenant_id": "t1", "tenant_name": "租户一", "role": "owner", "status": "active"},
        {"tenant_id": "t2", "tenant_name": "租户二", "role": "member", "status": "active"},
    ]
