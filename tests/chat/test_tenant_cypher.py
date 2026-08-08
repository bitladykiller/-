"""KG 租户约束注入单测。"""

from __future__ import annotations

import pytest
from app.chat.infrastructure.kg.tenant_cypher import (
    build_tenant_constraint,
    inject_tenant_constraint,
    resolve_kg_tenant_id,
)


def test_inject_adds_where_before_return_when_no_where() -> None:
    stmt = "MATCH (o:Order) RETURN o.OrderId, o.OrderDate"

    injected, ok = inject_tenant_constraint(stmt)

    assert ok is True
    assert injected == (
        "WITH $__tenant_id AS __tenant_boundary\n"
        "MATCH (o:Order) WHERE o.tenant_id = __tenant_boundary\n"
        "RETURN o.OrderId, o.OrderDate"
    )


def test_inject_prepends_condition_to_existing_where() -> None:
    stmt = "MATCH (p:Product) WHERE p.ProductName CONTAINS $name RETURN p"

    injected, ok = inject_tenant_constraint(stmt)

    assert ok is True
    assert "WHERE p.tenant_id = __tenant_boundary AND p.ProductName CONTAINS $name" in injected


def test_inject_covers_all_node_variables_in_relationship_pattern() -> None:
    stmt = (
        "MATCH (p:Product)-[:BELONGS_TO]->(c:Category) "
        "WHERE c.categoryName = $category RETURN p.ProductName"
    )

    injected, ok = inject_tenant_constraint(stmt)

    assert ok is True
    assert "p.tenant_id = __tenant_boundary" in injected
    assert "c.tenant_id = __tenant_boundary" in injected


def test_inject_handles_multiple_match_clauses() -> None:
    stmt = "MATCH (o:Order) MATCH (c:Customer) WHERE o.customerId = c.customerId RETURN o"

    injected, ok = inject_tenant_constraint(stmt)

    assert ok is True
    assert "o.tenant_id = __tenant_boundary" in injected
    assert "c.tenant_id = __tenant_boundary" in injected


def test_inject_appends_where_when_no_return() -> None:
    stmt = "MATCH (n:Product)"

    injected, ok = inject_tenant_constraint(stmt)

    assert ok is True
    assert injected.endswith("WHERE n.tenant_id = __tenant_boundary")


def test_inject_ignores_statements_without_node_variables() -> None:
    for stmt in ("RETURN 1", "MATCH () RETURN count(*)", ""):
        injected, ok = inject_tenant_constraint(stmt)
        assert ok is False
        assert injected == stmt


def test_inject_ignores_anonymous_nodes() -> None:
    stmt = "MATCH ()-[r:CONNECTED]->() RETURN r"

    injected, ok = inject_tenant_constraint(stmt)

    # 匿名节点无法绑定 tenant 条件，且无变量可注入
    assert ok is False
    assert injected == stmt


def test_build_tenant_constraint_joins_variables() -> None:
    constraint = build_tenant_constraint(["a", "b"])

    assert constraint == ("a.tenant_id = __tenant_boundary AND b.tenant_id = __tenant_boundary")


def test_inject_never_inlines_tenant_value() -> None:
    """租户 ID 必须走参数绑定，绝不拼成字面量（防注入）。"""
    stmt = "MATCH (o:Order) RETURN o"

    injected, ok = inject_tenant_constraint(stmt)

    assert ok is True
    assert "$__tenant_id" in injected
    assert "__tenant_boundary" in injected
    assert "tenant_id == " not in injected


def test_resolve_kg_tenant_id_reads_contextvars() -> None:
    from app.shared.core.identity import set_current_tenant_id

    set_current_tenant_id("t_kg_1")
    try:
        assert resolve_kg_tenant_id() == "t_kg_1"
    finally:
        set_current_tenant_id("default")
    assert resolve_kg_tenant_id() == "default"


def test_inject_preserves_order_by_and_limit() -> None:
    stmt = "MATCH (o:Order) RETURN o.orderId ORDER BY o.OrderDate DESC LIMIT 10"

    injected, ok = inject_tenant_constraint(stmt)

    assert ok is True
    assert "WHERE o.tenant_id = __tenant_boundary" in injected
    assert "ORDER BY o.OrderDate DESC LIMIT 10" in injected


@pytest.mark.parametrize(
    "stmt",
    [
        "MATCH (p:Product) WHERE p.UnitPrice > 100 RETURN p",
        "MATCH (p:Product) RETURN p.UnitPrice",
    ],
)
def test_inject_roundtrip_idempotent_shape(stmt: str) -> None:
    """同一语句注入后仍保留原语义结构（WHERE/返回内容不被破坏）。"""
    injected, ok = inject_tenant_constraint(stmt)
    assert ok is True
    for token in ("MATCH", "RETURN", "tenant_id"):
        assert token in injected
