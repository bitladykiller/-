"""
LangGraph Agent 图组装。

v3.15: 重构为纯图组装文件。节点实现 → lg_nodes.py，模型 → lg_models.py，
记忆上下文 → lg_context.py。本文件只负责 StateGraph 的节点注册和边连接。

图结构（3 层）：
  Layer 1 — 主图：Router → Guardrails → RetrievalPlan → 5 个执行器 → after_response
  Layer 2 — KG 子图：Text2Cypher（预定义模板匹配 + LLM 生成）
  Layer 3 — ReAct 子图：create_react_agent + 答案充分性检查
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.lg_agent.lg_states import AgentState, InputState
from app.lg_agent.lg_nodes import (
    analyze_and_route_query,
    route_query,
    respond_to_general_query,
    guardrails_node,
    guardrails_edge,
    retrieval_plan_route,
    retrieval_plan_edge,
    execute_graph_only,
    execute_rag_only,
    execute_parallel,
    execute_then,
    execute_react,
    after_response,
)

# ================================================================== #
# 图构建 — 10 个节点 + 条件边
# ================================================================== #

builder = StateGraph(AgentState, input=InputState)

# --- 注册节点 --- #
builder.add_node(analyze_and_route_query)
builder.add_node(respond_to_general_query)
builder.add_node("guardrails_node", guardrails_node)
builder.add_node("retrieval_plan_route", retrieval_plan_route)
builder.add_node("execute_graph_only", execute_graph_only)
builder.add_node("execute_rag_only", execute_rag_only)
builder.add_node("execute_parallel", execute_parallel)
builder.add_node("execute_then", execute_then)
builder.add_node("execute_react", execute_react)
builder.add_node("after_response", after_response)

# --- 连接边 --- #
# 入口 → 路由
builder.add_edge(START, "analyze_and_route_query")

# 路由 → 通用回复 / 守卫
builder.add_conditional_edges("analyze_and_route_query", route_query, {
    "respond_to_general_query": "respond_to_general_query",
    "retrieval_plan_router": "guardrails_node",
})

# 通用回复 → 记忆写入 → 结束
builder.add_edge("respond_to_general_query", "after_response")

# 守卫 → 检索计划 / 直接回复（范围外）
builder.add_conditional_edges("guardrails_node", guardrails_edge, {
    "retrieval_plan_route": "retrieval_plan_route",
    "after_response": "after_response",
})

# 检索计划 → 5 个执行器
builder.add_conditional_edges("retrieval_plan_route", retrieval_plan_edge, {
    "execute_graph_only": "execute_graph_only",
    "execute_rag_only": "execute_rag_only",
    "execute_parallel": "execute_parallel",
    "execute_then": "execute_then",
    "execute_react": "execute_react",
})

# 执行器 → 记忆写入 → 结束
for node_name in [
    "execute_graph_only",
    "execute_rag_only",
    "execute_parallel",
    "execute_then",
    "execute_react",
]:
    builder.add_edge(node_name, "after_response")

builder.add_edge("after_response", END)

# --- 编译 --- #
graph = builder.compile()
