"""租户服务单测：个人租户创建、归属校验、活跃租户解析、切换。"""

from __future__ import annotations

import pytest
from app.shared.core.errors import ResourceNotFoundError
from app.user.application.tenant_service import (
    DEFAULT_TENANT_ID,
    TenantMembershipView,
    TenantService,
    new_tenant_id,
)


class FakeRow:
    """模拟查询结果行（支持按位置取元素）。"""

    def __init__(self, *values) -> None:
        self._values = tuple(values)

    def __getitem__(self, index: int):
        return self._values[index]

    def __iter__(self):
        return iter(self._values)


class FakeResult:
    """模拟 SQLAlchemy Result。"""

    def __init__(self, *, first_row=None, rows=None, scalar=None) -> None:
        self._first_row = first_row
        self._rows = rows or []
        self._scalar = scalar

    def first(self):
        return self._first_row

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar


class FakeSession:
    """模拟 AsyncSession：记录 execute 语句并返回预制结果。"""

    def __init__(self, results: list) -> None:
        self._results = list(results)
        self.calls: list[str] = []
        self.added: list = []
        self.committed = False

    async def execute(self, statement, params=None):
        self.calls.append(str(statement))
        return self._results.pop(0)

    def add(self, obj) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None


class FakeSessionFactory:
    """按顺序弹出 FakeSession 的会话工厂。"""

    def __init__(self, *sessions: FakeSession) -> None:
        self._sessions = list(sessions)

    def __call__(self):
        if not self._sessions:
            raise AssertionError("会话工厂被多次调用，超出预制会话数量")
        return self._sessions.pop(0)


def test_new_tenant_id_is_unique_and_prefixed() -> None:
    ids = {new_tenant_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(tid.startswith("t_") and len(tid) == 14 for tid in ids)


async def test_create_personal_tenant_adds_tenant_and_owner_membership() -> None:
    session = FakeSession([])
    service = TenantService(session_factory=FakeSessionFactory(session))

    tenant_id = await service.create_personal_tenant(1, "alice")

    assert tenant_id.startswith("t_")
    assert session.committed is True
    added_classes = [type(obj).__name__ for obj in session.added]
    assert "Tenant" in added_classes
    assert "TenantMembership" in added_classes
    tenant = next(obj for obj in session.added if type(obj).__name__ == "Tenant")
    assert tenant.name == "alice 的个人空间"
    assert tenant.status == "active"
    membership = next(obj for obj in session.added if type(obj).__name__ == "TenantMembership")
    assert membership.tenant_id == tenant_id
    assert membership.user_id == 1
    assert membership.role == "owner"


async def test_resolve_active_tenant_returns_earliest_membership() -> None:
    session = FakeSession([FakeResult(first_row=FakeRow("t_a", "active"))])
    service = TenantService(session_factory=FakeSessionFactory(session))

    tenant_id = await service.resolve_active_tenant(7)

    assert tenant_id == "t_a"


async def test_resolve_active_tenant_falls_back_to_default() -> None:
    session = FakeSession([FakeResult(first_row=None)])
    service = TenantService(session_factory=FakeSessionFactory(session))

    tenant_id = await service.resolve_active_tenant(7)

    assert tenant_id == DEFAULT_TENANT_ID


async def test_validate_membership_returns_role() -> None:
    session = FakeSession([FakeResult(scalar="admin")])
    service = TenantService(session_factory=FakeSessionFactory(session))

    role = await service.validate_membership(7, "t_a")

    assert role == "admin"


async def test_validate_membership_returns_none_when_not_member() -> None:
    session = FakeSession([FakeResult(scalar=None)])
    service = TenantService(session_factory=FakeSessionFactory(session))

    role = await service.validate_membership(7, "t_b")

    assert role is None


async def test_list_user_tenants_builds_views() -> None:
    rows = [
        FakeRow("t_a", "租户一", "owner", "active"),
        FakeRow("t_b", "租户二", "member", "active"),
    ]
    session = FakeSession([FakeResult(rows=rows)])
    service = TenantService(session_factory=FakeSessionFactory(session))

    views = await service.list_user_tenants(7)

    assert views == [
        TenantMembershipView("t_a", "租户一", "owner", "active"),
        TenantMembershipView("t_b", "租户二", "member", "active"),
    ]


async def test_switch_tenant_ok(monkeypatch) -> None:
    session = FakeSession([FakeResult(scalar="member")])
    service = TenantService(session_factory=FakeSessionFactory(session))

    tenant_id = await service.switch_tenant(7, "t_b")

    assert tenant_id == "t_b"


async def test_switch_tenant_raises_when_not_member() -> None:
    session = FakeSession([FakeResult(scalar=None)])
    service = TenantService(session_factory=FakeSessionFactory(session))

    with pytest.raises(ResourceNotFoundError):
        await service.switch_tenant(7, "t_b")
