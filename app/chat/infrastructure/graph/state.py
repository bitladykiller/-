"""Agent 状态定义。

职责：
- 定义主图运行时会读写的路由类型、计划类型和状态结构
- 给节点层提供稳定的输入/输出字段约束

状态流转：
- `InputState` 只包含 `messages`
- Router 节点写入 `router`
- Guardrails 节点写入 `next_action`
- RetrievalPlan 节点写入 `retrieval_plan`（能力标签 + 解析后的执行路径）
- 执行节点主要通过 `messages` 返回回答
- `memory_state` 用于缓存单次请求内已加载的记忆上下文
"""

from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from langchain_core.messages import AnyMessage
from langgraph.graph import add_messages
from typing_extensions import TypedDict

RouterType = Literal["general", "rag_doc-query"]
# 执行层仍对应五类 execute 节点；由能力标签解析得到，不再由 LLM 直接五选一
ExecutionPlanType = Literal[
    "GRAPH_ONLY",
    "RAG_ONLY",
    "PARALLEL",
    "GRAPH_THEN_RAG",
    "AGENT_REACT",
]
# 兼容旧名：历史代码/文档中的 RetrievalPlanType 即执行路径
RetrievalPlanType = ExecutionPlanType
RetrievalMode = Literal["single", "parallel", "sequential"]
RetrievalComplexity = Literal["simple", "multi_hop"]
GuardrailsAction = Literal["continue", "end"]
ReactJudgeDecision = Literal["sufficient", "retry", "handoff"]


class Router(TypedDict):
    """顶层路由输出。"""

    logic: str
    type: RouterType


class RetrievalPlan(TypedDict):
    """检索计划：能力标签 + 编排方式 + 解析后的执行路径。

    LLM 输出 need_graph / need_rag / mode / complexity；
    节点内计算 resolved_plan 供边路由与日志使用。
    """

    logic: str
    need_graph: bool
    need_rag: bool
    mode: RetrievalMode
    complexity: RetrievalComplexity
    resolved_plan: ExecutionPlanType


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


__all__ = [
    "AgentState",
    "ExecutionPlanType",
    "GuardrailsAction",
    "InputState",
    "ReactJudgeDecision",
    "RetrievalComplexity",
    "RetrievalMode",
    "RetrievalPlan",
    "RetrievalPlanType",
    "Router",
]
