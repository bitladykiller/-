"""KG 租户约束：确定性 Cypher 注入，不依赖 LLM 自觉。

SaaS 多租户原则：**tenant 约束属于 validation / execution policy**，
不属于 prompt 最佳实践——LLM 某一次漏写 WHERE 条件就是跨租户数据泄漏。

本模块在唯一的 Cypher 执行闸口（text2cypher_workflow 的两处 graph.query）
对语句做确定性改写：

    改写前：
        MATCH (p:Product) WHERE p.ProductName CONTAINS $product_name
        RETURN p.ProductName, p.UnitPrice

    改写后：
        WITH $__tenant_id AS __tenant_boundary
        MATCH (p:Product)
        WHERE p.tenant_id = __tenant_boundary
          AND p.ProductName CONTAINS $product_name
        RETURN p.ProductName, p.UnitPrice

实现要点：
- 提取每个 MATCH 子句里全部带变量的节点（`(v:Label)` / `(v)`），
  每个节点都补租户条件，而不是只补第一个
- 已存在 WHERE 时把条件插进 WHERE 开头；否则在首个 RETURN 前插入
  WHERE；没有 RETURN 时追加到语句末尾
- 用参数占位符 `$__tenant_id` 绑定租户 ID，绝不把租户字面量拼进语句
- 提取不到任何变量（如 `RETURN 1` 健康检查）时原样返回

⚠️ 依赖数据前提：图上所有业务节点带 `tenant_id` 属性。存量数据
需执行 `scripts/neo4j-import.sh`（已内置打标）或一次性 `SET` 迁移。
"""

from __future__ import annotations

import re

_TENANT_PARAM = "__tenant_id"
_BOUNDARY_VAR = "__tenant_boundary"

# 每个 MATCH 子句（以 MATCH 开头，止于下一个子句关键字）。
# 关键字的负向前断言排除"节点标签/属性名"里的同名字段（如 (o:Order)、
# o.orderId）——否则 "MATCH (o:Order)" 会被 `\bORDER\b` 误截断。
_CLAUSE_KEYWORD = r"(?<![\w:])\b(?:MATCH|WITH|RETURN|ORDER|LIMIT|WHERE|UNWIND|CALL)\b"
_MATCH_CLAUSE = re.compile(
    r"MATCH\s+(.*?)(?=" + _CLAUSE_KEYWORD + r"|\Z)",
    re.IGNORECASE | re.DOTALL,
)
# 节点变量：`(var)` 或 `(var:Label)`；无变量的匿名节点不收集
_NODE_VAR = re.compile(r"\(\s*(\w+)(?:\s*:\w+)?\s*\)")

_WHERE_RE = re.compile(r"(?<![\w:])\bWHERE\b", re.IGNORECASE)
_RETURN_RE = re.compile(r"(?<![\w:])\bRETURN\b", re.IGNORECASE)


def _collect_match_variables(statement: str) -> list[str]:
    """按 MATCH 子句顺序收集全部带变量的节点名（去重保序）。"""
    variables: list[str] = []
    seen: set[str] = set()
    for clause_match in _MATCH_CLAUSE.finditer(statement):
        clause = clause_match.group(1)
        for node_match in _NODE_VAR.finditer(clause):
            var = node_match.group(1)
            if var not in seen:
                seen.add(var)
                variables.append(var)
    return variables


def build_tenant_constraint(variables: list[str]) -> str:
    """构造租户条件子句：`v1.tenant_id = __tenant_boundary AND ...`。"""
    return " AND ".join(f"{var}.tenant_id = {_BOUNDARY_VAR}" for var in variables)


def inject_tenant_constraint(statement: str) -> tuple[str, bool]:
    """对 Cypher 语句确定性注入租户约束。

    Returns:
        (改写后的语句, 是否成功注入了租户条件)。提取不到节点变量时
        原样返回并置 False——调用方应对此记录告警（可能是非数据查询）。
    """
    stmt = (statement or "").strip()
    if not stmt:
        return stmt, False

    variables = _collect_match_variables(stmt)
    if not variables:
        return stmt, False

    constraint = build_tenant_constraint(variables)

    where_match = _WHERE_RE.search(stmt)
    if where_match is not None:
        # 已有 WHERE：把租户条件作为第一个 AND 条件插入
        inserted = stmt[: where_match.end()] + f" {constraint} AND" + stmt[where_match.end() :]
    else:
        return_match = _RETURN_RE.search(stmt)
        if return_match is not None:
            inserted = (
                stmt[: return_match.start()]
                + f"WHERE {constraint}\n"
                + stmt[return_match.start() :]
            )
        else:
            inserted = f"{stmt}\nWHERE {constraint}"

    return (
        f"WITH ${_TENANT_PARAM} AS {_BOUNDARY_VAR}\n{inserted}",
        True,
    )


def resolve_kg_tenant_id(fallback: str = "default") -> str:
    """解析 KG 查询的租户边界。

    同步请求链：contextvars 已由 deps.get_current_user 写入可信租户。
    脚本/评测等无认证上下文：回落默认租户。
    """
    from app.shared.core.identity import get_current_tenant_id

    return get_current_tenant_id() or fallback


__all__ = [
    "build_tenant_constraint",
    "inject_tenant_constraint",
    "resolve_kg_tenant_id",
]
