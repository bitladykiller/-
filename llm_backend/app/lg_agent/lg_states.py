"""Agent 状态定义。

v3.16: 清理幽灵字段（steps/question/answer/hallucination 未在任何节点中读写），
补上实际运行中使用的 next_action 字段。

状态流转说明：
- InputState(只有 messages)
- Router 节点写入 router
- Guardrails 节点写入 next_action（"continue" / "end"）
- RetrievalPlan 节点写入 retrieval_plan
- 5 个执行节点通过 messages 返回回答
- after_response 节点写入记忆（不修改 state）
"""
from dataclasses import dataclass, field
from typing import Annotated, Literal, TypedDict, List
from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages


class Router(TypedDict):
    """顶层路由分类 — 2 路：general（闲聊） / rag_doc-query（需要检索）。
    温度 0.1，强制结构化输出。
    """
    logic: str
    type: Literal["general", "rag_doc-query"]


class RetrievalPlan(TypedDict):
    """检索计划路由 — 5 路：GRAPH_ONLY / RAG_ONLY / PARALLEL / GRAPH_THEN_RAG / AGENT_REACT。
    温度 0.1，强制结构化输出。
    """
    logic: str
    plan: Literal["GRAPH_ONLY", "RAG_ONLY", "PARALLEL", "GRAPH_THEN_RAG", "AGENT_REACT"]


@dataclass(kw_only=True)
class InputState:
    """Agent 输入状态 — 仅含 messages 字段。"""
    messages: Annotated[list[AnyMessage], add_messages]


@dataclass(kw_only=True)
class AgentState(InputState):
    """Agent 完整状态 — 每个字段对应一个节点的输出。

    v3.16 清理：移除了从未被任何节点读写的字段（steps/question/answer/hallucination）。
    """
    # Router 输出
    router: Router = field(default_factory=lambda: Router(type="general", logic=""))
    # Guardrails 输出 — "continue" 继续检索 / "end" 直接回复
    next_action: str = ""
    # RetrievalPlan 输出
    retrieval_plan: RetrievalPlan | None = None
