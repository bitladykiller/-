"""LangGraph 主图组装入口。

职责：
- 只负责注册节点和连接边
- 把主图结构与节点实现、模型创建、记忆上下文分层隔离
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.chat.infrastructure.graph.state import AgentState, InputState
from app.chat.infrastructure.graph.decision_nodes import (
    analyze_and_route_query,
    guardrails_edge,
    guardrails_node,
    respond_to_general_query,
    retrieval_plan_edge,
    retrieval_plan_route,
    route_query,
)
from app.chat.infrastructure.graph.lifecycle_nodes import after_response
from app.chat.infrastructure.graph.retrieval_nodes import (
    execute_graph_only,
    execute_parallel,
    execute_rag_only,
    execute_then,
)
from app.chat.infrastructure.react.react import execute_react

# ====================================================================
# 节点注册表
# ====================================================================
# 定义主图的所有节点，包括：
# - 分析路由节点（analyze_and_route_query）
# - 通用回复节点（respond_to_general_query）
# - Guardrails 安全检查节点（guardrails_node）
# - 检索计划节点（retrieval_plan_route）
# - 各种执行节点（KG/RAG/并行/ReAct）
# - 响应后处理节点（after_response）
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

# ====================================================================
# 边路由映射
# ====================================================================
# 定义主图的条件边路由：
# 1. 分析路由节点 → 通用回复或 Guardrails
# 2. Guardrails 节点 → 检索计划或结束
# 3. 检索计划节点 → 各种执行策略
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
# 所有执行节点的名称，用于统一连接到 after_response
_EXECUTION_NODE_NAMES = tuple(_RETRIEVAL_PLAN_EDGE_MAP.values())


def _register_nodes(builder: StateGraph) -> None:
    """统一注册主图节点。
    
    遍历 _NODE_REGISTRATIONS，将每个节点添加到 StateGraph。
    支持两种注册方式：
    - 直接注册：函数名作为节点名
    - 元组注册：(自定义名称, 函数)
    """
    for registration in _NODE_REGISTRATIONS:
        if isinstance(registration, tuple):
            node_name, node_handler = registration
            builder.add_node(node_name, node_handler)
            continue
        builder.add_node(registration)


def _register_edges(builder: StateGraph) -> None:
    """统一注册主图的固定边和条件边。
    
    主图执行流程：
    START → analyze_and_route_query → [route_query] → 
        ├─ respond_to_general_query → after_response → END
        └─ guardrails_node → [guardrails_edge] →
            ├─ retrieval_plan_route → [retrieval_plan_edge] →
            │   ├─ execute_graph_only → after_response → END
            │   ├─ execute_rag_only → after_response → END
            │   ├─ execute_parallel → after_response → END
            │   ├─ execute_then → after_response → END
            │   └─ execute_react → after_response → END
            └─ after_response → END
    """
    # 起始边：START → 分析路由节点
    builder.add_edge(START, "analyze_and_route_query")
    
    # 条件边：分析路由 → 通用回复 或 Guardrails
    builder.add_conditional_edges(
        "analyze_and_route_query",
        route_query,
        _ROUTER_EDGE_MAP,
    )
    
    # 固定边：通用回复 → 响应后处理
    builder.add_edge("respond_to_general_query", "after_response")
    
    # 条件边：Guardrails → 检索计划 或 结束
    builder.add_conditional_edges(
        "guardrails_node",
        guardrails_edge,
        _GUARDRAILS_EDGE_MAP,
    )
    
    # 条件边：检索计划 → 各种执行策略
    builder.add_conditional_edges(
        "retrieval_plan_route",
        retrieval_plan_edge,
        _RETRIEVAL_PLAN_EDGE_MAP,
    )
    
    # 固定边：所有执行节点 → 响应后处理
    for node_name in _EXECUTION_NODE_NAMES:
        builder.add_edge(node_name, "after_response")
    
    # 结束边：响应后处理 → END
    builder.add_edge("after_response", END)


def _build_graph():
    """构造并编译 LangGraph 主图。
    
    使用 AgentState 作为运行时状态，InputState 作为输入模式。
    编译后的图可以同步或异步执行。
    """
    builder = StateGraph(AgentState, input_schema=InputState)
    _register_nodes(builder)
    _register_edges(builder)
    return builder.compile()


# 编译后的主图实例，供外部使用
graph = _build_graph()

__all__ = ["graph"]
