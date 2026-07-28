"""LangGraph 主图组装入口。

职责：
- 只负责注册节点和连接边
- 把主图结构与节点实现、模型创建、记忆上下文分层隔离
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any, cast

from app.chat.infrastructure.graph.decision_nodes import (
    guardrails_edge,
    guardrails_node,
    respond_to_general_query,
    route_and_plan_query,
    routing_decision_edge,
)
from app.chat.infrastructure.graph.lifecycle_nodes import after_response
from app.chat.infrastructure.graph.retrieval_nodes import (
    execute_graph_only,
    execute_parallel,
    execute_rag_only,
    execute_then,
)
from app.chat.infrastructure.graph.state import AgentState, InputState
from app.chat.infrastructure.graph.timing import timed_node
from app.chat.infrastructure.react.react import execute_react
from langgraph.graph import END, START, StateGraph

# ====================================================================
# 节点注册表
# ====================================================================
# 定义主图的所有节点，包括：
# - 统一路由与检索规划节点（route_and_plan_query）
# - 通用回复节点（respond_to_general_query）
# - Guardrails 安全检查节点（guardrails_node）
# - 各种执行节点（KG/RAG/并行/ReAct）
# - 响应后处理节点（after_response）
_NODE_REGISTRATIONS = (
    route_and_plan_query,
    respond_to_general_query,
    ("guardrails_node", guardrails_node),
    ("execute_graph_only", execute_graph_only),
    ("execute_rag_only", execute_rag_only),
    ("execute_parallel", execute_parallel),
    ("execute_then", execute_then),
    ("execute_react", execute_react),
    ("after_response", after_response),
)

# ====================================================================
# 边路由映射
# ====================================================================
# 定义主图的条件边路由：
# 1. 统一路由节点 → 通用回复或 Guardrails
# 2. Guardrails 节点 → 已解析执行策略或结束
# path 键来自 routing_decision_edge / guardrails_edge，右侧是图上真实节点名
_ROUTING_DECISION_EDGE_MAP = {
    "respond_to_general_query": "respond_to_general_query",
    "guardrails_node": "guardrails_node",
}
_EXECUTION_PLAN_EDGE_MAP = {
    "execute_graph_only": "execute_graph_only",
    "execute_rag_only": "execute_rag_only",
    "execute_parallel": "execute_parallel",
    "execute_then": "execute_then",
    "execute_react": "execute_react",
}
_GUARDRAILS_EDGE_MAP = {
    "after_response": "after_response",
    **_EXECUTION_PLAN_EDGE_MAP,
}
# 所有执行节点的名称，用于统一连接到 after_response
_EXECUTION_NODE_NAMES = tuple(_EXECUTION_PLAN_EDGE_MAP.values())

# 编译后的主图实例，供外部使用
_graph_builder = StateGraph(AgentState, input=InputState)
for registration in _NODE_REGISTRATIONS:
    if isinstance(registration, tuple):
        node_name, node_handler = registration
    else:
        # 各节点返回类型不同，联合类型对逐项迭代无意义，统一放宽为 Any
        node_name, node_handler = registration.__name__, cast(Any, registration)
    # 每个节点包一层耗时打点：一次请求哪个环节慢，日志直接可读
    _graph_builder.add_node(node_name, timed_node(node_name, node_handler))

# 起始边：START → 统一路由与检索规划节点
_graph_builder.add_edge(START, "route_and_plan_query")

# 条件边：统一路由 → 通用回复 或 Guardrails
# cast 用于兼容 langgraph 对 path_map 的 Hashable 键类型约束
_graph_builder.add_conditional_edges(
    "route_and_plan_query",
    routing_decision_edge,
    cast(dict[Hashable, str], _ROUTING_DECISION_EDGE_MAP),
)

# 固定边：通用回复 → 响应后处理
_graph_builder.add_edge("respond_to_general_query", "after_response")

# 条件边：Guardrails → 已解析执行计划 或 结束
_graph_builder.add_conditional_edges(
    "guardrails_node",
    guardrails_edge,
    cast(dict[Hashable, str], _GUARDRAILS_EDGE_MAP),
)

# 固定边：所有执行节点 → 响应后处理
for node_name in _EXECUTION_NODE_NAMES:
    _graph_builder.add_edge(node_name, "after_response")

# 结束边：响应后处理 → END
_graph_builder.add_edge("after_response", END)

graph = _graph_builder.compile()

__all__ = ["graph"]
