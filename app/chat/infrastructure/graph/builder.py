"""LangGraph 主图组装入口。

职责：
- 只负责注册节点和连接边
- 把主图结构与节点实现、模型创建、记忆上下文分层隔离
"""

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

# 编译后的主图实例，供外部使用
_graph_builder = StateGraph(AgentState, input_schema=InputState)
_graph_builder.add_node(analyze_and_route_query)
_graph_builder.add_node(respond_to_general_query)
_graph_builder.add_node("guardrails_node", guardrails_node)
_graph_builder.add_node("retrieval_plan_route", retrieval_plan_route)
_graph_builder.add_node("execute_graph_only", execute_graph_only)
_graph_builder.add_node("execute_rag_only", execute_rag_only)
_graph_builder.add_node("execute_parallel", execute_parallel)
_graph_builder.add_node("execute_then", execute_then)
_graph_builder.add_node("execute_react", execute_react)
_graph_builder.add_node("after_response", after_response)

# 起始边：START → 分析路由节点
_graph_builder.add_edge(START, "analyze_and_route_query")

# 条件边：分析路由 → 通用回复 或 Guardrails
_graph_builder.add_conditional_edges(
    "analyze_and_route_query",
    route_query,
    {
        "respond_to_general_query": "respond_to_general_query",
        "retrieval_plan_router": "guardrails_node",
    },
)

# 固定边：通用回复 → 响应后处理
_graph_builder.add_edge("respond_to_general_query", "after_response")

# 条件边：Guardrails → 检索计划 或 结束
_graph_builder.add_conditional_edges(
    "guardrails_node",
    guardrails_edge,
    {
        "retrieval_plan_route": "retrieval_plan_route",
        "after_response": "after_response",
    },
)

# 条件边：检索计划 → 各种执行策略
_graph_builder.add_conditional_edges(
    "retrieval_plan_route",
    retrieval_plan_edge,
    {
        "execute_graph_only": "execute_graph_only",
        "execute_rag_only": "execute_rag_only",
        "execute_parallel": "execute_parallel",
        "execute_then": "execute_then",
        "execute_react": "execute_react",
    },
)

# 固定边：所有执行节点 → 响应后处理
for node_name in (
    "execute_graph_only",
    "execute_rag_only",
    "execute_parallel",
    "execute_then",
    "execute_react",
):
    _graph_builder.add_edge(node_name, "after_response")

# 结束边：响应后处理 → END
_graph_builder.add_edge("after_response", END)

graph = _graph_builder.compile()
