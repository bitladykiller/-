"""Agent 状态定义。

职责：
- 定义主图运行时会读写的路由类型、计划类型和状态结构
- 给节点层提供稳定的输入/输出字段约束

状态流转：
- `InputState` 只包含 `messages`
- 路由决策节点写入 `routing_decision`（路由类型、能力标签和执行路径）
- Guardrails 节点写入 `next_action`
- 执行节点主要通过 `messages` 返回回答
- `memory_state` 用于缓存单次请求内已加载的记忆上下文
"""

from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from typing_extensions import TypedDict

RoutingKind = Literal["general", "rag_doc-query"]
# 执行层仍对应五类 execute 节点；由能力标签解析得到，不再由 LLM 直接五选一
ExecutionPlanType = Literal[
    "GRAPH_ONLY",
    "RAG_ONLY",
    "PARALLEL",
    "GRAPH_THEN_RAG",
    "AGENT_REACT",
]
RetrievalMode = Literal["single", "parallel", "sequential"]
RetrievalComplexity = Literal["simple", "multi_hop"]
GuardrailsAction = Literal["continue", "end"]
ReactJudgeDecision = Literal["sufficient", "retry", "handoff"]


class RoutingDecision(TypedDict):
    """单节点路由决策：路由类型、能力标签和代码解析出的执行路径。"""

    logic: str
    type: RoutingKind
    need_graph: bool
    need_rag: bool
    mode: RetrievalMode
    complexity: RetrievalComplexity
    resolved_plan: ExecutionPlanType | None


@dataclass(kw_only=True)
class InputState:
    """Agent 输入状态。"""

    messages: Annotated[list[AnyMessage], add_messages]


@dataclass(kw_only=True)
class AgentState(InputState):
    """Agent 完整状态。"""

    routing_decision: RoutingDecision = field(
        default_factory=lambda: RoutingDecision(
            type="general",
            logic="",
            need_graph=False,
            need_rag=False,
            mode="single",
            complexity="simple",
            resolved_plan=None,
        )
    )
    next_action: GuardrailsAction | Literal[""] = ""
    memory_state: Any | None = field(default=None)


__all__ = [
    "AgentState",
    "ExecutionPlanType",
    "GuardrailsAction",
    "InputState",
    "ReactJudgeDecision",
    "RetrievalComplexity",
    "RetrievalMode",
    "RoutingDecision",
    "RoutingKind",
]
