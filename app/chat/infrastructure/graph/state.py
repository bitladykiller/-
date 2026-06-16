"""Agent 状态定义。

职责：
- 定义主图运行时会读写的路由类型、计划类型和状态结构
- 给节点层提供稳定的输入/输出字段约束

状态流转：
- `InputState` 只包含 `messages`
- Router 节点写入 `router`
- Guardrails 节点写入 `next_action`
- RetrievalPlan 节点写入 `retrieval_plan`
- 执行节点主要通过 `messages` 返回回答
- `memory_state` 用于缓存单次请求内已加载的记忆上下文
"""

from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages

RetrievalPlanType = Literal[
    "GRAPH_ONLY",
    "RAG_ONLY",
    "PARALLEL",
    "GRAPH_THEN_RAG",
    "AGENT_REACT",
]
GuardrailsAction = Literal["continue", "end"]
ReactJudgeDecision = Literal["sufficient", "retry"]


class Router(TypedDict):
    """顶层路由输出。"""

    logic: str
    type: Literal["general", "rag_doc-query"]


class RetrievalPlan(TypedDict):
    """检索计划路由输出。"""

    plan: RetrievalPlanType


@dataclass(kw_only=True)
class InputState:
    """Agent 输入状态。"""

    messages: Annotated[list[AnyMessage], add_messages]


@dataclass(kw_only=True)
class AgentState(InputState):
    """Agent 完整状态。"""

    router: Router = field(default_factory=lambda: Router(type="general", logic=""))
    next_action: GuardrailsAction | Literal[""] = ""
    retrieval_plan: RetrievalPlan | None = None
    memory_state: Any | None = field(default=None)
