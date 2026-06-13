"""LangGraph 主图组装入口。

职责：
- 只负责注册节点和连接边
- 把主图结构与节点实现、模型创建、记忆上下文分层隔离
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.lg_agent.graph.state import AgentState, InputState
from app.lg_agent.lg_nodes import (
    after_response,
    analyze_and_route_query,
    execute_graph_only,
    execute_parallel,
    execute_rag_only,
    execute_then,
    guardrails_edge,
    guardrails_node,
    respond_to_general_query,
    retrieval_plan_edge,
    retrieval_plan_route,
    route_query,
)
from app.lg_agent.lg_react import execute_react

_NODE_REGISTRATIONS = (
    analyze_and_route_query,
    respond_to_general_query,
    ("guardrails_node", guardrails_node),
    ("retrieval_plan_route", retrieval_plan_route),
    ("execute_graph_only", execute_graph_only),
    ("execute_rag_only", execute_rag_only),
    ("execute_parallel", execute_parallel),
    ("execute_then", execute_then),
    ("execute_react", execute_react),
    ("after_response", after_response),
)
_ROUTER_EDGE_MAP = {
    "respond_to_general_query": "respond_to_general_query",
    "retrieval_plan_router": "guardrails_node",
}
_GUARDRAILS_EDGE_MAP = {
    "retrieval_plan_route": "retrieval_plan_route",
    "after_response": "after_response",
}
_RETRIEVAL_PLAN_EDGE_MAP = {
    "execute_graph_only": "execute_graph_only",
    "execute_rag_only": "execute_rag_only",
    "execute_parallel": "execute_parallel",
    "execute_then": "execute_then",
    "execute_react": "execute_react",
}
_EXECUTION_NODE_NAMES = tuple(_RETRIEVAL_PLAN_EDGE_MAP.values())


def _register_nodes(builder: StateGraph) -> None:
    """统一注册主图节点。"""
    for registration in _NODE_REGISTRATIONS:
        if isinstance(registration, tuple):
            node_name, node_handler = registration
            builder.add_node(node_name, node_handler)
            continue
        builder.add_node(registration)


def _register_edges(builder: StateGraph) -> None:
    """统一注册主图的固定边和条件边。"""
    builder.add_edge(START, "analyze_and_route_query")
    builder.add_conditional_edges(
        "analyze_and_route_query",
        route_query,
        _ROUTER_EDGE_MAP,
    )
    builder.add_edge("respond_to_general_query", "after_response")
    builder.add_conditional_edges(
        "guardrails_node",
        guardrails_edge,
        _GUARDRAILS_EDGE_MAP,
    )
    builder.add_conditional_edges(
        "retrieval_plan_route",
        retrieval_plan_edge,
        _RETRIEVAL_PLAN_EDGE_MAP,
    )
    for node_name in _EXECUTION_NODE_NAMES:
        builder.add_edge(node_name, "after_response")
    builder.add_edge("after_response", END)


def _build_graph():
    """构造并编译 LangGraph 主图。"""
    builder = StateGraph(AgentState, input_schema=InputState)
    _register_nodes(builder)
    _register_edges(builder)
    return builder.compile()


graph = _build_graph()

__all__ = ["graph"]
